use std::collections::{BTreeMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PySet, PyString, PyTuple};

use crate::group_membership::group_members;

fn id(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

#[pyfunction]
pub(crate) fn assemble_group_membership<'py>(
    py: Python<'py>,
    groups: &Bound<'py, PyList>,
    rows: &Bound<'py, PyList>,
    members_model: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(groups) = groups
        .iter()
        .map(|value| id(&value))
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(None);
    };
    let mut parsed = Vec::with_capacity(rows.len());
    for row in rows.iter() {
        let Ok(row) = row.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        if row.len() != 3 {
            return Ok(None);
        }
        let kind = row.get_item(0)?;
        let (Ok(kind), Some(group), Some(member)) = (
            kind.cast_exact::<PyString>(),
            id(&row.get_item(1)?),
            id(&row.get_item(2)?),
        ) else {
            return Ok(None);
        };
        parsed.push((kind.eq("user"), group, member));
    }
    let Some(grouped) = group_members(&groups, parsed) else {
        return Ok(None);
    };
    let result = PyDict::new(py);
    for group in groups {
        let members = &grouped[&group];
        let args = PyDict::new(py);
        args.set_item("direct_members", &members.users)?;
        args.set_item("direct_subgroups", &members.subgroups)?;
        result.set_item(group, members_model.call((), Some(&args))?)?;
    }
    Ok(Some(result))
}

fn membership_data<'py>(
    py: Python<'py>,
    by_id: &Bound<'py, PyDict>,
    group_id: i64,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(members) = by_id.get_item(group_id)? else {
        return Ok(None);
    };
    let entry = PyDict::new(py);
    entry.set_item("direct_members", members.getattr("direct_members")?)?;
    entry.set_item("direct_subgroups", members.getattr("direct_subgroups")?)?;
    Ok(Some(entry))
}

fn sorted_ids(value: &Bound<'_, PyAny>) -> Option<Vec<i64>> {
    let mut ids = value
        .cast_exact::<PyList>()
        .ok()?
        .iter()
        .map(|value| id(&value))
        .collect::<Option<Vec<_>>>()?;
    ids.sort_unstable();
    Some(ids)
}

const PERMISSION_FIELDS: [&str; 6] = [
    "can_add_members_group",
    "can_join_group",
    "can_leave_group",
    "can_manage_group",
    "can_mention_group",
    "can_remove_members_group",
];

type RealmGroups<'py> = (Bound<'py, PyList>, Bound<'py, PyDict>, Bound<'py, PyDict>);

#[pyfunction]
pub(crate) fn assemble_realm_group_payload<'py>(
    py: Python<'py>,
    selected: &Bound<'py, PyList>,
    named_ids: &Bound<'py, PySet>,
    by_id: &Bound<'py, PyDict>,
    anonymous_ids: &Bound<'py, PySet>,
    timestamp: &Bound<'py, PyAny>,
) -> PyResult<Option<RealmGroups<'py>>> {
    let mut groups = BTreeMap::new();
    let system = PyDict::new(py);
    for group in selected.iter() {
        let group_id = group.getattr("id")?;
        let Some(group_id) = id(&group_id) else {
            return Ok(None);
        };
        let Some(members) = by_id.get_item(group_id)? else {
            return Ok(None);
        };
        let (Some(users), Some(subgroups)) = (
            sorted_ids(&members.getattr("direct_members")?),
            sorted_ids(&members.getattr("direct_subgroups")?),
        ) else {
            return Ok(None);
        };
        let date = group.getattr("date_created")?;
        let date = if date.is_none() {
            date
        } else {
            timestamp.call1((date,))?
        };
        let system_group = group.getattr("is_system_group")?;
        let Ok(system_group) = system_group.cast_into_exact::<PyBool>() else {
            return Ok(None);
        };
        let name = group.getattr("name")?;
        let output = PyDict::new(py);
        output.set_item("id", group_id)?;
        output.set_item("name", &name)?;
        output.set_item("creator_id", group.getattr("creator_id")?)?;
        output.set_item("date_created", date)?;
        output.set_item("description", group.getattr("description")?)?;
        output.set_item("members", users)?;
        output.set_item("direct_subgroup_ids", subgroups)?;
        output.set_item("is_system_group", &system_group)?;
        for field in PERMISSION_FIELDS {
            let setting = group.getattr(format!("{field}_id"))?;
            let Some(setting_id) = id(&setting) else {
                return Ok(None);
            };
            if named_ids.contains(setting_id)? {
                output.set_item(field, setting)?;
            } else {
                let Some(data) = membership_data(py, by_id, setting_id)? else {
                    return Ok(None);
                };
                output.set_item(field, data)?;
            }
        }
        output.set_item("deactivated", group.getattr("deactivated")?)?;
        if system_group.extract::<bool>()? {
            system.set_item(group_id, name)?;
        }
        groups.insert(group_id, output);
    }
    let anonymous = PyDict::new(py);
    for group_id in anonymous_ids.iter() {
        let Some(group_id) = id(&group_id) else {
            return Ok(None);
        };
        if !named_ids.contains(group_id)? {
            let Some(data) = membership_data(py, by_id, group_id)? else {
                return Ok(None);
            };
            anonymous.set_item(group_id, data)?;
        }
    }
    Ok(Some((
        PyList::new(py, groups.into_values())?,
        system,
        anonymous,
    )))
}

/// Replay an already-selected register card's member/subgroup ID append.
#[pyfunction]
pub(crate) fn append_and_sort_register_group_ids<'py>(
    py: Python<'py>,
    selected: &Bound<'py, PyAny>,
    incoming: &Bound<'py, PyAny>,
) -> PyResult<bool> {
    let (Ok(selected), Ok(incoming)) = (
        selected.cast_exact::<PyList>(),
        incoming.cast_exact::<PyList>(),
    ) else {
        return Ok(false);
    };
    let Some(mut merged) = selected
        .iter()
        .chain(incoming.iter())
        .map(|value| Some((id(&value)?, value)))
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(false);
    };
    // Python extend + sort preserves the list and keeps equal items stable.
    merged.sort_by_key(|(id, _)| *id);
    let replacement = PyList::new(py, merged.into_iter().map(|(_, value)| value))?;
    selected.set_slice(0, selected.len(), replacement.as_any())?;
    Ok(true)
}

/// Produce the sorted set difference without changing the old roster alias.
#[pyfunction]
pub(crate) fn subtract_register_group_ids<'py>(
    py: Python<'py>,
    selected: &Bound<'py, PyAny>,
    incoming: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let (Ok(selected), Ok(incoming)) = (
        selected.cast_exact::<PyList>(),
        incoming.cast_exact::<PyList>(),
    ) else {
        return Ok(None);
    };
    let Some(removed) = incoming
        .iter()
        .map(|value| id(&value))
        .collect::<Option<HashSet<_>>>()
    else {
        return Ok(None);
    };
    let mut remaining = BTreeMap::new();
    for value in selected.iter() {
        let Some(id) = id(&value) else {
            return Ok(None);
        };
        if !removed.contains(&id) {
            remaining.entry(id).or_insert(value);
        }
    }
    Ok(Some(PyList::new(py, remaining.into_values())?))
}

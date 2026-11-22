use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};

use crate::presence::{UtcInstant, legacy_presence};

type DatePolicy<'py> = (Bound<'py, PyAny>, Bound<'py, PyAny>);

fn utc_instant(value: &Bound<'_, PyAny>, policy: &DatePolicy<'_>) -> Option<UtcInstant> {
    let (datetime, utc) = policy;
    if !value.get_type().is(datetime) || !value.getattr("tzinfo").ok()?.is(utc) {
        return None;
    }
    let part = |name| value.getattr(name).ok()?.extract::<i64>().ok();
    UtcInstant::from_datetime_parts(
        part("year")?,
        part("month")?.try_into().ok()?,
        part("day")?,
        part("hour")?,
        part("minute")?,
        part("second")?,
        part("microsecond")?,
    )
}

struct LegacySpec<'py> {
    offset: i64,
    active: Bound<'py, PyString>,
    idle: Bound<'py, PyString>,
}

fn legacy_spec<'py>(spec: &Bound<'py, PyTuple>) -> Option<LegacySpec<'py>> {
    if spec.len() != 3 {
        return None;
    }
    let offset = spec.get_item(0).ok()?;
    let offset = offset.cast_exact::<PyInt>().ok()?.extract().ok()?;
    let active = spec
        .get_item(1)
        .ok()?
        .cast_exact::<PyString>()
        .ok()?
        .clone();
    let idle = spec
        .get_item(2)
        .ok()?
        .cast_exact::<PyString>()
        .ok()?
        .clone();
    Some(LegacySpec {
        offset,
        active,
        idle,
    })
}

fn modern<'py>(
    py: Python<'py>,
    active: UtcInstant,
    connected: UtcInstant,
) -> PyResult<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("active_timestamp", active.timestamp())?;
    result.set_item("idle_timestamp", connected.timestamp())?;
    Ok(result)
}

fn legacy<'py>(
    py: Python<'py>,
    active: UtcInstant,
    connected: UtcInstant,
    spec: &LegacySpec<'py>,
    aggregate: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(presence) = legacy_presence(active, connected, spec.offset) else {
        return Ok(None);
    };
    let status = if presence.is_active {
        &spec.active
    } else {
        &spec.idle
    };
    let prefix = PyDict::new(py);
    prefix.set_item("client", "website")?;
    prefix.set_item("status", status)?;
    prefix.set_item("timestamp", presence.timestamp)?;
    if !aggregate {
        prefix.set_item("pushable", false)?;
        return Ok(Some(prefix));
    }
    let website = prefix.copy()?;
    website.set_item("pushable", false)?;
    let result = PyDict::new(py);
    result.set_item("aggregated", prefix)?;
    result.set_item("website", website)?;
    Ok(Some(result))
}

#[pyfunction]
#[pyo3(signature = (active, connected, policy, spec, aggregate))]
pub(crate) fn format_presence_information<'py>(
    py: Python<'py>,
    active: &Bound<'py, PyAny>,
    connected: &Bound<'py, PyAny>,
    policy: DatePolicy<'py>,
    spec: Option<&Bound<'py, PyTuple>>,
    aggregate: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Some(active), Some(connected)) = (
        utc_instant(active, &policy),
        utc_instant(connected, &policy),
    ) else {
        return Ok(None);
    };
    if let Some(spec) = spec {
        let Some(spec) = legacy_spec(spec) else {
            return Ok(None);
        };
        legacy(py, active, connected, &spec, aggregate)
    } else {
        modern(py, active, connected).map(Some)
    }
}

#[pyfunction]
#[pyo3(signature = (rows, policy, spec))]
pub(crate) fn format_presence_rows<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    policy: DatePolicy<'py>,
    spec: Option<&Bound<'py, PyTuple>>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(rows) = rows.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let spec = match spec {
        Some(spec) => {
            let Some(spec) = legacy_spec(spec) else {
                return Ok(None);
            };
            Some(spec)
        }
        None => None,
    };
    let result = PyDict::new(py);
    for row in rows.iter() {
        let Ok(row) = row.cast_exact::<PyDict>() else {
            return Ok(None);
        };
        if !row
            .keys()
            .iter()
            .all(|key| key.is_exact_instance_of::<PyString>())
        {
            return Ok(None);
        }
        let key = if spec.is_some() {
            let Some(email) = row.get_item("user_profile__email")? else {
                return Ok(None);
            };
            if !email.is_exact_instance_of::<PyString>() {
                return Ok(None);
            }
            email
        } else {
            let Some(user_id) = row.get_item("user_profile_id")? else {
                return Ok(None);
            };
            let Some(id) = user_id
                .cast_exact::<PyInt>()
                .ok()
                .and_then(|id| id.extract::<i64>().ok())
            else {
                return Ok(None);
            };
            PyString::new(py, &id.to_string()).into_any()
        };
        let (Some(active), Some(connected), Some(joined)) = (
            row.get_item("last_active_time")?,
            row.get_item("last_connected_time")?,
            row.get_item("user_profile__date_joined")?,
        ) else {
            return Ok(None);
        };
        let (Some(active), Some(connected)) = (
            utc_instant(if active.is_none() { &joined } else { &active }, &policy),
            utc_instant(
                if connected.is_none() {
                    &joined
                } else {
                    &connected
                },
                &policy,
            ),
        ) else {
            return Ok(None);
        };
        let value = if let Some(spec) = &spec {
            let Some(value) = legacy(py, active, connected, spec, true)? else {
                return Ok(None);
            };
            value
        } else {
            modern(py, active, connected)?
        };
        result.set_item(key, value)?;
    }
    Ok(Some(result))
}

fn ordinary_dict<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    let dict = value.cast_exact::<PyDict>().ok()?;
    dict.keys()
        .iter()
        .all(|key| key.is_exact_instance_of::<PyString>())
        .then_some(dict)
}

#[pyfunction]
pub(crate) fn finish_single_user_presence<'py>(
    modern: &Bound<'py, PyAny>,
    legacy: &Bound<'py, PyAny>,
    duration: &Bound<'py, PyAny>,
    offline_threshold: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let signed_int =
        |value: &Bound<'_, PyAny>| value.cast_exact::<PyInt>().ok()?.extract::<i64>().ok();
    let (Some(modern), Some(legacy), Some(duration), Some(offline_threshold)) = (
        ordinary_dict(modern),
        ordinary_dict(legacy),
        signed_int(duration),
        signed_int(offline_threshold),
    ) else {
        return Ok(None);
    };
    let Some(aggregated) = legacy.get_item("aggregated")? else {
        return Ok(None);
    };
    let Some(aggregated) = ordinary_dict(&aggregated) else {
        return Ok(None);
    };
    if aggregated
        .get_item("status")?
        .is_some_and(|status| !status.is_exact_instance_of::<PyString>())
    {
        return Ok(None);
    }
    let mut rows = Vec::with_capacity(legacy.len());
    for row in legacy.values().iter() {
        let Some(row) = ordinary_dict(&row) else {
            return Ok(None);
        };
        if row
            .get_item("client")?
            .is_some_and(|client| !client.is_exact_instance_of::<PyString>())
            || row
                .get_item("pushable")?
                .is_some_and(|pushable| !pushable.is_exact_instance_of::<PyBool>())
        {
            return Ok(None);
        }
        rows.push(row.clone());
    }

    if duration > offline_threshold {
        aggregated.set_item("status", "offline")?;
    }
    for row in rows {
        for key in ["client", "pushable"] {
            if row.contains(key)? {
                row.del_item(key)?;
            }
        }
    }
    let result = modern.copy()?;
    result.call_method1("update", (legacy,))?;
    Ok(Some(result))
}

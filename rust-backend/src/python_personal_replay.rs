//! Replay already selected compose and navigation list events in memory.

use std::collections::HashSet;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

use crate::python_schedule_replay::{fields, find_row, int_field};

enum Change<'py> {
    Noop,
    Append(Bound<'py, PyAny>),
    Extend(Vec<Bound<'py, PyAny>>),
    Remove(usize),
    Replace(usize, Bound<'py, PyAny>),
    Patch(Bound<'py, PyDict>, Bound<'py, PyDict>),
}

#[derive(Clone, Copy)]
enum ComposeList {
    Snippets,
    Drafts,
}

impl ComposeList {
    fn card_key(self) -> &'static str {
        match self {
            Self::Snippets => "saved_snippet",
            Self::Drafts => "draft",
        }
    }

    fn remove_key(self) -> &'static str {
        match self {
            Self::Snippets => "saved_snippet_id",
            Self::Drafts => "draft_id",
        }
    }
}

fn card<'py>(event: &Bound<'py, PyDict>, key: &str) -> Option<Bound<'py, PyAny>> {
    let value = event.get_item(key).ok()??;
    fields(&value)?;
    Some(value)
}

fn compose_change<'py>(
    rows: &Bound<'py, PyList>,
    event: &Bound<'py, PyDict>,
    kind: ComposeList,
    op: &str,
) -> Option<Change<'py>> {
    match op {
        "add" if matches!(kind, ComposeList::Drafts) => {
            let existing = rows
                .iter()
                .map(|row| int_field(&row, "id"))
                .collect::<Option<HashSet<_>>>()?;
            let incoming = event.get_item("drafts").ok()??;
            let incoming = incoming.cast_exact::<PyList>().ok()?;
            let mut additions = Vec::new();
            for draft in incoming.iter() {
                let id = int_field(&draft, "id")?;
                // The reference only deduplicates against the existing list,
                // not against earlier items in this same add event.
                if !existing.contains(&id) {
                    additions.push(draft);
                }
            }
            Some(Change::Extend(additions))
        }
        "add" => Some(Change::Append(card(event, kind.card_key())?)),
        "remove" => {
            let id = int_field(event.as_any(), kind.remove_key())?;
            Some(find_row(rows, "id", id, false)?.map_or(Change::Noop, Change::Remove))
        }
        "update" => {
            let replacement = card(event, kind.card_key())?;
            let id = int_field(&replacement, "id")?;
            Some(
                find_row(rows, "id", id, false)?
                    .map_or(Change::Noop, |index| Change::Replace(index, replacement)),
            )
        }
        _ => None,
    }
}

fn navigation_change<'py>(
    rows: &Bound<'py, PyList>,
    event: &Bound<'py, PyDict>,
    op: &str,
) -> Option<Change<'py>> {
    if op == "add" {
        return Some(Change::Append(card(event, "navigation_view")?));
    }
    if op != "remove" && op != "update" {
        return None;
    }
    let target = event.get_item("fragment").ok()??;
    let target = target.cast_exact::<PyString>().ok()?.to_str().ok()?;
    for (index, row) in rows.iter().enumerate() {
        let row = fields(&row)?;
        let fragment = row.get_item("fragment").ok()??;
        let fragment = fragment.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if fragment != target {
            continue;
        }
        if op == "remove" {
            return Some(Change::Remove(index));
        }
        let patch = event.get_item("data").ok()??;
        return Some(Change::Patch(row.clone(), fields(&patch)?.clone()));
    }
    Some(Change::Noop)
}

#[pyfunction]
pub(crate) fn apply_personal_list_event(
    py: Python<'_>,
    selected: &Bound<'_, PyAny>,
    event: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let (Ok(rows), Some(event)) = (selected.cast_exact::<PyList>(), fields(event)) else {
        return Ok(false);
    };
    let (Some(kind), Some(op)) = (event.get_item("type")?, event.get_item("op")?) else {
        return Ok(false);
    };
    let (Ok(kind), Ok(op)) = (kind.cast_exact::<PyString>(), op.cast_exact::<PyString>()) else {
        return Ok(false);
    };
    let (Ok(kind), Ok(op)) = (kind.to_str(), op.to_str()) else {
        return Ok(false);
    };
    let change = match kind {
        "saved_snippets" => compose_change(rows, event, ComposeList::Snippets, op),
        "drafts" => compose_change(rows, event, ComposeList::Drafts, op),
        "navigation_view" => navigation_change(rows, event, op),
        _ => None,
    };
    let Some(change) = change else {
        return Ok(false);
    };

    // Validate before making any updates, so a decline leaves the state
    // untouched for the original Python reducer.
    match change {
        Change::Noop => {}
        Change::Append(card) => rows.append(card)?,
        Change::Extend(cards) if !cards.is_empty() => {
            let index = rows.len();
            rows.set_slice(index, index, PyList::new(py, cards)?.as_any())?;
        }
        Change::Extend(_) => {}
        Change::Remove(index) => rows.del_item(index)?,
        Change::Replace(index, card) => rows.set_item(index, card)?,
        Change::Patch(view, patch) => view.update(patch.as_mapping())?,
    }
    Ok(true)
}

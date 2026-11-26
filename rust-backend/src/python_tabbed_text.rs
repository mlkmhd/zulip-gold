//! Fixed text transformations over Python-selected documentation tab content.

use crate::python_text_bytes::{
    join_exact_python_strings, python_string_bytes, python_string_from_bytes, trim_python_text,
};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

#[pyfunction]
pub(crate) fn normalize_prepared_tabbed_lines<'py>(
    lines: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let joined = join_exact_python_strings(lines, b"\n")?;
    let text = python_string_bytes(&joined)?;
    let trimmed = trim_python_text(&text);
    if trimmed.len() == text.len() {
        return Some(joined);
    }
    python_string_from_bytes(joined.py(), trimmed)
}

#[pyfunction]
pub(crate) fn join_prepared_tabbed_blocks<'py>(
    tabs: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_exact_python_strings(tabs, b"\n")
}

#[pyfunction]
pub(crate) fn parse_prepared_tab_markers<'py>(
    lines: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyDict>> {
    let lines = lines.cast_exact::<PyList>().ok()?;
    let py = lines.py();
    let result = PyDict::new(py);
    let mut tabs: Option<Bound<'_, PyList>> = None;
    for (index, line) in lines.iter().enumerate() {
        let text = python_string_bytes(line.cast_exact::<PyString>().ok()?)?;
        // Python regex $ matches at the end or before a single final LF.
        let marker = text.strip_suffix(b"\n").unwrap_or(&text);
        match marker {
            b"{start_tabs}" => result.set_item("start_tabs_index", index).ok()?,
            b"{end_tabs}" => {
                result.set_item("end_tabs_index", index).ok()?;
                break;
            }
            _ => {
                let Some(key) = marker
                    .strip_prefix(b"{tab|")
                    .and_then(|rest| rest.strip_suffix(b"}"))
                    .filter(|key| !key.is_empty() && !key.contains(&b'}'))
                else {
                    continue;
                };
                if tabs.is_none() {
                    let list = PyList::empty(py);
                    result.set_item("tabs", &list).ok()?;
                    tabs = Some(list);
                }
                let tab = PyDict::new(py);
                tab.set_item("start", index).ok()?;
                tab.set_item("tab_key", python_string_from_bytes(py, key)?)
                    .ok()?;
                tabs.as_ref()?.append(tab).ok()?;
            }
        }
    }
    Some(result)
}

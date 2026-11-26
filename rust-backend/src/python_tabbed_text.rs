//! Fixed text transformations over Python-selected documentation tab content.

use crate::python_text_bytes::{
    join_exact_python_strings, python_string_bytes, python_string_from_bytes, trim_python_text,
};
use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PySlice, PyString};

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

#[pyfunction]
pub(crate) fn build_prepared_tab_nav<'py>(
    section: &Bound<'py, PyAny>,
    labels: &Bound<'py, PyAny>,
    list_item_template: &Bound<'py, PyAny>,
    nav_template: &Bound<'py, PyAny>,
    unknown_label: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = section.py();
    let items = PyList::empty(py);
    for (index, row) in section.get_item("tabs")?.try_iter()?.enumerate() {
        let key = row?.call_method1("get", ("tab_key",))?;
        let label = labels.call_method1("get", (&key,))?;
        if label.is_none() {
            return unknown_label.call1((key,));
        }

        let kw = PyDict::new(py);
        kw.set_item("class_", if index == 0 { "active" } else { "" })?;
        kw.set_item("data_tab_key", key)?;
        kw.set_item("label", label)?;
        items.append(list_item_template.call_method("format", (), Some(&kw))?)?;
    }
    let joined = PyString::new(py, "\n").call_method1("join", (items,))?;
    let kw = PyDict::new(py);
    kw.set_item("tabs", joined)?;
    nav_template.call_method("format", (), Some(&kw))
}

#[pyfunction]
pub(crate) fn build_prepared_tab_content<'py>(
    section: &Bound<'py, PyAny>,
    lines: &Bound<'py, PyAny>,
    template: &Bound<'py, PyAny>,
    labels: &Bound<'py, PyAny>,
    normalize: Option<&Bound<'py, PyAny>>,
    join: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = lines.py();
    let lf = PyString::new(py, "\n");
    let wrap_content = PyString::new(py, "\n{}\n");
    let blocks = PyList::empty(py);
    for (index, tab) in section.get_item("tabs")?.try_iter()?.enumerate() {
        let tab = tab?;
        let start = tab.get_item("start")?.add(1)?;
        let end = match section
            .get_item("tabs")
            .and_then(|tabs| tabs.get_item(index + 1))
            .and_then(|next| next.get_item("start"))
        {
            Ok(end) => end,
            Err(error) if error.is_instance_of::<PyIndexError>(py) => {
                section.get_item("end_tabs_index")?
            }
            Err(error) => return Err(error),
        };
        let selected = lines.get_item(py.get_type::<PySlice>().call1((start, end))?)?;
        let mut content = match normalize {
            Some(normalize) => normalize.call1((&selected,))?,
            None => py.None().into_bound(py),
        };
        if content.is_none() {
            content = lf
                .call_method1("join", (selected,))?
                .call_method0("strip")?;
        }

        let render = template.getattr("format")?;
        let args = PyDict::new(py);
        args.set_item("class_", if index == 0 { "active" } else { "" })?;
        args.set_item("data_tab_key", tab.get_item("tab_key")?)?;
        args.set_item("tab_label", labels.get_item(tab.get_item("tab_key")?)?)?;
        args.set_item("content", wrap_content.call_method1("format", (content,))?)?;
        blocks.append(render.call((), Some(&args))?)?;
    }
    if let Some(join) = join {
        let joined = join.call1((&blocks,))?;
        if !joined.is_none() {
            return Ok(joined);
        }
    }
    lf.call_method1("join", (blocks,))
}

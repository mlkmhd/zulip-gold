//! Display transformations over already selected commit scalars.

use std::collections::HashMap;

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString};

type Row<'py> = (Bound<'py, PyString>, Bound<'py, PyString>);

#[pyfunction]
pub(crate) fn project_commit_display_row<'py>(
    py: Python<'py>,
    sha: &Bound<'py, PyAny>,
    message: &Bound<'py, PyAny>,
) -> Option<Row<'py>> {
    let sha = sha.cast_exact::<PyString>().ok()?;
    let message = message.cast_exact::<PyString>().ok()?;
    let sha_text = python_string_bytes(sha)?;
    let message_text = python_string_bytes(message)?;
    // The first byte of each encoded code point is not a continuation byte.
    let short = match sha_text
        .iter()
        .enumerate()
        .filter(|(_, byte)| **byte & 0xc0 != 0x80)
        .nth(11)
    {
        Some((index, _)) => python_string_from_bytes(py, &sha_text[..index])?,
        None => sha.clone(),
    };
    let subject = match message_text.iter().position(|&byte| byte == b'\n') {
        Some(index) => python_string_from_bytes(py, &message_text[..index])?,
        None => message.clone(),
    };
    Some((short, subject))
}

type Ranking<'py> = Vec<(Bound<'py, PyString>, usize)>;

#[pyfunction]
pub(crate) fn rank_commit_display_names<'py>(
    py: Python<'py>,
    names: &Bound<'py, PyAny>,
    max_rows: &Bound<'_, PyAny>,
) -> Option<Ranking<'py>> {
    let names = names.cast_exact::<PyList>().ok()?;
    let limit = max_rows
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<usize>()
        .ok()?;
    let mut entries: HashMap<Vec<u8>, (Bound<'py, PyString>, usize)> = HashMap::new();
    for name in names.iter() {
        let name = name.cast_into_exact::<PyString>().ok()?;
        let key = python_string_bytes(&name)?.into_owned();
        entries.entry(key).or_insert((name, 0)).1 += 1;
    }
    let mut order = entries.into_iter().collect::<Vec<_>>();
    // Surrogate-pass UTF-8 byte order matches Python code-point order.
    // Equal display names keep the first Python string.
    order.sort_unstable_by(|(a, (_, ca)), (b, (_, cb))| cb.cmp(ca).then_with(|| a.cmp(b)));
    let mut other = 0;
    if order.len() > limit {
        other = order.drain(limit..).map(|(_, (_, count))| count).sum();
    }
    let mut result = order.into_iter().map(|(_, pair)| pair).collect::<Vec<_>>();
    if other != 0 {
        result.push((PyString::new(py, "others"), other));
    }
    Some(result)
}

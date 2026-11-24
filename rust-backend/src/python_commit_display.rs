//! Display transformations over already selected commit scalars.

use std::collections::HashMap;

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
    let sha_text = sha.to_str().ok()?;
    let message_text = message.to_str().ok()?;
    let short = match sha_text.char_indices().nth(11) {
        Some((index, _)) => PyString::new(py, &sha_text[..index]),
        None => sha.clone(),
    };
    let subject = match message_text.split_once('\n') {
        Some((subject, _)) => PyString::new(py, subject),
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
    let mut entries: HashMap<String, (Bound<'py, PyString>, usize)> = HashMap::new();
    for name in names.iter() {
        let name = name.cast_into_exact::<PyString>().ok()?;
        let key = name.to_str().ok()?;
        entries.entry(key.to_owned()).or_insert((name, 0)).1 += 1;
    }
    let mut order = entries.into_iter().collect::<Vec<_>>();
    // UTF-8 order matches Python scalar order; unsupported surrogates
    // have already declined. Equal display names share one first key.
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

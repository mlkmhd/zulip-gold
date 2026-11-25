//! Chart display ordering over already computed synthetic or application series.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

use crate::python_text_bytes::python_string_bytes;

type Labels<'py> = Vec<Bound<'py, PyString>>;

fn chart_order<'py>(rows: &Bound<'py, PyDict>) -> Option<Labels<'py>> {
    let mut totals = Vec::with_capacity(rows.len());
    for (label, values) in rows.iter() {
        let label = label.cast_into_exact::<PyString>().ok()?;
        let name = python_string_bytes(&label)?.into_owned();
        let values = values.cast_into_exact::<PyList>().ok()?;
        let total = values.iter().try_fold(0i64, |acc, value| {
            acc.checked_add(value.cast_exact::<PyInt>().ok()?.extract().ok()?)
        })?;
        totals.push((total, name, label));
    }
    // Surrogate-pass UTF-8 sorts in Python code-point order, even for lone
    // surrogates. Labels in an exact dictionary are unique.
    totals.sort_unstable_by(|a, b| (b.0, &b.1).cmp(&(a.0, &a.1)));
    Some(totals.into_iter().map(|(_, _, label)| label).collect())
}

#[pyfunction]
pub(crate) fn order_analytics_chart_totals<'py>(rows: &Bound<'py, PyAny>) -> Option<Labels<'py>> {
    chart_order(rows.cast_exact::<PyDict>().ok()?)
}

#[pyfunction]
pub(crate) fn order_analytics_chart_clients<'py>(
    everyone: &Bound<'py, PyAny>,
    user: &Bound<'py, PyAny>,
) -> Option<Labels<'py>> {
    let everyone = everyone.cast_exact::<PyDict>().ok()?;
    let user = user.cast_exact::<PyDict>().ok()?;
    // Across this bound the original float i - 0.1 is strictly
    // between i - 1 and i, so ranking in tenths is equivalent.
    const MAX_LEN: usize = 1 << 28;
    if everyone.len().max(user.len()) > MAX_LEN {
        return None;
    }
    let everyone = chart_order(everyone)?;
    let user = chart_order(user)?;
    let mut positions = HashMap::new();
    let mut ranks = Vec::new();
    for (i, label) in everyone.into_iter().enumerate() {
        positions.insert(python_string_bytes(&label)?.into_owned(), i);
        ranks.push(((i as i64) * 10, label));
    }
    for (i, label) in user.into_iter().enumerate() {
        let rank = (i as i64) * 10 - 1;
        if let Some(&index) = positions.get(python_string_bytes(&label)?.as_ref()) {
            // Keep the first dict key object, as Python does on replacement.
            ranks[index].0 = ranks[index].0.min(rank);
        } else {
            ranks.push((rank, label));
        }
    }
    ranks.sort_by_key(|(rank, _)| *rank);
    Some(ranks.into_iter().map(|(_, label)| label).collect())
}

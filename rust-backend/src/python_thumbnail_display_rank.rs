//! Rank already prepared thumbnail display grades without inspecting requests.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyFloat, PyInt, PyList, PyTuple};

type Grade = (bool, bool, f64, i64, i64);

fn extract_grade(row: &Bound<'_, PyAny>) -> Option<Grade> {
    let row = row.cast_exact::<PyTuple>().ok()?;
    if row.len() != 5 {
        return None;
    }
    let quality = row.get_item(2).ok()?.cast_exact::<PyFloat>().ok()?.value();
    if !quality.is_finite() {
        return None;
    }
    Some((
        row.get_item(0).ok()?.cast_exact::<PyBool>().ok()?.is_true(),
        row.get_item(1).ok()?.cast_exact::<PyBool>().ok()?.is_true(),
        quality,
        row.get_item(3)
            .ok()?
            .cast_exact::<PyInt>()
            .ok()?
            .extract()
            .ok()?,
        row.get_item(4)
            .ok()?
            .cast_exact::<PyInt>()
            .ok()?
            .extract()
            .ok()?,
    ))
}

#[pyfunction]
pub(crate) fn rank_thumbnail_display(grades: &Bound<'_, PyAny>) -> Option<usize> {
    let grades = grades.cast_exact::<PyList>().ok()?;
    let mut best: Option<(usize, Grade)> = None;
    for (index, row) in grades.iter().enumerate() {
        let grade = extract_grade(&row)?;
        // Only strictly better grades replace the first candidate.
        // Floats are finite; ordinary comparison also keeps the two zeroes tied.
        if best.is_none_or(|(_, previous)| grade < previous) {
            best = Some((index, grade));
        }
    }
    best.map(|(index, _)| index)
}

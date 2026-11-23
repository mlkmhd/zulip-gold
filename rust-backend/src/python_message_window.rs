//! Binary-search the window in a Django-selected, visible, sorted page.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyTuple};

fn boundary(rows: &Bound<'_, PyList>, anchor: i64, include_equal: bool) -> Option<usize> {
    let mut left = 0;
    let mut right = rows.len();
    while left < right {
        let middle = left + (right - left) / 2;
        let row = rows.get_item(middle).ok()?;
        let row = row.cast_exact::<PyTuple>().ok()?;
        let id = row.get_item(0).ok()?;
        let id = id.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
        if id < anchor || (include_equal && id == anchor) {
            left = middle + 1;
        } else {
            right = middle;
        }
    }
    Some(left)
}

/// The caller sorts and applies its visibility cutoff before calling.
/// This returns only positions; it never reads any other tuple field.
#[pyfunction]
pub(crate) fn plan_sorted_message_window(
    rows: &Bound<'_, PyAny>,
    anchor: &Bound<'_, PyAny>,
    before: usize,
    after: usize,
    anchored_to_right: bool,
) -> Option<(usize, usize, usize, usize)> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    let anchor = anchor.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let length = rows.len();
    let (anchor_start, after_start) = if anchored_to_right {
        (length, length)
    } else {
        (
            boundary(rows, anchor, false)?,
            boundary(rows, anchor, true)?,
        )
    };
    let start = if before == 0 {
        0
    } else {
        anchor_start.saturating_sub(before)
    };
    let end = if after == 0 {
        length
    } else {
        after_start.saturating_add(after).min(length)
    };
    Some((start, anchor_start, after_start, end))
}

//! Bucket arithmetic for preselected UTC chart boundaries.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PySlice, PyTuple};

const HOUR_US: i64 = 3_600_000_000;
const DAY_US: i64 = 24 * HOUR_US;
const MAX_DATETIME_US: i64 = 3_652_059 * DAY_US - 1;
const MAX_BUCKETS: i64 = 65_536;

fn integer(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

#[pyfunction]
pub(crate) fn plan_analytics_time_buckets(
    start: &Bound<'_, PyAny>,
    end: &Bound<'_, PyAny>,
    step: &Bound<'_, PyAny>,
    minimum_length: &Bound<'_, PyAny>,
) -> Option<(i64, i64, i64)> {
    let span = 0..=MAX_DATETIME_US;
    let start = integer(start)?;
    let end = integer(end)?;
    let step = integer(step)?;
    if !span.contains(&start) || !span.contains(&end) || !matches!(step, HOUR_US | DAY_US) {
        return None;
    }
    let end = end - end % step;
    let start = if minimum_length.is_none() {
        start
    } else {
        let adjustment = integer(minimum_length)?.checked_sub(1)?.checked_mul(step)?;
        let padded = end.checked_sub(adjustment)?;
        // Python evaluates this expression before choosing the smaller start.
        if !span.contains(&padded) {
            return None;
        }
        start.min(padded)
    };
    if end < start {
        return Some((0, 0, step));
    }

    let count = (end - start) / step + 1;
    if count > MAX_BUCKETS {
        return None;
    }
    let first = end - (count - 1) * step;
    // The original while loop subtracts one step even after the final
    // included element, raising when the subtraction precedes year 1.
    if first < step {
        return None;
    }
    Some((first, count, step))
}

#[pyfunction]
pub(crate) fn walk_prepared_analytics_time_grid<'py>(
    start: &Bound<'py, PyAny>,
    mut end: Bound<'py, PyAny>,
    step: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let py = start.py();
    let times = PyList::empty(py);
    let subtract = py.import("operator")?.getattr("isub")?;
    while end.ge(start)? {
        times.append(&end)?;
        end = subtract.call1((end, step))?;
    }
    times.call_method0("reverse")?;
    Ok(times)
}

#[pyfunction]
pub(crate) fn make_prepared_utc_floor<'py>(
    dt: &Bound<'py, PyAny>,
    constructor: &Bound<'py, PyAny>,
    utc: &Bound<'py, PyAny>,
    day: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let py = dt.py();
    let stop = if day { 3 } else { 4 };
    let fields = dt
        .call_method0("timetuple")?
        .get_item(PySlice::new(py, 0, stop, 1))?;
    let fields = fields.cast::<PyTuple>()?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("tzinfo", utc)?;
    constructor.call(fields, Some(&kwargs))
}

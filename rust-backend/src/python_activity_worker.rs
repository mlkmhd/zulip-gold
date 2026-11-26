//! Group prepared user activity events before Django builds its existing SQL.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFloat, PyInt, PyList, PyString};

use crate::python_presence::utc_instant;

type ActivityRow<'py> = (
    Bound<'py, PyAny>,
    Bound<'py, PyAny>,
    Bound<'py, PyAny>,
    usize,
    Bound<'py, PyAny>,
);

#[pyfunction]
pub(crate) fn group_prepared_user_activity<'py>(
    events: &Bound<'py, PyAny>,
) -> Option<Vec<ActivityRow<'py>>> {
    let events = events.cast_exact::<PyList>().ok()?;
    let mut indexes = HashMap::<(i64, i64, String), usize>::new();
    let mut rows: Vec<ActivityRow<'py>> = Vec::new();
    let mut newest: Vec<f64> = Vec::new();
    for event in events.iter() {
        let event = event.cast_exact::<PyDict>().ok()?;
        if !event
            .iter()
            .all(|(key, _)| key.is_exact_instance_of::<PyString>())
        {
            return None;
        }
        let uid = event.get_item("user_profile_id").ok()??;
        let client = event.get_item("client_id").ok()??;
        let query = event.get_item("query").ok()??;
        let time = event.get_item("time").ok()??;
        let key = (
            uid.cast_exact::<PyInt>().ok()?.extract().ok()?,
            client.cast_exact::<PyInt>().ok()?.extract().ok()?,
            query
                .cast_exact::<PyString>()
                .ok()?
                .to_str()
                .ok()?
                .to_owned(),
        );
        let stamp = time.cast_exact::<PyFloat>().ok()?.extract::<f64>().ok()?;
        if !stamp.is_finite() {
            return None;
        }
        let next_index = rows.len();
        let index = *indexes.entry(key).or_insert(next_index);
        if index == next_index {
            rows.push((uid, client, query, 1, time));
            newest.push(stamp);
        } else {
            let row = rows.get_mut(index)?;
            row.3 += 1;
            // Python max keeps the first value on a tie, including signed zero.
            if stamp > newest[index] {
                newest[index] = stamp;
                row.4 = time;
            }
        }
    }
    Some(rows)
}

#[pyfunction]
pub(crate) fn plan_prepared_activity_interval<'py>(
    old_start: &Bound<'py, PyAny>,
    old_end: &Bound<'py, PyAny>,
    new_start: &Bound<'py, PyAny>,
    new_end: &Bound<'py, PyAny>,
    policy: (Bound<'py, PyAny>, Bound<'py, PyAny>),
) -> Option<(bool, Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let old_start_time = utc_instant(old_start, &policy)?;
    let old_end_time = utc_instant(old_end, &policy)?;
    let new_start_time = utc_instant(new_start, &policy)?;
    let new_end_time = utc_instant(new_end, &policy)?;
    if new_start_time > old_end_time || new_end_time < old_start_time {
        return Some((false, new_start.clone(), new_end.clone()));
    }
    // The first argument to Python min/max wins when values are equal.
    let start = if new_start_time < old_start_time {
        new_start
    } else {
        old_start
    };
    let end = if new_end_time > old_end_time {
        new_end
    } else {
        old_end
    };
    Some((true, start.clone(), end.clone()))
}

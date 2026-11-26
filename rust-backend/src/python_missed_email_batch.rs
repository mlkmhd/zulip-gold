use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyTuple};

#[pyfunction]
pub(crate) fn group_prepared_missed_email_rows(keys: &Bound<'_, PyAny>) -> Option<Vec<Vec<usize>>> {
    let keys = keys.cast_exact::<PyList>().ok()?;
    let mut recipient_indexes = HashMap::<i64, usize>::new();
    let mut message_indexes = HashMap::<(i64, i64), usize>::new();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (index, key) in keys.iter().enumerate() {
        let key = key.cast_exact::<PyTuple>().ok()?;
        if key.len() != 2 {
            return None;
        }
        let recipient = key
            .get_item(0)
            .ok()?
            .cast_exact::<PyInt>()
            .ok()?
            .extract()
            .ok()?;
        let message = key
            .get_item(1)
            .ok()?
            .cast_exact::<PyInt>()
            .ok()?
            .extract()
            .ok()?;
        let next_group = groups.len();
        let group_index = *recipient_indexes.entry(recipient).or_insert(next_group);
        if group_index == next_group {
            groups.push(Vec::new());
        }
        let group = &mut groups[group_index];
        let next_message = group.len();
        let position = *message_indexes
            .entry((recipient, message))
            .or_insert(next_message);
        if position == next_message {
            group.push(index);
        } else {
            // Python dict assignment replaces the value without moving the key.
            group[position] = index;
        }
    }
    Some(groups)
}

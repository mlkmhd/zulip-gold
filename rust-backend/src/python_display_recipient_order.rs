//! Sort indices of supplied display-only member IDs, separately per group.

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyTuple};

#[pyfunction]
pub(crate) fn plan_display_recipient_order(groups: &Bound<'_, PyAny>) -> Option<Vec<Vec<usize>>> {
    let groups = groups.cast_exact::<PyList>().ok()?;
    let mut output = Vec::with_capacity(groups.len());
    for group in groups.iter() {
        let group = group.cast_exact::<PyTuple>().ok()?;
        let mut positions = Vec::with_capacity(group.len());
        for (position, member) in group.iter().enumerate() {
            let member = member.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
            positions.push((member, position));
        }
        positions.sort_unstable_by_key(|&(member, _)| member);
        if positions.windows(2).any(|pair| pair[0].0 == pair[1].0) {
            return None;
        }
        output.push(
            positions
                .into_iter()
                .map(|(_, position)| position)
                .collect(),
        );
    }
    Some(output)
}

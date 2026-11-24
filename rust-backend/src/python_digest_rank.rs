//! Rank already eligible digest topics by scalar scores and object identity.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyTuple};

const DIVERSE_COUNT: usize = 2;
const LENGTH_COUNT: usize = 4;

#[derive(Clone, Copy)]
struct Rank {
    score: u64,
    identity: u64,
    index: usize,
}

fn insert_rank(ranks: &mut Vec<Rank>, cap: usize, row: Rank) {
    // Python nlargest keeps earlier entries ahead of equal later entries.
    let position = ranks.partition_point(|entry| entry.score >= row.score);
    if position < cap {
        ranks.insert(position, row);
        if ranks.len() > cap {
            ranks.pop();
        }
    }
}

fn scalar(row: &Bound<'_, PyTuple>, index: usize) -> Option<u64> {
    row.get_item(index)
        .ok()?
        .cast_exact::<PyInt>()
        .ok()?
        .extract::<u64>()
        .ok()
}

#[pyfunction]
pub(crate) fn rank_eligible_digest_topic_indices(rows: &Bound<'_, PyAny>) -> Option<Vec<usize>> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    let mut top_diversity = Vec::with_capacity(DIVERSE_COUNT + 1);
    let mut top_length = Vec::with_capacity(LENGTH_COUNT + 1);
    let mut scores = HashMap::with_capacity(rows.len());

    for (index, row) in rows.iter().enumerate() {
        let row = row.cast_exact::<PyTuple>().ok()?;
        if row.len() != 3 {
            return None;
        }
        let (identity, diversity, length) = (scalar(row, 0)?, scalar(row, 1)?, scalar(row, 2)?);
        if scores
            .insert(identity, (diversity, length))
            .is_some_and(|previous| previous != (diversity, length))
        {
            return None;
        }
        insert_rank(
            &mut top_diversity,
            DIVERSE_COUNT,
            Rank {
                score: diversity,
                identity,
                index,
            },
        );
        insert_rank(
            &mut top_length,
            LENGTH_COUNT,
            Rank {
                score: length,
                identity,
                index,
            },
        );
    }

    // The diversity stage retains repeated occurrences. Only the fill
    // stage skips objects already present, and cannot look past its top four.
    for candidate in top_length {
        if !top_diversity
            .iter()
            .any(|row| row.identity == candidate.identity)
        {
            top_diversity.push(candidate);
        }
        if top_diversity.len() == LENGTH_COUNT {
            break;
        }
    }
    Some(top_diversity.into_iter().map(|row| row.index).collect())
}

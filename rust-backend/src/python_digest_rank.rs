//! Rank already eligible digest topics by scalar scores and object identity.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyTuple};

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

#[pyfunction]
pub(crate) fn assemble_recent_digest_topics<'py>(
    messages: &Bound<'py, PyAny>,
    stream_id: &Bound<'py, PyAny>,
    new_topic: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let py = messages.py();
    let topic_map = pyo3::types::PyDict::new(py);
    for message in messages.try_iter()? {
        let message = message?;
        let name = message.call_method0("topic_name")?;
        let key = PyTuple::new(py, [stream_id, &name])?;
        if !topic_map.contains(&key)? {
            let topic = new_topic.call1((&key,))?;
            topic_map.set_item(&key, topic)?;
        }
        topic_map
            .as_any()
            .get_item(&key)?
            .call_method1("add_message", (message,))?;
    }
    Ok(topic_map.values())
}

#[pyfunction]
pub(crate) fn add_prepared_digest_topic_message(
    topic: &Bound<'_, PyAny>,
    message: &Bound<'_, PyAny>,
) -> PyResult<()> {
    if topic.getattr("sample_messages")?.len()? < 2 {
        topic
            .getattr("sample_messages")?
            .call_method1("append", (message,))?;
    }

    if !message.getattr("sender")?.getattr("is_bot")?.is_truthy()? {
        let add_sender = topic.getattr("human_senders")?.getattr("add")?;
        let full_name = message.getattr("sender")?.getattr("full_name")?;
        add_sender.call1((full_name,))?;
        let count = topic
            .getattr("num_human_messages")?
            .call_method1("__add__", (1,))?;
        topic.setattr("num_human_messages", count)?;
    }
    Ok(())
}

#[pyfunction]
pub(crate) fn build_prepared_digest_teaser<'py>(
    topic: &Bound<'py, PyAny>,
    user: &Bound<'py, PyAny>,
    stream_id_map: &Bound<'py, PyAny>,
    render_messages: &Bound<'py, PyAny>,
    create_teaser: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let count = topic.getattr("num_human_messages")?;
    let samples = topic.getattr("sample_messages")?;
    // Keep arbitrary-precision Python integers and compute the count before the
    // message renderer can mutate or reorder sample_messages.
    let remaining = count.call_method1("__sub__", (samples.len()?,))?;
    let message_kwargs = PyDict::new(topic.py());
    message_kwargs.set_item("user", user)?;
    message_kwargs.set_item("messages", samples)?;
    message_kwargs.set_item("stream_id_map", stream_id_map)?;
    let rendered = render_messages.call((), Some(&message_kwargs))?;

    let senders = topic.getattr("human_senders")?;
    let participants = PyList::new(
        topic.py(),
        senders.try_iter()?.collect::<PyResult<Vec<_>>>()?,
    )?;
    participants.sort()?;
    let teaser_kwargs = PyDict::new(topic.py());
    teaser_kwargs.set_item("participants", participants)?;
    teaser_kwargs.set_item("count", remaining)?;
    teaser_kwargs.set_item("first_few_messages", rendered)?;
    create_teaser.call((), Some(&teaser_kwargs))
}

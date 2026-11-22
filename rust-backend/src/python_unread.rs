use std::collections::{BTreeMap, HashSet};

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PySet, PyString};

fn string_keys(value: &Bound<'_, PyDict>) -> bool {
    value
        .iter()
        .all(|(key, _)| key.is_exact_instance_of::<PyString>())
}

fn int(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

fn field<'py>(row: &Bound<'py, PyDict>, key: &str) -> Option<Bound<'py, PyAny>> {
    row.get_item(key).ok().flatten()
}

fn rows<'py, T>(
    raw: &Bound<'py, PyDict>,
    key: &str,
    parse: impl Fn(&Bound<'py, PyDict>) -> Option<T>,
) -> Option<Vec<(i64, T)>> {
    let value = field(raw, key)?;
    let rows = value.cast_exact::<PyDict>().ok()?;
    rows.iter()
        .map(|(id, row)| {
            let row = row.cast_exact::<PyDict>().ok()?;
            if !string_keys(row) {
                return None;
            }
            Some((int(&id)?, parse(row)?))
        })
        .collect()
}

fn text<'py>(row: &Bound<'py, PyDict>, key: &str) -> Option<Bound<'py, PyString>> {
    field(row, key)?.cast_into_exact::<PyString>().ok()
}

type StreamRow<'py> = (i64, (i64, Bound<'py, PyString>));

struct Unread<'py> {
    pms: Vec<(i64, i64)>,
    streams: Vec<StreamRow<'py>>,
    huddles: Vec<(i64, Bound<'py, PyString>)>,
    mentions: Bound<'py, PySet>,
    unmuted: Bound<'py, PySet>,
}

fn read_unread<'py>(raw: &Bound<'py, PyDict>) -> Option<Unread<'py>> {
    if !string_keys(raw) {
        return None;
    }
    Some(Unread {
        pms: rows(raw, "pm_dict", |row| int(&field(row, "other_user_id")?))?,
        streams: rows(raw, "stream_dict", |row| {
            Some((int(&field(row, "stream_id")?)?, text(row, "topic")?))
        })?,
        huddles: rows(raw, "huddle_dict", |row| text(row, "user_ids_string"))?,
        mentions: field(raw, "mentions")?.cast_into_exact::<PySet>().ok()?,
        unmuted: field(raw, "unmuted_stream_msgs")?
            .cast_into_exact::<PySet>()
            .ok()?,
    })
}

fn buckets<K: Ord>(pairs: impl IntoIterator<Item = (i64, K)>) -> BTreeMap<K, Vec<i64>> {
    let mut buckets = BTreeMap::<K, Vec<i64>>::new();
    for (id, key) in pairs {
        buckets.entry(key).or_default().push(id);
    }
    for ids in buckets.values_mut() {
        ids.sort_unstable();
    }
    buckets
}

fn mentions_in_iteration_order(mentions: &Bound<'_, PySet>) -> Option<Vec<i64>> {
    // Despite the other arrays being sorted, the API built this list in
    // Python set iteration order. Preserve that order.
    mentions.iter().map(|id| int(&id)).collect()
}

fn topic_key(topic: &Bound<'_, PyString>) -> Option<String> {
    let spelling = topic.to_str().ok()?;
    if spelling.is_ascii() {
        return Some(spelling.to_ascii_lowercase());
    }
    // Python lowercasing is contextual for characters such as Greek sigma and
    // tied to the interpreter's Unicode database.
    topic.call_method0("lower").ok()?.extract().ok()
}

fn stream_buckets<'py>(
    py: Python<'py>,
    streams: Vec<StreamRow<'py>>,
    allow_empty_topic: bool,
    fallback: &Bound<'py, PyString>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let mut groups = BTreeMap::<_, (Bound<'py, PyString>, Vec<i64>)>::new();
    for (id, (stream, topic)) in streams {
        let topic = if !allow_empty_topic && topic.is_empty()? {
            fallback.clone()
        } else {
            topic
        };
        let Some(key) = topic_key(&topic) else {
            return Ok(None);
        };
        groups
            .entry((stream, key))
            .or_insert_with(|| (topic, Vec::new()))
            .1
            .push(id);
    }

    let result = PyList::empty(py);
    for ((stream, _), (topic, mut ids)) in groups {
        ids.sort_unstable();
        let row = PyDict::new(py);
        row.set_item("stream_id", stream)?;
        row.set_item("topic", topic)?;
        row.set_item("unread_message_ids", ids)?;
        result.append(row)?;
    }
    Ok(Some(result))
}

#[pyfunction]
pub(crate) fn aggregate_unread_payload<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyAny>,
    allow_empty_topic: bool,
    fallback: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Ok(raw), Ok(fallback)) = (
        raw.cast_exact::<PyDict>(),
        fallback.cast_exact::<PyString>(),
    ) else {
        return Ok(None);
    };
    let (Some(data), Some(old_unreads_missing)) =
        (read_unread(raw), field(raw, "old_unreads_missing"))
    else {
        return Ok(None);
    };
    if !old_unreads_missing.is_exact_instance_of::<PyBool>() {
        return Ok(None);
    }
    let Some(mentions) = mentions_in_iteration_order(&data.mentions) else {
        return Ok(None);
    };

    let count = data.pms.len() + data.unmuted.len() + data.huddles.len();
    let Some(streams) = stream_buckets(py, data.streams, allow_empty_topic, fallback)? else {
        return Ok(None);
    };
    let pms = PyList::empty(py);
    for (other, ids) in buckets(data.pms) {
        let row = PyDict::new(py);
        row.set_item("other_user_id", other)?;
        row.set_item("sender_id", other)?;
        row.set_item("unread_message_ids", ids)?;
        pms.append(row)?;
    }
    let Some(huddle_pairs) = data
        .huddles
        .iter()
        .map(|(id, key)| Some((*id, key.to_str().ok()?)))
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(None);
    };
    let huddles = PyList::empty(py);
    for (key, ids) in buckets(huddle_pairs) {
        let row = PyDict::new(py);
        row.set_item("user_ids_string", key)?;
        row.set_item("unread_message_ids", ids)?;
        huddles.append(row)?;
    }

    let result = PyDict::new(py);
    result.set_item("pms", pms)?;
    result.set_item("streams", streams)?;
    result.set_item("huddles", huddles)?;
    result.set_item("mentions", mentions)?;
    result.set_item("count", count)?;
    result.set_item("old_unreads_missing", old_unreads_missing)?;
    Ok(Some(result))
}

fn other_participants(ids: &str, my_user_id: i64) -> Option<Vec<i64>> {
    let mut others = Vec::new();
    for part in ids.split(',') {
        if part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        let id = part.parse().ok()?;
        if id != my_user_id {
            others.push(id);
        }
    }
    others.sort_unstable();
    Some(others)
}

#[pyfunction]
pub(crate) fn format_unread_details<'py>(
    py: Python<'py>,
    my_user_id: &Bound<'py, PyAny>,
    raw: &Bound<'py, PyAny>,
    details_model: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Some(my_user_id), Ok(raw)) = (int(my_user_id), raw.cast_exact::<PyDict>()) else {
        return Ok(None);
    };
    let Some(data) = read_unread(raw) else {
        return Ok(None);
    };
    let (Some(mentions), Some(unmuted)) = (
        data.mentions
            .iter()
            .map(|id| int(&id))
            .collect::<Option<HashSet<_>>>(),
        data.unmuted
            .iter()
            .map(|id| int(&id))
            .collect::<Option<HashSet<_>>>(),
    ) else {
        return Ok(None);
    };
    let Some(huddles) = data
        .huddles
        .iter()
        .map(|(id, users)| Some((*id, other_participants(users.to_str().ok()?, my_user_id)?)))
        .collect::<Option<Vec<_>>>()
    else {
        return Ok(None);
    };
    if data
        .streams
        .iter()
        .any(|(_, (_, topic))| topic.to_str().is_err())
    {
        return Ok(None);
    }
    let result = PyDict::new(py);
    let insert = |id: i64, kwargs: Bound<'py, PyDict>| -> PyResult<()> {
        let details = details_model.call((), Some(&kwargs))?;
        if mentions.contains(&id) {
            details.setattr("mentioned", true)?;
        }
        result.set_item(id.to_string(), details)
    };
    let private = |id, users| {
        let kwargs = PyDict::new(py);
        kwargs.set_item("type", "private")?;
        kwargs.set_item("user_ids", users)?;
        insert(id, kwargs)
    };
    for (id, other) in data.pms {
        private(
            id,
            if other == my_user_id {
                vec![]
            } else {
                vec![other]
            },
        )?;
    }
    for (id, (stream, topic)) in data.streams {
        let kwargs = PyDict::new(py);
        kwargs.set_item("type", "stream")?;
        kwargs.set_item("stream_id", stream)?;
        kwargs.set_item("topic", topic)?;
        kwargs.set_item("unmuted_stream_msg", unmuted.contains(&id))?;
        insert(id, kwargs)?;
    }
    for (id, others) in huddles {
        private(id, others)?;
    }
    Ok(Some(result))
}

//! Message payload assembly over ordinary Python dictionaries.

use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

fn ordinary_dict<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    let dict = value.cast_exact::<PyDict>().ok()?;
    dict.keys()
        .iter()
        .all(|key| key.is_exact_instance_of::<PyString>())
        .then_some(dict)
}

fn optional_plain_string(value: Option<&Bound<'_, PyAny>>) -> bool {
    value.is_none_or(|item| item.is_none() || item.is_exact_instance_of::<PyString>())
}

const TOPIC_KEYS: [&str; 2] = ["prev_topic", "topic"];

fn ordinary_history(
    history: &Bound<'_, PyList>,
    allow_empty_topic: bool,
    plain_reply_links: bool,
) -> PyResult<bool> {
    for row in history.iter() {
        let Some(row) = ordinary_dict(&row) else {
            return Ok(false);
        };
        if !allow_empty_topic {
            for key in TOPIC_KEYS {
                if row
                    .get_item(key)?
                    .is_some_and(|value| !value.is_exact_instance_of::<PyString>())
                {
                    return Ok(false);
                }
            }
        }
        if row
            .get_item("prev_rendered_content_version")?
            .is_some_and(|value| !value.is_none() && !value.is_exact_instance_of::<PyInt>())
        {
            return Ok(false);
        }
        if plain_reply_links
            && !optional_plain_string(row.get_item("prev_rendered_content")?.as_ref())
        {
            return Ok(false);
        }
    }
    Ok(true)
}

fn plain_history<'py>(
    py: Python<'py>,
    history: &Bound<'py, PyList>,
    plain_reply_fields: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let normalized = PyList::empty(py);
    for row in history.iter() {
        let row_dict = row.cast_exact::<PyDict>()?;
        let copy = row_dict.copy()?;
        let updates = plain_reply_fields.call1((&row, "prev_rendered_content"))?;
        copy.call_method1("update", (updates,))?;
        normalized.append(copy)?;
    }
    Ok(normalized)
}

fn trim_history(
    history: &Bound<'_, PyList>,
    allow_empty_topic: bool,
    empty_topic_fallback: &Bound<'_, PyString>,
) -> PyResult<()> {
    for row in history.iter() {
        let row = row.cast_exact::<PyDict>()?;
        if row.contains("prev_rendered_content_version")? {
            row.del_item("prev_rendered_content_version")?;
        }
        if !allow_empty_topic {
            for key in TOPIC_KEYS {
                if let Some(value) = row.get_item(key)?
                    && value.len()? == 0
                {
                    row.set_item(key, empty_topic_fallback)?;
                }
            }
        }
    }
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (obj, apply_markdown, allow_empty_topic, empty_topic_fallback, plain_reply_fields))]
pub(crate) fn finalize_message_payload_content(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
    apply_markdown: bool,
    allow_empty_topic: bool,
    empty_topic_fallback: &Bound<'_, PyString>,
    plain_reply_fields: Option<&Bound<'_, PyAny>>,
) -> PyResult<bool> {
    let Some(obj) = ordinary_dict(obj) else {
        return Ok(false);
    };
    // An overwritten custom object could run Python code during decref.
    if !optional_plain_string(obj.get_item("content_type")?.as_ref())
        || (apply_markdown && !optional_plain_string(obj.get_item("content")?.as_ref()))
    {
        return Ok(false);
    }
    let rendered = if apply_markdown {
        let Some(rendered) = obj.get_item("rendered_content")? else {
            return Ok(false);
        };
        Some(rendered)
    } else {
        None
    };
    let history = obj.get_item("edit_history")?;
    let history = match &history {
        Some(value) => match value.cast_exact::<PyList>() {
            Ok(history)
                if ordinary_history(history, allow_empty_topic, plain_reply_fields.is_some())? =>
            {
                Some(history)
            }
            _ => return Ok(false),
        },
        None => None,
    };
    if plain_reply_fields.is_some()
        && ((!optional_plain_string(rendered.as_ref()))
            || !optional_plain_string(obj.get_item("match_content")?.as_ref()))
    {
        return Ok(false);
    }

    // All declines precede mutation. Callback errors after this point
    // propagate exactly once rather than replaying the reference path.
    if let Some(rendered) = rendered {
        obj.set_item("content_type", "text/html")?;
        obj.set_item("content", rendered)?;
    } else {
        obj.set_item("content_type", "text/x-markdown")?;
    }

    if let Some(plain_reply_fields) = plain_reply_fields {
        let updates = if apply_markdown {
            plain_reply_fields.call1((obj, "content", "match_content"))?
        } else {
            plain_reply_fields.call1((obj, "match_content"))?
        };
        obj.call_method1("update", (updates,))?;
        if let Some(history) = history {
            let copied = plain_history(py, history, plain_reply_fields)?;
            obj.set_item("edit_history", &copied)?;
            trim_history(&copied, allow_empty_topic, empty_topic_fallback)?;
        }
    } else if let Some(history) = history {
        trim_history(history, allow_empty_topic, empty_topic_fallback)?;
    }
    Ok(true)
}

fn initialize_buckets<'py>(
    py: Python<'py>,
    messages: &Bound<'py, PyList>,
    field: &str,
) -> PyResult<()> {
    for message in messages.iter() {
        message
            .cast_exact::<PyDict>()?
            .set_item(field, PyList::empty(py))?;
    }
    Ok(())
}

fn assemble_annotations<'py>(
    py: Python<'py>,
    messages: &Bound<'py, PyAny>,
    annotations: &Bound<'py, PyAny>,
    field: &'static str,
    missing_message_allowed: bool,
    lazy_reaction_rows: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(messages) = messages.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let index = PyDict::new(py);
    for message in messages.iter() {
        let Some(message) = ordinary_dict(&message) else {
            return Ok(None);
        };
        // Indexing before initializing the buckets is safe only when an
        // existing bucket cannot trigger custom behavior when overwritten.
        if message.get_item(field)?.is_some_and(|existing| {
            !existing
                .cast_exact::<PyList>()
                .is_ok_and(|list| list.is_empty())
        }) {
            return Ok(None);
        }
        let Some(id) = message.get_item("id")? else {
            return Ok(None);
        };
        if !id.is_exact_instance_of::<PyInt>() {
            return Ok(None);
        }
        index.set_item(id, message)?;
    }

    if lazy_reaction_rows {
        // Django's validated Values QuerySet must not be evaluated before
        // the message buckets are initialized. Do not fall back after an
        // iteration, lookup, or append error on this path.
        initialize_buckets(py, messages, field)?;
        for annotation in annotations.try_iter()? {
            let annotation = annotation?;
            let message_id = annotation.get_item("message_id")?;
            let Some(message) = index.get_item(&message_id)? else {
                return Err(PyKeyError::new_err(message_id.unbind()));
            };
            message
                .get_item(field)?
                .call_method1("append", (annotation,))?;
        }
        return Ok(Some(index));
    }

    let Ok(annotations) = annotations.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let mut attachments = Vec::with_capacity(annotations.len());
    for annotation in annotations.iter() {
        let Some(annotation) = ordinary_dict(&annotation) else {
            return Ok(None);
        };
        let Some(message_id) = annotation.get_item("message_id")? else {
            return Ok(None);
        };
        if !message_id.is_exact_instance_of::<PyInt>() {
            return Ok(None);
        }
        match index.get_item(message_id)? {
            Some(message) => attachments.push((message, annotation.clone())),
            None if missing_message_allowed => {}
            None => return Ok(None),
        }
    }

    initialize_buckets(py, messages, field)?;
    for (message, annotation) in attachments {
        let bucket = message
            .cast_exact::<PyDict>()?
            .get_item(field)?
            .ok_or_else(|| PyKeyError::new_err(field))?;
        bucket.cast_exact::<PyList>()?.append(annotation)?;
    }
    Ok(Some(index))
}

#[pyfunction]
#[pyo3(signature = (messages, reactions, lazy_values=false))]
pub(crate) fn assemble_message_reactions<'py>(
    py: Python<'py>,
    messages: &Bound<'py, PyAny>,
    reactions: &Bound<'py, PyAny>,
    lazy_values: bool,
) -> PyResult<Option<Bound<'py, PyList>>> {
    Ok(
        assemble_annotations(py, messages, reactions, "reactions", false, lazy_values)?
            .map(|index| index.values()),
    )
}

#[pyfunction]
pub(crate) fn assemble_message_submessages<'py>(
    py: Python<'py>,
    messages: &Bound<'py, PyAny>,
    submessages: &Bound<'py, PyAny>,
) -> PyResult<bool> {
    Ok(assemble_annotations(py, messages, submessages, "submessages", true, false)?.is_some())
}

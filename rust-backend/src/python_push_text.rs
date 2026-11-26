//! Plain-text formatting after Django extracts a mobile notification preview.

use std::collections::BTreeMap;
use std::io::Write;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PySet, PyString, PyTuple};

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes, trim_python_text};

fn is_line_boundary(character: char) -> bool {
    matches!(
        character,
        '\n' | '\r'
            | '\u{b}'
            | '\u{c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

fn append_quote_line(output: &mut Vec<u8>, line: &[u8]) {
    if !line.is_empty() {
        output.extend_from_slice(b"> ");
        output.extend_from_slice(line);
        output.push(b'\n');
    }
}

#[pyfunction]
pub(crate) fn quote_mobile_push_text<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(input)?;
    let mut output = Vec::with_capacity(text.len());
    let mut offset = 0;
    let mut line_start = 0;
    for chunk in text.utf8_chunks() {
        for (index, ch) in chunk.valid().char_indices() {
            if is_line_boundary(ch) {
                let end = offset + index;
                append_quote_line(&mut output, &text[line_start..end]);
                line_start = end + ch.len_utf8();
            }
        }
        // Preserve lone surrogates in the line. CRLF may split twice,
        // which is equivalent because empty lines are never quoted.
        offset += chunk.valid().len() + chunk.invalid().len();
    }
    append_quote_line(&mut output, &text[line_start..]);
    python_string_from_bytes(input.py(), &output)
}

#[pyfunction]
pub(crate) fn truncate_mobile_push_text<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<(Bound<'py, PyString>, bool)> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(input)?;
    // Count Python code points, including lone surrogates, not graphemes.
    let Some((end, _)) = text
        .iter()
        .enumerate()
        .filter(|(_, byte)| **byte & 0xc0 != 0x80)
        .nth(200)
    else {
        return Some((input.clone(), false));
    };
    let mut output = Vec::with_capacity(end + '…'.len_utf8());
    output.extend_from_slice(&text[..end]);
    output.extend_from_slice("…".as_bytes());
    Some((python_string_from_bytes(input.py(), &output)?, true))
}

#[pyfunction]
pub(crate) fn format_mobile_ordered_list<'py>(
    rows: &Bound<'py, PyAny>,
    start: &Bound<'_, PyAny>,
    depth: &Bound<'_, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    let start = start.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let depth = depth.cast_exact::<PyInt>().ok()?.extract::<usize>().ok()?;
    if depth > 256 {
        return None;
    }
    start.checked_add(i64::try_from(rows.len().saturating_sub(1)).ok()?)?;

    let indent = if depth == 0 {
        String::new()
    } else {
        format!("\n{}", "  ".repeat(depth))
    };
    let mut output = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let row = python_string_bytes(row.cast_exact::<PyString>().ok()?)?;
        if index != 0 {
            output.push(b'\n');
        }
        output.extend_from_slice(indent.as_bytes());
        let number = start + i64::try_from(index).ok()?;
        write!(output, "{number}. ").ok()?;
        output.extend_from_slice(trim_python_text(&row));
    }
    python_string_from_bytes(rows.py(), &output)
}

#[pyfunction]
pub(crate) fn format_mobile_spoiler_header<'py>(
    header: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let header = header.cast_exact::<PyString>().ok()?;
    let text = python_string_bytes(header)?;
    let text = trim_python_text(&text);
    let mut preview = Vec::with_capacity(text.len().saturating_add(7));
    if !text.is_empty() {
        preview.extend_from_slice(text);
        preview.push(b' ');
    }
    preview.extend_from_slice("(…)\n".as_bytes());
    python_string_from_bytes(header.py(), &preview)
}

#[pyfunction]
pub(crate) fn plan_mobile_quote_collapse(statuses: &Bound<'_, PyAny>) -> Option<Vec<Option<bool>>> {
    let statuses = statuses.cast_exact::<PyList>().ok()?;
    let mut has_quote = false;
    let mut has_unquoted = false;
    let mut previous = false;
    let mut actions = Vec::with_capacity(statuses.len());
    for status in statuses.iter() {
        let current = status.cast_exact::<PyBool>().ok()?.extract::<bool>().ok()?;
        has_quote |= current;
        has_unquoted |= !current;
        // None keeps the element, true drops it, false replaces it with the marker.
        actions.push(current.then_some(previous));
        previous = current;
    }
    if !has_quote || !has_unquoted {
        actions.clear();
    }
    Some(actions)
}

#[pyfunction]
pub(crate) fn render_plain_mobile_preview_tree<'py>(
    rows: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let rows = rows.cast_exact::<PyList>().ok()?;
    if rows.is_empty() || rows.len() > 512 {
        return None;
    }
    let mut output = Vec::new();
    let mut tails: Vec<Bound<'py, PyString>> = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let row = row.cast_exact::<PyTuple>().ok()?;
        if row.len() != 3 {
            return None;
        }
        let depth: usize = row
            .get_item(0)
            .ok()?
            .cast_exact::<PyInt>()
            .ok()?
            .extract()
            .ok()?;
        if (index == 0 && depth != 0)
            || (index > 0 && depth == 0)
            || depth > tails.len()
            || depth > 128
        {
            return None;
        }
        while tails.len() > depth {
            output.extend_from_slice(&python_string_bytes(&tails.pop()?)?);
        }
        let text = row.get_item(1).ok()?.cast_into_exact::<PyString>().ok()?;
        let tail = row.get_item(2).ok()?.cast_into_exact::<PyString>().ok()?;
        output.extend_from_slice(&python_string_bytes(&text)?);
        tails.push(tail);
    }
    for tail in tails.iter().rev() {
        output.extend_from_slice(&python_string_bytes(tail)?);
    }
    python_string_from_bytes(rows.py(), &output)
}

#[pyfunction]
pub(crate) fn format_mobile_remove_fields(
    payload: &Bound<'_, PyAny>,
    message_ids: &Bound<'_, PyAny>,
    legacy: bool,
) -> PyResult<bool> {
    let (Ok(payload), Ok(message_ids)) = (
        payload.cast_exact::<PyDict>(),
        message_ids.cast_exact::<PyList>(),
    ) else {
        return Ok(false);
    };
    if legacy {
        let mut joined = Vec::new();
        for (index, item) in message_ids.iter().enumerate() {
            let Some(message_id) = item
                .cast_exact::<PyInt>()
                .ok()
                .and_then(|integer| integer.extract::<i64>().ok())
            else {
                // Decline unchanged before touching the payload.
                return Ok(false);
            };
            if index != 0 {
                joined.push(b',');
            }
            write!(joined, "{message_id}").expect("writing to Vec succeeds");
        }
        let ids = PyString::new(
            payload.py(),
            std::str::from_utf8(&joined).expect("ASCII IDs"),
        );
        payload.set_item("event", "remove")?;
        payload.set_item("zulip_message_ids", ids)?;
    } else {
        payload.set_item("type", "remove")?;
        payload.set_item("message_ids", message_ids)?;
    }
    Ok(true)
}

#[pyfunction(signature = (legacy, realm_url, realm_name, user_id))]
pub(crate) fn format_mobile_base_payload<'py>(
    py: Python<'py>,
    legacy: Option<&Bound<'py, PyTuple>>,
    realm_url: &Bound<'py, PyAny>,
    realm_name: &Bound<'py, PyAny>,
    user_id: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let output = PyDict::new(py);
    if let Some(legacy) = legacy {
        output.set_item("server", legacy.get_item(0)?)?;
        output.set_item("realm_id", legacy.get_item(1)?)?;
        output.set_item("realm_uri", legacy.get_item(2)?)?;
    }
    output.set_item("realm_url", realm_url)?;
    output.set_item("realm_name", realm_name)?;
    output.set_item("user_id", user_id)?;
    Ok(output)
}

#[pyfunction]
pub(crate) fn format_mobile_message_identity(
    payload: &Bound<'_, PyAny>,
    id: &Bound<'_, PyAny>,
    mode: &str,
) -> PyResult<bool> {
    let (Ok(payload), Ok(id)) = (payload.cast_exact::<PyDict>(), id.cast_exact::<PyInt>()) else {
        return Ok(false);
    };
    match mode {
        "apns" => payload.set_item("message_ids", PyList::new(payload.py(), [id])?)?,
        "legacy" => {
            payload.set_item("event", "message")?;
            payload.set_item("zulip_message_id", id)?;
        }
        "modern" => {
            payload.set_item("type", "message")?;
            payload.set_item("message_id", id)?;
        }
        _ => return Ok(false),
    }
    Ok(true)
}

#[pyfunction]
pub(crate) fn format_prepared_android_message<'py>(
    payload: &Bound<'py, PyDict>,
    timestamp: &Bound<'py, PyAny>,
    content: &Bound<'py, PyAny>,
    sender_name: &Bound<'py, PyAny>,
    sender_avatar_url: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    // Mutate only the prepared copy. Reuse Python objects without narrowing
    // integer size or altering localized text and URL values.
    payload.set_item("time", timestamp)?;
    payload.set_item("content", content)?;
    payload.set_item("sender_full_name", sender_name)?;
    payload.set_item("sender_avatar_url", sender_avatar_url)?;
    let options = PyDict::new(payload.py());
    options.set_item("priority", "high")?;
    Ok(options)
}

#[pyfunction]
pub(crate) fn format_mobile_direct_recipient_ids<'py>(
    recipients: &Bound<'py, PyAny>,
    get_sender: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let Ok(recipients) = recipients.cast_exact::<PyList>() else {
        return Ok(None);
    };
    let mut ids = BTreeMap::new();
    let exact_integer = |id: &Bound<'_, PyAny>| {
        id.cast_exact::<PyInt>()
            .ok()
            .and_then(|integer| integer.extract::<i64>().ok())
    };
    for row in recipients.iter() {
        let Ok(row) = row.cast_exact::<PyDict>() else {
            return Ok(None);
        };
        let Some(id) = row.get_item("id")? else {
            return Ok(None);
        };
        let Some(value) = exact_integer(&id) else {
            return Ok(None);
        };
        ids.entry(value).or_insert(id);
    }
    if ids.len() == 1 {
        let sender = get_sender.call0()?;
        let Some(value) = exact_integer(&sender) else {
            let py = recipients.py();
            let combined = PySet::new(py, ids.into_values())?;
            combined.add(sender)?;
            let sorted = py.import("builtins")?.call_method1("sorted", (combined,))?;
            return Ok(Some(sorted.cast_into_exact::<PyList>()?));
        };
        ids.entry(value).or_insert(sender);
    }
    Ok(Some(PyList::new(recipients.py(), ids.into_values())?))
}

#[pyfunction]
pub(crate) fn format_mobile_channel_recipient(
    payload: &Bound<'_, PyAny>,
    channel_id: &Bound<'_, PyAny>,
    channel_name: &Bound<'_, PyAny>,
    legacy: bool,
) -> PyResult<bool> {
    let Ok(payload) = payload.cast_exact::<PyDict>() else {
        return Ok(false);
    };
    let (kind, name_key, id_key) = if legacy {
        ("stream", "stream", "stream_id")
    } else {
        ("channel", "channel_name", "channel_id")
    };
    payload.set_item("recipient_type", kind)?;
    payload.set_item(name_key, channel_name)?;
    payload.set_item(id_key, channel_id)?;
    Ok(true)
}

#[pyfunction]
pub(crate) fn format_apns_group_title<'py>(
    names: &Bound<'py, PyList>,
) -> Option<Bound<'py, PyString>> {
    let mut parts = names
        .iter()
        .map(|name| {
            let name = name.cast_exact::<PyString>().ok()?;
            Some(python_string_bytes(name)?.into_owned())
        })
        .collect::<Option<Vec<_>>>()?;
    // UTF-8 (including surrogatepass) compares in code-point order, exactly
    // the sort used by Python for ordinary strings. Keep repeated names.
    parts.sort_unstable();
    python_string_from_bytes(names.py(), &parts.join(&b", "[..]))
}

#[pyfunction]
pub(crate) fn format_apns_channel_title<'py>(
    channel: &Bound<'py, PyAny>,
    topic: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let channel_text = python_string_bytes(channel.cast_exact::<PyString>().ok()?)?;
    let topic_text = python_string_bytes(topic.cast_exact::<PyString>().ok()?)?;
    let mut title = Vec::with_capacity(channel_text.len() + topic_text.len() + 4);
    title.extend_from_slice(b"#");
    title.extend_from_slice(&channel_text);
    title.extend_from_slice(b" > ");
    title.extend_from_slice(&topic_text);
    python_string_from_bytes(channel.py(), &title)
}

#[pyfunction]
pub(crate) fn render_prepared_apns_subtitle<'py>(
    sender_name: &Bound<'py, PyAny>,
    trigger: &Bound<'py, PyAny>,
    group_name: &Bound<'py, PyAny>,
    trigger_names: &Bound<'py, PyTuple>,
    is_small_group: &Bound<'py, PyAny>,
    render_mention: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let (Ok(sender_name), Ok(trigger)) = (
        sender_name.cast_exact::<PyString>(),
        trigger.cast_exact::<PyString>(),
    ) else {
        return Ok(None);
    };
    if trigger.as_any().eq(trigger_names.get_item(0)?)? {
        let kind = if group_name.is_none() { "you" } else { "group" };
        return Ok(Some(render_mention.call1((kind,))?));
    }
    if trigger_names.get_item(1)?.contains(trigger)? {
        return Ok(Some(render_mention.call1(("all",))?));
    }
    if is_small_group.call0()?.is_truthy()? {
        return Ok(Some(PyString::new(sender_name.py(), "").into_any()));
    }

    // No decline after the recipient callback: even allocation errors from
    // concatenation must propagate without loading recipients twice.
    Ok(Some(
        sender_name
            .as_any()
            .add(PyString::new(sender_name.py(), ":"))?,
    ))
}

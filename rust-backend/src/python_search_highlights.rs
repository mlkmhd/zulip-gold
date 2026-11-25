//! Project the match ranges that Django's search query already selected.

use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString, PyTuple};

const START: &[u8] = br#"<span class="highlight">"#;
const END: &[u8] = b"</span>";

fn highlight(
    text: &[u8],
    spans: impl IntoIterator<Item = Option<(usize, usize)>>,
) -> Option<Vec<u8>> {
    let mut result = Vec::with_capacity(text.len());
    // DB offsets describe Unicode code points, not UTF-8 bytes or graphemes.
    let mut boundaries = text
        .iter()
        .enumerate()
        .filter_map(|(index, &byte)| (byte & 0xc0 != 0x80).then_some(index))
        .chain([text.len()]);
    let mut code_point = 0;
    let mut byte = boundaries.next()?;
    let mut in_tag = false;
    for span in spans {
        let (offset, length) = span?;
        let skip = offset.checked_sub(code_point)?;
        let prefix = byte;
        if skip != 0 {
            byte = boundaries.nth(skip - 1)?;
        }
        let begin = byte;
        if length != 0 {
            byte = boundaries.nth(length - 1)?;
        }
        code_point = offset.checked_add(length)?;
        // The Python reference evaluates tag state after the whole range.
        if let Some(delimiter) = text
            .get(prefix..byte)?
            .iter()
            .rfind(|&&value| matches!(value, b'<' | b'>'))
        {
            in_tag = *delimiter == b'<';
        }
        result.extend_from_slice(text.get(prefix..begin)?);
        if !in_tag {
            result.extend_from_slice(START);
        }
        result.extend_from_slice(text.get(begin..byte)?);
        if !in_tag {
            result.extend_from_slice(END);
        }
    }
    result.extend_from_slice(text.get(byte..)?);
    Some(result)
}

fn span(value: Bound<'_, PyAny>) -> Option<(usize, usize)> {
    let (offset, length) = if let Ok(pair) = value.cast_exact::<PyList>() {
        if pair.len() != 2 {
            return None;
        }
        (pair.get_item(0).ok()?, pair.get_item(1).ok()?)
    } else {
        let pair = value.cast_exact::<PyTuple>().ok()?;
        if pair.len() != 2 {
            return None;
        }
        (pair.get_item(0).ok()?, pair.get_item(1).ok()?)
    };
    Some((
        offset.cast_exact::<PyInt>().ok()?.extract().ok()?,
        length.cast_exact::<PyInt>().ok()?.extract().ok()?,
    ))
}

fn projected<'py>(
    text: Bound<'py, PyAny>,
    positions: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let text = text.cast_into_exact::<PyString>().ok()?;
    let spelling = python_string_bytes(&text)?;
    let rendered = if let Ok(spans) = positions.cast_exact::<PyList>() {
        if spans.is_empty() {
            return Some(text);
        }
        highlight(&spelling, spans.iter().map(span))?
    } else {
        let spans = positions.cast_exact::<PyTuple>().ok()?;
        if spans.is_empty() {
            return Some(text);
        }
        highlight(&spelling, spans.iter().map(span))?
    };
    python_string_from_bytes(text.py(), &rendered)
}

#[pyfunction]
pub(crate) fn assemble_search_highlights<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    let content_key = pyo3::intern!(py, "match_content");
    let subject_key = pyo3::intern!(py, "match_subject");
    for row in rows.cast_exact::<PyList>().ok()?.iter() {
        let row = row.cast_exact::<PyTuple>().ok()?;
        if row.len() != 5 {
            return None;
        }
        let id = row.get_item(0).ok()?;
        if !id.is_exact_instance_of::<PyInt>() {
            return None;
        }
        let fields = PyDict::new(py);
        fields
            .set_item(
                content_key,
                projected(row.get_item(1).ok()?, &row.get_item(3).ok()?)?,
            )
            .ok()?;
        fields
            .set_item(
                subject_key,
                projected(row.get_item(2).ok()?, &row.get_item(4).ok()?)?,
            )
            .ok()?;
        result.set_item(id, fields).ok()?;
    }
    Some(result)
}

#[cfg(test)]
mod tests {
    use super::highlight as highlight_bytes;

    fn highlight(
        text: &str,
        spans: impl IntoIterator<Item = Option<(usize, usize)>>,
    ) -> Option<String> {
        String::from_utf8(highlight_bytes(text.as_bytes(), spans)?).ok()
    }

    #[test]
    fn preserves_code_point_offsets_and_adjacent_boundaries() {
        assert_eq!(
            highlight("é🙂a\u{301}z", [Some((1, 1)), Some((2, 2))]).unwrap(),
            "é<span class=\"highlight\">🙂</span><span class=\"highlight\">a\u{301}</span>z"
        );
        assert_eq!(
            highlight("🙂", [Some((1, 0)), Some((1, 0))]).unwrap(),
            "🙂<span class=\"highlight\"></span><span class=\"highlight\"></span>"
        );
    }

    #[test]
    fn maintains_html_tag_state_across_multiple_spans() {
        assert_eq!(
            highlight(
                "<a title=\"a\">a</a>",
                [Some((3, 5)), Some((10, 1)), Some((13, 1))]
            )
            .unwrap(),
            "<a title=\"a\"><span class=\"highlight\">a</span></a>"
        );
        assert_eq!(
            highlight("<p>x</p>", [Some((0, 3))]).unwrap(),
            "<span class=\"highlight\"><p></span>x</p>"
        );
    }

    #[test]
    fn lets_python_handle_noncanonical_ranges() {
        assert_eq!(highlight("abc", [Some((0, 2)), Some((1, 1))]), None);
        assert_eq!(highlight("é", [Some((0, 2))]), None);
        assert_eq!(highlight("é", [Some((usize::MAX, 1))]), None);
        assert_eq!(highlight("é", [None]), None);
        assert_eq!(highlight("é", []), Some("é".to_owned()));
    }
}

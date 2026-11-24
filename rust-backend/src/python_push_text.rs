//! Plain-text formatting after Django extracts a mobile notification preview.

use pyo3::prelude::*;
use pyo3::types::PyString;

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

#[pyfunction]
pub(crate) fn quote_mobile_push_text(input: &Bound<'_, PyAny>) -> Option<String> {
    let input = input.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let mut output = String::with_capacity(input.len());
    // Python's splitlines treats CRLF as a single boundary. Treating it as
    // two is equivalent here because empty lines are not rendered.
    for line in input
        .split(is_line_boundary)
        .filter(|line| !line.is_empty())
    {
        output.push_str("> ");
        output.push_str(line);
        output.push('\n');
    }
    Some(output)
}

#[pyfunction]
pub(crate) fn truncate_mobile_push_text<'py>(
    input: &Bound<'py, PyAny>,
) -> Option<(Bound<'py, PyString>, bool)> {
    let input = input.cast_exact::<PyString>().ok()?;
    let text = input.to_str().ok()?;
    // The existing mobile payload limit counts Python code points, not
    // UTF-8 bytes or grapheme clusters.
    let Some((end, _)) = text.char_indices().nth(200) else {
        return Some((input.clone(), false));
    };
    let mut output = String::with_capacity(end + '…'.len_utf8());
    output.push_str(&text[..end]);
    output.push('…');
    Some((PyString::new(input.py(), &output), true))
}

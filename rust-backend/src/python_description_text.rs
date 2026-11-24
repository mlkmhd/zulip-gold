//! Whitespace normalization of already extracted plain description text.

use std::borrow::Cow;

use pyo3::prelude::*;
use pyo3::types::PyString;

fn python_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

fn normalize(text: &str) -> Cow<'_, str> {
    let mut words = text.split(python_space).filter(|word| !word.is_empty());
    let Some(first) = words.next() else {
        return Cow::Borrowed("");
    };
    let Some(second) = words.next() else {
        return Cow::Borrowed(first);
    };
    let mut out = String::with_capacity(text.len());
    out.push_str(first);
    for word in std::iter::once(second).chain(words) {
        out.push(' ');
        out.push_str(word);
    }
    Cow::Owned(out)
}

#[pyfunction]
pub(crate) fn normalize_description_text<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let value = value.cast_exact::<PyString>().ok()?;
    let text = value.to_str().ok()?;
    Some(match normalize(text) {
        // A one-word split and join keeps the original Python string.
        Cow::Borrowed(word) if word.len() == text.len() => value.clone(),
        normalized => PyString::new(py, &normalized),
    })
}

#[cfg(test)]
mod tests {
    use super::normalize;

    #[test]
    fn normalizes_python_whitespace_without_altering_other_scalars() {
        assert_eq!(normalize("\r\u{1f} فارسی\u{85}\t😀\u{3000}"), "فارسی 😀");
        assert_eq!(normalize("\u{200b}\u{feff}\0"), "\u{200b}\u{feff}\0");
        assert_eq!(normalize("\t\u{2028}\u{a0}"), "");
    }
}

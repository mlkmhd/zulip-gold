//! Prepare two already selected plain display strings without choosing a fallback.

use pyo3::prelude::*;
use pyo3::types::PyString;

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

#[pyfunction]
pub(crate) fn plan_mattermost_display_name(
    first: &Bound<'_, PyAny>,
    last: &Bound<'_, PyAny>,
) -> Option<(String, bool)> {
    let first = first.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let last = last.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let has_text = first.chars().chain(last.chars()).any(|ch| !py_space(ch));
    Some((format!("{first} {last}"), has_text))
}

//! Fixed text transformations over Python-selected documentation tab content.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

fn is_python_space(c: char) -> bool {
    // CPython str.strip(): Unicode spacing plus the ASCII information separators.
    matches!(
        c,
        '\t'..='\r'
            | '\u{001c}'..=' '
            | '\u{0085}'
            | '\u{00a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

fn join_lines<'py>(lines: &Bound<'py, PyAny>, trim: bool) -> Option<Bound<'py, PyString>> {
    let lines = lines.cast_exact::<PyList>().ok()?;
    if lines.len() == 1 {
        let value = lines.get_item(0).ok()?;
        let value = value.cast_exact::<PyString>().ok()?;
        if !trim {
            return Some(value.clone());
        }
        let original = value.to_str().ok()?;
        let trimmed = original.trim_matches(is_python_space);
        return Some(if original.len() == trimmed.len() {
            value.clone()
        } else {
            PyString::new(lines.py(), trimmed)
        });
    }

    let mut joined = String::new();
    for (index, line) in lines.iter().enumerate() {
        let text = line.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if index > 0 {
            joined.push('\n');
        }
        joined.push_str(text);
    }
    Some(PyString::new(
        lines.py(),
        if trim {
            joined.trim_matches(is_python_space)
        } else {
            &joined
        },
    ))
}

#[pyfunction]
pub(crate) fn normalize_prepared_tabbed_lines<'py>(
    lines: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_lines(lines, true)
}

#[pyfunction]
pub(crate) fn join_prepared_tabbed_blocks<'py>(
    tabs: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    join_lines(tabs, false)
}

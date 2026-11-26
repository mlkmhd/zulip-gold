//! Pure quote/reply size calculations; Markdown fence closing stays with Django.

use pyo3::prelude::*;
use pyo3::types::{PyFunction, PyInt, PySlice, PyString};

const MINIMUM_QUOTE: i128 = 1000;
const TRUNCATION: &str = "\n[message truncated]";

fn integer(value: &Bound<'_, PyAny>) -> Option<i128> {
    Some(i128::from(
        value.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?,
    ))
}

#[pyfunction]
pub(crate) fn allocate_compose_quote_lengths(
    reply_length: &Bound<'_, PyAny>,
    quote_length: &Bound<'_, PyAny>,
    syntax_length: &Bound<'_, PyAny>,
    message_limit: &Bound<'_, PyAny>,
) -> Option<(i64, i64)> {
    let (reply, quote, syntax, limit) = (
        integer(reply_length)?,
        integer(quote_length)?,
        integer(syntax_length)?,
        integer(message_limit)?,
    );
    let available = (limit - syntax).max(0);
    let quoted = quote.min((available - reply).max(MINIMUM_QUOTE.min(available)));
    let replied = reply.min(available - quoted);
    Some((i64::try_from(replied).ok()?, i64::try_from(quoted).ok()?))
}

#[pyfunction]
pub(crate) fn truncate_compose_quote_text<'py>(
    content: &Bound<'py, PyAny>,
    max_length: &Bound<'py, PyAny>,
    // The caller supplies only its fixed close_unclosed_fences function.
    close_fences: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyString>>> {
    let (Ok(content), Some(max_length)) = (content.cast_exact::<PyString>(), integer(max_length))
    else {
        return Ok(None);
    };
    let Ok(text) = content.to_str() else {
        return Ok(None);
    };
    if text.chars().count() as i128 <= max_length {
        return Ok(Some(content.clone()));
    }
    if !close_fences.is_exact_instance_of::<PyFunction>() {
        return Ok(None);
    }

    let suffix_length = TRUNCATION.len() as i128;
    let mut end = max_length - suffix_length;
    while end > 0 {
        // end is less than the original code-point length.
        let Some((byte, _)) = text.char_indices().nth(end as usize) else {
            return Ok(None);
        };
        let closed = close_fences.call1((&text[..byte],))?;
        let Ok(closed) = closed.cast_exact::<PyString>() else {
            return Ok(None);
        };
        let Ok(closed) = closed.to_str() else {
            return Ok(None);
        };
        let overflow = closed.chars().count() as i128 + suffix_length - max_length;
        if overflow <= 0 {
            let mut result = String::with_capacity(closed.len() + TRUNCATION.len());
            result.push_str(closed);
            result.push_str(TRUNCATION);
            return Ok(Some(PyString::new(content.py(), &result)));
        }
        end -= overflow;
    }
    Ok(Some(PyString::new(content.py(), TRUNCATION)))
}

#[pyfunction]
pub(crate) fn truncate_exact_compose_quote_text<'py>(
    content: &Bound<'py, PyAny>,
    max_length: &Bound<'py, PyAny>,
    close_fences: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let (Ok(content), Some(max_length)) = (content.cast_exact::<PyString>(), integer(max_length))
    else {
        return Ok(None);
    };
    let original_len = content.as_any().len()? as i128;
    if original_len <= max_length {
        return Ok(Some(content.clone().into_any()));
    }
    if !close_fences.is_exact_instance_of::<PyFunction>() {
        return Ok(None);
    }

    let py = content.py();
    let suffix_len = TRUNCATION.len() as i128;
    let mut end = max_length - suffix_len;
    while end > 0 {
        // The initial stop is smaller than the exact Python length; it
        // only decreases after an overflow.
        let stop = isize::try_from(end).expect("the slice stop fits Python's string length");
        let prefix = content.get_item(PySlice::new(py, 0, stop, 1))?;
        let closed = close_fences.call1((prefix,))?;
        let overflow = closed.len()? as i128 + suffix_len - max_length;
        if overflow <= 0 {
            if let Ok(text) = closed.cast_exact::<PyString>()
                && let Ok(text) = text.to_str()
            {
                let result = format!("{text}{TRUNCATION}");
                return Ok(Some(PyString::new(py, &result).into_any()));
            }
            // Keep the actual Python addition result or exception after the
            // closer has run. Never replay even a custom closer result.
            return Ok(Some(closed.add(PyString::new(py, TRUNCATION))?));
        }
        end -= overflow;
    }
    Ok(Some(PyString::new(py, TRUNCATION).into_any()))
}

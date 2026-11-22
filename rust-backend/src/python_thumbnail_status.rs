//! Completeness only: no storage, decoding, upload policy, or authorization.

use std::collections::HashSet;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct FormatKey {
    extension: String,
    width: i64,
    height: i64,
    animated: bool,
}

fn integer(value: &Bound<'_, PyAny>) -> Option<i64> {
    value.cast_exact::<PyInt>().ok()?.extract().ok()
}

fn make_key(
    extension: &Bound<'_, PyAny>,
    width: &Bound<'_, PyAny>,
    height: &Bound<'_, PyAny>,
    animated: &Bound<'_, PyAny>,
) -> Option<FormatKey> {
    Some(FormatKey {
        extension: extension
            .cast_exact::<PyString>()
            .ok()?
            .to_str()
            .ok()?
            .to_owned(),
        width: integer(width)?,
        height: integer(height)?,
        animated: animated.cast_exact::<PyBool>().ok()?.extract().ok()?,
    })
}

fn supplied_key(value: &Bound<'_, PyAny>) -> Option<FormatKey> {
    let tuple = value.cast_exact::<PyTuple>().ok()?;
    if tuple.len() != 4 {
        return None;
    }
    make_key(
        &tuple.get_item(0).ok()?,
        &tuple.get_item(1).ok()?,
        &tuple.get_item(2).ok()?,
        &tuple.get_item(3).ok()?,
    )
}

fn stored_key(value: &Bound<'_, PyAny>) -> Option<FormatKey> {
    let row = value.cast_exact::<PyDict>().ok()?;
    if row.len() != 8 {
        return None;
    }
    // Preserve Python's fallback for old or unsupported stored shapes.
    // The physical dimensions, MIME field and byte size are not format keys.
    for key in ["width", "height", "byte_size"] {
        integer(&row.get_item(key).ok()??)?;
    }
    if !row
        .get_item("content_type")
        .ok()??
        .is_exact_instance_of::<PyString>()
    {
        return None;
    }
    make_key(
        &row.get_item("extension").ok()??,
        &row.get_item("max_width").ok()??,
        &row.get_item("max_height").ok()??,
        &row.get_item("animated").ok()??,
    )
}

fn complete(
    seen: &HashSet<FormatKey>,
    normal: Vec<FormatKey>,
    transcode: Option<FormatKey>,
    frames: i64,
    landscape: bool,
) -> bool {
    let transcode = transcode.map(|mut format| {
        if !landscape {
            std::mem::swap(&mut format.width, &mut format.height);
        }
        format
    });
    normal
        .into_iter()
        .chain(transcode)
        .filter(|format| frames != 1 || !format.animated)
        .all(|format| seen.contains(&format))
}

#[pyfunction]
pub(crate) fn thumbnail_status_complete(
    metadata: &Bound<'_, PyAny>,
    formats: &Bound<'_, PyAny>,
    transcode: &Bound<'_, PyAny>,
    frames: &Bound<'_, PyAny>,
    original_width: &Bound<'_, PyAny>,
    original_height: &Bound<'_, PyAny>,
) -> Option<bool> {
    let seen = metadata
        .cast_exact::<PyList>()
        .ok()?
        .iter()
        .map(|row| stored_key(&row))
        .collect::<Option<HashSet<_>>>()?;
    let normal = formats
        .cast_exact::<PyTuple>()
        .ok()?
        .iter()
        .map(|row| supplied_key(&row))
        .collect::<Option<Vec<_>>>()?;
    let transcode = if transcode.is_none() {
        None
    } else {
        Some(supplied_key(transcode)?)
    };
    Some(complete(
        &seen,
        normal,
        transcode,
        integer(frames)?,
        integer(original_width)? >= integer(original_height)?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(width: i64, height: i64, animated: bool) -> FormatKey {
        FormatKey {
            extension: "webp".into(),
            width,
            height,
            animated,
        }
    }

    #[test]
    fn treats_still_and_animated_as_separate_outputs() {
        let still = key(840, 560, false);
        let animation = key(840, 560, true);
        let seen = HashSet::from([still.clone()]);
        assert!(complete(
            &seen,
            vec![animation.clone(), still.clone()],
            None,
            1,
            true
        ));
        assert!(!complete(&seen, vec![animation, still], None, 2, true));
    }

    #[test]
    fn only_rotates_the_required_transcode_for_portrait_originals() {
        let seen = HashSet::from([key(840, 560, false), key(3024, 4032, false)]);
        assert!(complete(
            &seen,
            vec![key(840, 560, false)],
            Some(key(4032, 3024, false)),
            1,
            false
        ));
        assert!(!complete(
            &seen,
            vec![key(840, 560, false)],
            Some(key(4032, 3024, false)),
            1,
            true
        ));
    }

    #[test]
    fn no_expected_outputs_is_complete() {
        assert!(complete(&HashSet::new(), vec![], None, 1, true));
        assert!(!complete(
            &HashSet::new(),
            vec![],
            Some(key(4032, 3024, false)),
            1,
            true
        ));
    }
}

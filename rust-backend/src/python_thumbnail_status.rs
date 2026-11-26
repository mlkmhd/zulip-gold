//! Thumbnail metadata only: no storage, decoding, upload policy, or authorization.

use std::collections::HashSet;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

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
    if row.len() != 8
        || row
            .keys()
            .iter()
            .any(|key| !key.is_exact_instance_of::<PyString>())
    {
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

fn mark_stored(value: &Bound<'_, PyAny>, formats: &[FormatKey], found: &mut [bool]) -> Option<()> {
    let row = value.cast_exact::<PyDict>().ok()?;
    if row.len() != 8 {
        return None;
    }
    let (mut extension, mut width, mut height, mut animated) = (None, None, None, None);
    for (key, value) in row.iter() {
        match key.cast_exact::<PyString>().ok()?.to_str().ok()? {
            "extension" => extension = Some(value.cast_into_exact::<PyString>().ok()?),
            "max_width" => width = Some(integer(&value)?),
            "max_height" => height = Some(integer(&value)?),
            "animated" => {
                animated = Some(value.cast_exact::<PyBool>().ok()?.extract::<bool>().ok()?)
            }
            "width" | "height" | "byte_size" => {
                integer(&value)?;
            }
            "content_type" if value.is_exact_instance_of::<PyString>() => (),
            _ => return None,
        }
    }
    let extension = extension?;
    let extension = extension.to_str().ok()?;
    let (width, height, animated) = (width?, height?, animated?);
    for (format, found) in formats.iter().zip(found) {
        *found |= format.extension == extension
            && format.width == width
            && format.height == height
            && format.animated == animated;
    }
    Some(())
}

/// Return the missing positions in Django's ordered output configuration.
#[pyfunction]
pub(crate) fn thumbnail_missing_indexes(
    metadata: &Bound<'_, PyAny>,
    formats: &Bound<'_, PyAny>,
    frames: &Bound<'_, PyAny>,
) -> Option<Vec<usize>> {
    let metadata = metadata.cast_exact::<PyList>().ok()?;
    let formats = formats
        .cast_exact::<PyTuple>()
        .ok()?
        .iter()
        .map(|row| supplied_key(&row))
        .collect::<Option<Vec<_>>>()?;
    let still = integer(frames)? == 1;
    let mut found = vec![false; formats.len()];
    for row in metadata.iter() {
        mark_stored(&row, &formats, &mut found)?;
    }
    Some(
        formats
            .into_iter()
            .zip(found)
            .enumerate()
            .filter_map(|(index, (format, found))| {
                (!(found || still && format.animated)).then_some(index)
            })
            .collect(),
    )
}

/// Check a resolved variant against the existing manifest, without I/O.
#[pyfunction]
pub(crate) fn thumbnail_has_stored_variant(
    metadata: &Bound<'_, PyAny>,
    requested: &Bound<'_, PyAny>,
) -> Option<bool> {
    let metadata = metadata.cast_exact::<PyList>().ok()?;
    let requested = [supplied_key(requested)?];
    let mut found = [false];
    for row in metadata.iter() {
        // Still validate later rows so an apparently exact match cannot
        // bypass Python's handling of unsupported legacy metadata.
        mark_stored(&row, &requested, &mut found)?;
    }
    Some(found[0])
}

// The dataclass constructor expects exactly these eight field names. Only
// dimensions affect the selection; Python still constructs the selected row.
fn stored_area(value: &Bound<'_, PyAny>) -> Option<i128> {
    let row = value.cast_exact::<PyDict>().ok()?;
    if row.len() != 8 {
        return None;
    }
    let (mut width, mut height) = (None, None);
    for (key, value) in row.iter() {
        match key.cast_exact::<PyString>().ok()?.to_str().ok()? {
            "width" => width = integer(&value),
            "height" => height = integer(&value),
            "extension" | "max_width" | "max_height" | "animated" | "content_type"
            | "byte_size" => (),
            _ => return None,
        }
    }
    Some(i128::from(width?) * i128::from(height?))
}

fn largest_index(areas: impl Iterator<Item = Option<i128>>) -> Option<usize> {
    let mut best = None;
    for (index, area) in areas.enumerate() {
        let area = area?;
        // The reference stable sort takes the last row on equal area.
        if best.is_none_or(|(_, maximum)| area >= maximum) {
            best = Some((index, area));
        }
    }
    best.map(|(index, _)| index)
}

/// Plan selections for already selected rows. A decline leaves all rows intact.
#[pyfunction]
pub(crate) fn thumbnail_transcode_indexes(candidates: &Bound<'_, PyAny>) -> Option<Vec<usize>> {
    candidates
        .cast_exact::<PyList>()
        .ok()?
        .iter()
        .map(|metadata| {
            let metadata = metadata.cast_exact::<PyList>().ok()?;
            largest_index(metadata.iter().map(|row| stored_area(&row)))
        })
        .collect()
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
    fn selects_last_maximum_and_declines_any_unsupported_row() {
        assert_eq!(
            largest_index([Some(6), Some(8), Some(8), Some(2)].into_iter()),
            Some(2)
        );
        assert_eq!(largest_index([Some(-6), Some(-8)].into_iter()), Some(0));
        assert_eq!(largest_index([Some(6), None, Some(8)].into_iter()), None);
        assert_eq!(largest_index(std::iter::empty()), None);
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

#[pyfunction]
pub(crate) fn thumbnail_frame_load_options(
    frames: &Bound<'_, PyAny>,
    width: &Bound<'_, PyAny>,
    height: &Bound<'_, PyAny>,
    animated: &Bound<'_, PyAny>,
    limit: &Bound<'_, PyAny>,
) -> Option<String> {
    let frames = integer(frames)?;
    if frames <= 1 {
        return Some(String::new());
    }
    if !animated
        .cast_exact::<PyBool>()
        .ok()?
        .extract::<bool>()
        .ok()?
    {
        return Some("n=1".into());
    }
    let (width, height) = (integer(width)?, integer(height)?);
    if width <= 0 || height <= 0 {
        return None;
    }
    let pixels_per_frame = width.checked_mul(height)?;
    let total = pixels_per_frame.checked_mul(frames)?;
    let (limit, suffix) = if limit.is_exact_instance_of::<PyInt>() {
        (integer(limit)?, "")
    } else {
        let limit = limit.cast_exact::<PyFloat>().ok()?.extract::<f64>().ok()?;
        // The configured budget is an integral float. Preserve its output
        // spelling and leave other floating-point behavior to CPython.
        if !(0.0..=1_099_511_627_776.0).contains(&limit)
            || limit.fract() != 0.0
            || limit.is_sign_negative()
            || pixels_per_frame > (1_i64 << 40)
        {
            return None;
        }
        (limit as i64, ".0")
    };
    if limit < 0 {
        return None;
    }
    if total < limit {
        Some("n=-1".into())
    } else {
        Some(format!("n={}{}", limit / pixels_per_frame, suffix))
    }
}

#[pyfunction]
pub(crate) fn thumbnail_format_string(
    width: &Bound<'_, PyAny>,
    height: &Bound<'_, PyAny>,
    animated: &Bound<'_, PyAny>,
    extension: &Bound<'_, PyAny>,
) -> Option<String> {
    let (width, height) = (integer(width)?, integer(height)?);
    let animated = animated
        .cast_exact::<PyBool>()
        .ok()?
        .extract::<bool>()
        .ok()?;
    let extension = extension.cast_exact::<PyString>().ok()?.to_str().ok()?;
    let suffix = if animated { "-anim" } else { "" };
    Some(format!("{width}x{height}{suffix}.{extension}"))
}

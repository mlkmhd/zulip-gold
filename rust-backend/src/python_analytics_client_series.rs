//! Display aliases and summation over already computed client chart series.

use std::borrow::Cow;
use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString};

fn alias(name: &str) -> Option<Cow<'static, str>> {
    Some(Cow::Borrowed(match name {
        "website" => "Web app",
        _ if name.starts_with("desktop app") => "Old desktop app",
        "ZulipElectron" => "Desktop app",
        "ZulipTerminal" => "Terminal app",
        "ZulipAndroid" => "Ancient Android app",
        "ZulipiOS" => "Ancient iOS app",
        "ZulipMobile" => "Old mobile app (React Native)",
        "ZulipFlutter" | "ZulipMobile/flutter" => "Mobile app (Flutter)",
        "ZulipPython" | "API: Python" => "Python API",
        _ => {
            let webhook = name.strip_prefix("Zulip")?.strip_suffix("Webhook")?;
            return Some(if webhook.is_empty() {
                Cow::Borrowed(" webhook")
            } else {
                Cow::Owned(format!("{webhook} webhook"))
            });
        }
    }))
}

struct Value<'py> {
    number: i64,
    original: Option<Bound<'py, PyInt>>,
}

fn parse_values(array: Bound<'_, PyAny>) -> Option<Vec<Value<'_>>> {
    array
        .cast_into_exact::<PyList>()
        .ok()?
        .iter()
        .map(|value| {
            let original = value.cast_into_exact::<PyInt>().ok()?;
            Some(Value {
                number: original.extract().ok()?,
                original: Some(original),
            })
        })
        .collect()
}

type Series<'py> = (Bound<'py, PyString>, Vec<Value<'py>>);

#[pyfunction]
pub(crate) fn rewrite_analytics_client_series<'py>(
    py: Python<'py>,
    arrays: &Bound<'py, PyAny>,
    stable_labels: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(arrays) = arrays.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    let Ok(stable_labels) = stable_labels.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    if stable_labels
        .iter()
        .any(|(key, _)| key.cast_exact::<PyString>().is_err())
    {
        return Ok(None);
    }
    let mut positions = HashMap::<String, usize>::new();
    let mut series = Vec::<Series<'py>>::new();
    for (key, array) in arrays.iter() {
        let Ok(original_key) = key.cast_into_exact::<PyString>() else {
            return Ok(None);
        };
        let Ok(raw) = original_key.to_str() else {
            return Ok(None);
        };
        let mapped = alias(raw);
        let name = mapped.as_deref().unwrap_or(raw);
        let Some(values) = parse_values(array) else {
            return Ok(None);
        };
        if let Some(&position) = positions.get(name) {
            let prior = &mut series[position].1;
            // Python stores the first array at full length. A longer
            // subsequent array raises IndexError; fall back for that case.
            if values.len() > prior.len() {
                return Ok(None);
            }
            for (old, added) in prior.iter_mut().zip(values) {
                let Some(sum) = old.number.checked_add(added.number) else {
                    return Ok(None);
                };
                old.number = sum;
                old.original = None;
            }
        } else {
            positions.insert(name.to_owned(), series.len());
            let output_key = match mapped {
                Some(Cow::Borrowed(fixed)) => {
                    let Some(label) = stable_labels
                        .get_item(fixed)?
                        .and_then(|value| value.cast_into_exact::<PyString>().ok())
                    else {
                        return Ok(None);
                    };
                    if label.to_str().ok() != Some(fixed) {
                        return Ok(None);
                    }
                    label
                }
                Some(Cow::Owned(dynamic)) => PyString::new(py, &dynamic),
                None => original_key,
            };
            series.push((output_key, values));
        }
    }

    let output = PyDict::new(py);
    for (key, values) in series {
        let array = PyList::empty(py);
        for value in values {
            if let Some(original) = value.original {
                array.append(original)?;
            } else {
                array.append(value.number)?;
            }
        }
        output.set_item(key, array)?;
    }
    Ok(Some(output))
}

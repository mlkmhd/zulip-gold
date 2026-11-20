use std::path::Path;

use pyo3::exceptions::{PyNameError, PyNotImplementedError, PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyString};

use crate::{markdown_tex, markdown_tex_syntax};

type TexMatch = (String, String, Option<String>, String);

#[pyfunction]
pub(crate) fn change_katex_to_raw_latex(
    fragment: &Bound<'_, PyAny>,
    context: &Bound<'_, PyDict>,
) -> PyResult<()> {
    let spans = fragment.call_method1("xpath", ("//span[@class='katex']",))?;
    for span in spans.try_iter()? {
        let span = span?;
        let text = span
            .call_method1("xpath", (".//annotation[@encoding='application/x-tex']",))?
            .get_item(0)?
            .getattr("text")?;
        let tail = span.getattr("tail")?;
        let lxml = context
            .get_item("lxml")?
            .ok_or_else(|| PyNameError::new_err("name 'lxml' is not defined"))?;
        let replacement = lxml.getattr("html")?.call_method1("Element", ("span",))?;
        replacement.setattr(
            "text",
            PyString::new(context.py(), "$${}$$").call_method1("format", (text,))?,
        )?;
        span.call_method0("getparent")?
            .call_method1("replace", (&span, &replacement))?;
        replacement.setattr("tail", tail)?;
    }
    Ok(())
}

#[pyfunction]
pub(crate) fn find_markdown_tex(
    data: Bound<'_, PyString>,
    unicode_version: &str,
) -> Option<(Option<TexMatch>,)> {
    let data = data.to_str().ok()?;
    if data.len() > 8 * 1024 * 1024 || unicode_version != markdown_tex_syntax::UNICODE_VERSION {
        return None;
    }
    Some((markdown_tex_syntax::find_tex(data).map(|matched| {
        (
            matched.before.to_owned(),
            matched.body.to_owned(),
            matched.last.map(str::to_owned),
            matched.after.to_owned(),
        )
    }),))
}

#[pyfunction]
pub(crate) fn run_katex_cli(
    py: Python<'_>,
    script: &str,
    input: &[u8],
    is_inline: bool,
) -> PyResult<(bool, Py<PyBytes>)> {
    if script.contains('\0') {
        return Err(PyValueError::new_err("embedded null byte"));
    }
    let output = py
        .detach(|| markdown_tex::run_cli(Path::new(script), input, is_inline))
        .map_err(|failure| -> PyErr {
            if failure.spawning
                && (failure.error.kind() == std::io::ErrorKind::Unsupported
                    || matches!(
                        failure.error.raw_os_error(),
                        Some(libc::ENOSYS | libc::EINVAL | libc::EPERM)
                    ))
            {
                return PyNotImplementedError::new_err(
                    "Native KaTeX process isolation is unavailable",
                );
            }
            let Some(errno) = failure.error.raw_os_error() else {
                return PyOSError::new_err(failure.error.to_string());
            };
            let message = match py
                .import("os")
                .and_then(|os| os.call_method1("strerror", (errno,)))
            {
                Ok(message) => message,
                Err(error) => return error,
            };
            if failure.spawning {
                PyOSError::new_err((errno, message.unbind(), "node"))
            } else {
                PyOSError::new_err((errno, message.unbind()))
            }
        })?;
    Ok((output.success, PyBytes::new(py, &output.stdout).unbind()))
}

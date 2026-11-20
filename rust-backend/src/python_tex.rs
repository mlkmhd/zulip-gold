use std::path::Path;

use pyo3::exceptions::{PyNotImplementedError, PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::markdown_tex;

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

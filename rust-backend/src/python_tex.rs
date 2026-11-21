use std::path::Path;

use pyo3::exceptions::{PyNameError, PyNotImplementedError, PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyString};

use crate::{markdown_tex, markdown_tex_syntax};

type TexMatch = (String, String, Option<String>, String);

fn global<'py>(context: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    context
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(format!("name '{name}' is not defined")))
}

#[pyfunction]
pub(crate) fn request_katex_service<'py>(
    tex: &Bound<'py, PyAny>,
    is_inline: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = context.py();
    let post = global(context, "KatexSession")?.call0()?.getattr("post")?;
    let port = global(context, "settings")?.getattr("KATEX_SERVER_PORT")?;
    let url = PyString::new(py, "http://localhost:{}/").call_method1("format", (port,))?;
    let data = PyDict::new(py);
    data.set_item("content", tex)?;
    data.set_item(
        "is_display",
        if is_inline.is_truthy()? {
            "false"
        } else {
            "true"
        },
    )?;
    data.set_item(
        "shared_secret",
        global(context, "settings")?.getattr("SHARED_SECRET")?,
    )?;
    let proxies = PyDict::new(py);
    // The fixed localhost destination intentionally bypasses Smokescreen.
    proxies.set_item("http", "")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("data", data)?;
    kwargs.set_item("proxies", proxies)?;
    post.call((url,), Some(&kwargs))
}

#[pyfunction]
pub(crate) fn locate_katex_cli<'py>(
    context: &Bound<'py, PyDict>,
) -> PyResult<(bool, Bound<'py, PyAny>)> {
    let path = if global(context, "settings")?
        .getattr("PRODUCTION")?
        .is_truthy()?
    {
        global(context, "static_path")?.call1(("webpack-bundles/katex-cli.js",))?
    } else {
        let join = global(context, "os")?.getattr("path")?.getattr("join")?;
        let root = global(context, "settings")?.getattr("DEPLOY_ROOT")?;
        join.call1((root, "node_modules/katex/cli.js"))?
    };
    let exists = global(context, "os")?
        .getattr("path")?
        .call_method1("isfile", (&path,))?
        .is_truthy()?;
    Ok((exists, path))
}

#[pyfunction]
pub(crate) fn select_katex_encoding<'py>(
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    if global(context, "sys")?
        .getattr("flags")?
        .getattr("utf8_mode")?
        .is_truthy()?
    {
        Ok(PyString::new(context.py(), "utf-8").into_any())
    } else {
        global(context, "locale")?.call_method0("getencoding")
    }
}

#[pyfunction]
pub(crate) fn encode_katex_input<'py>(
    tex: &Bound<'py, PyAny>,
    encoding: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if tex.is_exact_instance_of::<PyString>()
        && encoding.is_exact_instance_of::<PyString>()
        && let Ok(encoding) = encoding.cast::<PyString>()?.to_str()
        && matches!(encoding, "utf-8" | "UTF-8" | "utf8" | "UTF8")
        && let Ok(value) = tex.cast::<PyString>()?.to_str()
    {
        return Ok(PyBytes::new(tex.py(), value.as_bytes()).into_any());
    }
    tex.call_method1("encode", (encoding,))
}

#[pyfunction]
pub(crate) fn finish_katex_cli<'py>(
    success: bool,
    stdout: &Bound<'py, PyBytes>,
    encoding: &str,
) -> PyResult<Bound<'py, PyAny>> {
    let py = stdout.py();
    if matches!(encoding, "utf-8" | "UTF-8" | "utf8" | "UTF8")
        && let Ok(decoded) = std::str::from_utf8(stdout.as_bytes())
    {
        return Ok(match markdown_tex::finish_output(decoded, success) {
            Some(output) => PyString::new(py, &output).into_any(),
            None => py.None().into_bound(py),
        });
    }
    // Preserve codec-specific errors even when the process exits unsuccessfully.
    let decoded = stdout.call_method1("decode", (encoding,))?;
    let normalized = decoded
        .call_method1("replace", ("\r\n", "\n"))?
        .call_method1("replace", ("\r", "\n"))?;
    if success {
        normalized.call_method0("strip")
    } else {
        Ok(py.None().into_bound(py))
    }
}

#[pyfunction]
pub(crate) fn render_katex_response<'py>(
    response: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    if response.getattr("status_code")?.eq(200)? {
        let content = response.getattr("content")?;
        if content.is_exact_instance_of::<PyBytes>()
            && let Ok(decoded) = std::str::from_utf8(content.cast::<PyBytes>()?.as_bytes())
        {
            return Ok(PyString::new(
                context.py(),
                decoded.trim_matches(crate::draft_text::is_python_whitespace),
            )
            .into_any());
        }
        // Preserve custom decoders and CPython's exact UnicodeDecodeError.
        return content.call_method0("decode")?.call_method0("strip");
    }
    if !response.getattr("status_code")?.eq(400)? {
        let logging = context
            .get_item("logging")?
            .ok_or_else(|| PyNameError::new_err("name 'logging' is not defined"))?;
        let warning = logging.getattr("warning")?;
        let status = response.getattr("status_code")?;
        let content = response.getattr("content")?.call_method0("decode")?;
        warning.call1(("KaTeX rendering service failed: (%s) %s", status, content))?;
    }
    Ok(context.py().None().into_bound(context.py()))
}

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

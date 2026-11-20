use pyo3::exceptions::PyNameError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

fn global<'py>(context: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    context
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(format!("name '{name}' is not defined")))
}

/// Run at the host handler's original position, after any speculative plan has
/// delegated. Rendering and HTML stash writes must never be retried as fallback.
#[pyfunction]
pub(crate) fn render_markdown_inline_tex<'py>(
    pattern: &Bound<'py, PyAny>,
    matched: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = context.py();
    let renderer = global(context, "render_tex")?;
    let body = matched.call_method1("group", ("body",))?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("is_inline", true)?;
    let rendered = renderer.call((body,), Some(&kwargs))?;
    if !rendered.is_none() {
        return pattern
            .getattr("md")?
            .getattr("htmlStash")?
            .call_method1("store", (rendered,));
    }
    let span = global(context, "Element")?.call1(("span",))?;
    span.call_method1("set", ("class", "tex-error"))?;
    let atomic = global(context, "markdown")?
        .getattr("util")?
        .getattr("AtomicString")?;
    // Python string operations preserve lone surrogates and subclass behavior.
    let text = PyString::new(py, "$$")
        .add(matched.call_method1("group", ("body",))?)?
        .add("$$")?;
    span.setattr("text", atomic.call1((text,))?)?;
    Ok(span)
}

#[pyfunction]
pub(crate) fn render_markdown_display_tex<'py>(
    text: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = context.py();
    let paragraphs = text.call_method1("split", ("\n\n",))?;
    let output = PyList::empty(py);
    for paragraph in paragraphs.try_iter()? {
        let paragraph = paragraph?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("is_inline", false)?;
        let rendered = global(context, "render_tex")?.call((&paragraph,), Some(&kwargs))?;
        if !rendered.is_none() {
            output.append(rendered)?;
        } else {
            let escaped = global(context, "escape")?.call1((paragraph,))?;
            output.append(
                PyString::new(py, "<span class=\"tex-error\">")
                    .add(escaped)?
                    .add("</span>")?,
            )?;
        }
    }
    PyString::new(py, "\n\n").call_method1("join", (output,))
}

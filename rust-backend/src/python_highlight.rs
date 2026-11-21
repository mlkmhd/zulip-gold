use pyo3::exceptions::PyNameError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString, PyType};

use crate::{markdown_code, markdown_code_header};

#[pyfunction]
pub(crate) fn escape_fenced_code(source: &Bound<'_, PyString>) -> Option<String> {
    let source = source.to_str().ok()?;
    if source.len() > 8 * 1024 * 1024 {
        return None;
    }
    Some(markdown_code::escape_fenced_code(source))
}

type CodeHeader = (String, Option<(String, bool, Option<String>)>);

#[pyfunction]
pub(crate) fn parse_fenced_code_header(
    source: &Bound<'_, PyString>,
    unicode_version: &str,
) -> Option<CodeHeader> {
    let source = source.to_str().ok()?;
    if unicode_version != markdown_code_header::UNICODE_VERSION || source.len() > 8 * 1024 * 1024 {
        return None;
    }
    let header = markdown_code_header::parse_header(source).map(|header| {
        (
            header.language.to_owned(),
            header.shebang,
            header.highlight_lines.map(str::to_owned),
        )
    });
    Some((source.trim_matches('\n').to_owned(), header))
}

fn global<'py>(context: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    context
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(format!("name '{name}' is not defined")))
}

#[pyfunction]
pub(crate) fn prepare_fenced_highlight<'py>(
    processor: &Bound<'py, PyAny>,
    language: &Bound<'py, PyAny>,
    text: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<(bool, Bound<'py, PyAny>)> {
    if !processor.getattr("checked_for_codehilite")?.is_truthy()? {
        for extension in processor
            .getattr("md")?
            .getattr("registeredExtensions")?
            .try_iter()?
        {
            let extension = extension?;
            let extension_type = global(context, "CodeHiliteExtension")?;
            if extension.is_instance(&extension_type)? {
                processor.setattr("codehilite_conf", extension.getattr("config")?)?;
                break;
            }
        }
        processor.setattr("checked_for_codehilite", true)?;
    }
    if !processor.getattr("codehilite_conf")?.is_truthy()? {
        return Ok((false, processor.py().None().into_bound(processor.py())));
    }
    let constructor = global(context, "CodeHilite")?;
    let kwargs = PyDict::new(processor.py());
    for (argument, key) in [
        ("linenums", "linenums"),
        ("guess_lang", "guess_lang"),
        ("css_class", "css_class"),
        ("style", "pygments_style"),
        ("use_pygments", "use_pygments"),
    ] {
        kwargs.set_item(
            argument,
            processor
                .getattr("codehilite_conf")?
                .get_item(key)?
                .get_item(0)?,
        )?;
    }
    kwargs.set_item(
        "lang",
        if language.is_truthy()? {
            language.clone()
        } else {
            processor.py().None().into_bound(processor.py())
        },
    )?;
    kwargs.set_item(
        "noclasses",
        processor
            .getattr("codehilite_conf")?
            .get_item("noclasses")?
            .get_item(0)?,
    )?;
    // PHP snippets use the lexer without requiring an opening PHP tag.
    kwargs.set_item("startinline", true)?;
    Ok((true, constructor.call((text,), Some(&kwargs))?))
}

#[pyfunction]
pub(crate) fn finish_fenced_highlight<'py>(
    code: &Bound<'py, PyAny>,
    language: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    if !language.is_truthy()? {
        return Ok(code.clone());
    }
    let element = global(context, "lxml")?
        .getattr("html")?
        .call_method1("fromstring", (code,))?;
    let lexer = global(context, "find_lexer_class_by_name")?;
    let name = match lexer
        .call1((language,))
        .and_then(|lexer| lexer.getattr("name"))
    {
        Ok(name) => name,
        Err(error) => {
            let class_not_found = global(context, "ClassNotFound")?;
            if error.is_instance(context.py(), class_not_found.cast::<PyType>()?) {
                language.clone()
            } else {
                return Err(error);
            }
        }
    };
    element
        .getattr("attrib")?
        .set_item("data-code-language", name)?;
    let kwargs = PyDict::new(context.py());
    kwargs.set_item("encoding", "unicode")?;
    global(context, "lxml")?
        .getattr("html")?
        .call_method("tostring", (element,), Some(&kwargs))
}

use pyo3::class::basic::CompareOp;
use pyo3::exceptions::PyNameError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple, PyType};

use crate::{markdown_code, markdown_code_header};

fn fenced_line_closes(handler: &Bound<'_, PyAny>, line: &Bound<'_, PyAny>) -> PyResult<bool> {
    let candidate = line.call_method0("rstrip")?;
    let fence = handler.getattr("fence")?;
    let equal = candidate.rich_compare(&fence, CompareOp::Eq)?;
    drop(candidate);
    drop(fence);
    equal.is_truthy()
}

#[pyfunction]
pub(crate) fn handle_fenced_block_line(
    handler: &Bound<'_, PyAny>,
    line: &Bound<'_, PyAny>,
) -> PyResult<()> {
    if fenced_line_closes(handler, line)? {
        handler.call_method0("done")?;
    } else {
        // Resolve the append callback before the second rstrip, as in Python.
        let append = handler.getattr("lines")?.getattr("append")?;
        append.call1((line.call_method0("rstrip")?,))?;
    }
    Ok(())
}

#[pyfunction]
pub(crate) fn handle_nested_fenced_block_line(
    handler: &Bound<'_, PyAny>,
    line: &Bound<'_, PyAny>,
    context: &Bound<'_, PyDict>,
    inherit_language: bool,
) -> PyResult<()> {
    if fenced_line_closes(handler, line)? {
        handler.call_method0("done")?;
    } else {
        let check = global(context, "check_for_new_fence")?;
        let processor = handler.getattr("processor")?;
        let output = handler.getattr("lines")?;
        if inherit_language {
            let kwargs = PyDict::new(handler.py());
            kwargs.set_item("default_language", handler.getattr("default_language")?)?;
            check.call((processor, output, line), Some(&kwargs))?;
        } else {
            check.call1((processor, output, line))?;
        }
    }
    Ok(())
}

#[pyfunction]
pub(crate) fn dispatch_new_fenced_block<'py>(
    processor: &Bound<'py, PyAny>,
    output: &Bound<'py, PyAny>,
    line: &Bound<'py, PyAny>,
    run_validators: &Bound<'py, PyAny>,
    default_language: &Bound<'py, PyAny>,
    parser: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<()> {
    let parsed = parser.call1((line,))?;
    if parsed.is_none() {
        output.call_method1("append", (line,))?;
        return Ok(());
    }

    // The Python adapter only selects this path for its original parser, which
    // returns an ordinary three-item tuple, or None, including on old wheels.
    let parsed = parsed.cast::<PyTuple>()?;
    let fence = parsed.get_item(0)?;
    let mut language = parsed.get_item(1)?;
    let header = parsed.get_item(2)?;
    if !language.is_truthy()? && default_language.is_truthy()? {
        language = default_language.clone();
    }
    let handler = global(context, "generic_handler")?.call1((
        processor,
        output,
        fence,
        language,
        header,
        run_validators,
        default_language,
    ))?;
    processor.call_method1("push", (handler,))?;
    Ok(())
}

#[pyfunction]
pub(crate) fn finish_fenced_block(handler: &Bound<'_, PyAny>) -> PyResult<()> {
    if handler.getattr("lines")?.is_truthy()? {
        // Keep Python join/split for exact errors, string identity, subclasses,
        // and lone surrogates returned by custom host formatters.
        let text =
            PyString::new(handler.py(), "\n").call_method1("join", (handler.getattr("lines")?,))?;
        let mut text = handler.call_method1("format_text", (text,))?;
        if !handler.getattr("process_contents")?.is_truthy()? {
            text = handler
                .getattr("processor")?
                .call_method1("placeholder", (text,))?;
        }
        let lines = text.call_method1("split", ("\n",))?;
        handler.getattr("output")?.call_method1("append", ("",))?;
        handler
            .getattr("output")?
            .call_method1("extend", (lines,))?;
        handler.getattr("output")?.call_method1("append", ("",))?;
    }
    handler.getattr("processor")?.call_method0("pop")?;
    Ok(())
}

#[pyfunction]
pub(crate) fn format_fenced_spoiler<'py>(
    processor: &Bound<'py, PyAny>,
    header: Bound<'py, PyAny>,
    text: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let mut fragments = Vec::with_capacity(5);
    fragments.push(processor.call_method1(
        "placeholder",
        ("<div class=\"spoiler-block\"><div class=\"spoiler-header\">",),
    )?);
    if !header.is_none() {
        fragments.push(header);
    }
    fragments.push(processor.call_method1(
        "placeholder",
        ("</div><div class=\"spoiler-content\" aria-hidden=\"true\">",),
    )?);
    fragments.push(text);
    fragments.push(processor.call_method1("placeholder", ("</div></div>",))?);

    let py = processor.py();
    let strings: Option<Vec<_>> = fragments
        .iter()
        .map(|value| {
            value
                .is_exact_instance_of::<PyString>()
                .then(|| value.cast::<PyString>().ok()?.to_str().ok())
                .flatten()
        })
        .collect();
    if let Some(strings) = strings
        && strings.iter().map(|value| value.len()).sum::<usize>() <= 8 * 1024 * 1024
    {
        return Ok(PyString::new(py, &strings.join("\n\n")).into_any());
    }
    // Preserve the host's exact join errors and lone surrogates without
    // rerunning any of the three effectful placeholder callbacks.
    PyString::new(py, "\n\n").call_method1("join", (PyList::new(py, fragments)?,))
}

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

#[pyfunction]
pub(crate) fn normalize_fenced_code_language(language: &Bound<'_, PyString>) -> Option<String> {
    markdown_code_header::normalize_language(language.to_str().ok()?)
}

#[pyfunction]
pub(crate) fn parse_fenced_highlight_lines(
    expression: Option<&Bound<'_, PyString>>,
) -> Option<Vec<i64>> {
    let expression = match expression {
        Some(value) => Some(value.to_str().ok()?),
        None => None,
    };
    markdown_code_header::parse_highlight_lines(expression)
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

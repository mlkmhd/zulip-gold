use pyo3::class::basic::CompareOp;
use pyo3::exceptions::PyNameError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple, PyType};

use crate::markdown_blocks::BlockStep;
use crate::{markdown_code, markdown_code_header};

#[pyfunction]
pub(crate) fn find_invalid_fenced_curl_line(lines: &Bound<'_, PyAny>) -> Option<isize> {
    // None means delegate before any Python method or error is invoked.
    // -1 means validation succeeded; other values identify the first error.
    let lines = lines.cast_exact::<PyList>().ok()?;
    for (index, line) in lines.iter().enumerate() {
        let line = line.cast_exact::<PyString>().ok()?.to_str().ok()?;
        if crate::markdown_fences::curl_line_is_invalid(line) {
            return isize::try_from(index).ok();
        }
    }
    Some(-1)
}

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
#[allow(clippy::too_many_arguments)]
pub(crate) fn create_fenced_block_handler<'py>(
    processor: &Bound<'py, PyAny>,
    output: &Bound<'py, PyAny>,
    fence: &Bound<'py, PyAny>,
    lang: &Bound<'py, PyAny>,
    header: &Bound<'py, PyAny>,
    run_validators: &Bound<'py, PyAny>,
    default_language: &Bound<'py, PyAny>,
    context: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    // The caller delegates subclasses before entering, preserving overridden
    // lower and comparison behavior. Keep Python's Unicode lower result for
    // CodeHandler, including values that contain lone surrogate code points.
    let language = if lang.is_none() {
        lang.clone()
    } else {
        lang.call_method0("lower")?
    };
    let kind = language
        .cast::<PyString>()
        .ok()
        .and_then(|lang| lang.to_str().ok());
    match kind {
        Some("quote" | "quoted") => {
            global(context, "QuoteHandler")?.call1((processor, output, fence, default_language))
        }
        Some("math") => global(context, "TexHandler")?.call1((processor, output, fence)),
        Some("spoiler") => {
            global(context, "SpoilerHandler")?.call1((processor, output, fence, header))
        }
        _ => global(context, "CodeHandler")?.call1((
            processor,
            output,
            fence,
            language,
            run_validators,
        )),
    }
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
pub(crate) fn dispatch_fenced_input_lines(
    processor: &Bound<'_, PyAny>,
    lines: &Bound<'_, PyAny>,
) -> PyResult<()> {
    // Match Python's for-loop lifetime: the previous item stays live while
    // advancing the iterator, and the iterator drops before the last item.
    let mut line;
    for next in lines.try_iter()? {
        line = next?;
        let handler = processor.getattr("handlers")?.get_item(-1)?;
        let handle_line = handler.getattr("handle_line")?;
        drop(handler);
        handle_line.call1((&line,))?;
    }
    Ok(())
}

fn replay_global<'py>(
    context: &Bound<'py, PyDict>,
    name: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyAny>> {
    context
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(format!("name '{name}' is not defined")))
}

fn fenced_plan_target<'py>(
    handler: &Bound<'py, PyAny>,
    output: &Bound<'py, PyList>,
    outer: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if handler.is_instance(outer)? {
        Ok(output.clone().into_any())
    } else {
        handler.getattr(pyo3::intern!(handler.py(), "lines"))
    }
}

pub(crate) fn apply_fenced_block_steps<'py>(
    processor: &Bound<'py, PyAny>,
    output: &Bound<'py, PyList>,
    steps: Vec<BlockStep<'_>>,
    context: &Bound<'py, PyDict>,
    list_handlers: &Bound<'py, PyTuple>,
) -> PyResult<()> {
    let py = processor.py();
    let outer_name = pyo3::intern!(py, "OuterHandler");
    let handler_name = pyo3::intern!(py, "handlers");
    let standard_outer = list_handlers.get_item(0).ok();
    let mut steps = steps.into_iter().peekable();
    while let Some(step) = steps.next() {
        let handler = processor.getattr(handler_name)?.get_item(-1)?;
        match step {
            BlockStep::Close => {
                handler.call_method0(pyo3::intern!(py, "done"))?;
            }
            BlockStep::Open {
                fence,
                language,
                header,
                default_language,
            } => {
                let target =
                    fenced_plan_target(&handler, output, &replay_global(context, outer_name)?)?;
                // Bind push before the live factory and its arguments are evaluated.
                let push = processor.getattr(pyo3::intern!(py, "push"))?;
                let factory = replay_global(context, pyo3::intern!(py, "generic_handler"))?;
                let validators = if handler.is_instance(&replay_global(context, outer_name)?)? {
                    processor.getattr(pyo3::intern!(py, "run_content_validators"))?
                } else {
                    false.into_pyobject(py)?.to_owned().into_any()
                };
                let next_handler = factory.call1((
                    processor,
                    target,
                    fence,
                    language,
                    header,
                    validators,
                    default_language,
                ))?;
                push.call1((next_handler,))?;
            }
            BlockStep::Line(line) => {
                let outer = replay_global(context, outer_name)?;
                let target = fenced_plan_target(&handler, output, &outer)?;
                // Only plain lists on known handlers cannot change the stack
                // during append. Otherwise the next step must read it afresh.
                if matches!(steps.peek(), Some(BlockStep::Line(_)))
                    && target.is_exact_instance_of::<PyList>()
                    && standard_outer.as_ref().is_some_and(|class| outer.is(class))
                    && {
                        let handler_class = handler.get_type();
                        list_handlers.iter().any(|class| handler_class.is(&class))
                    }
                {
                    let mut lines = vec![line];
                    while let Some(BlockStep::Line(next)) =
                        steps.next_if(|step| matches!(step, BlockStep::Line(_)))
                    {
                        lines.push(next);
                    }
                    target.call_method1(pyo3::intern!(py, "extend"), (PyList::new(py, lines)?,))?;
                } else {
                    target.call_method1(pyo3::intern!(py, "append"), (line,))?;
                }
            }
        }
    }
    Ok(())
}

#[pyfunction]
pub(crate) fn finish_fenced_input(
    processor: &Bound<'_, PyAny>,
    output: &Bound<'_, PyAny>,
) -> PyResult<()> {
    while processor.getattr("handlers")?.is_truthy()? {
        // The previous done callback can change the live stack.
        processor
            .getattr("handlers")?
            .get_item(-1)?
            .call_method0("done")?;
    }
    if output.len()? > 2
        && output
            .get_item(-2)?
            .rich_compare("", CompareOp::Ne)?
            .is_truthy()?
    {
        output.call_method1("append", ("",))?;
    }
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

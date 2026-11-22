use std::borrow::Cow;
use std::sync::LazyLock;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString, PyTuple};
use regex::Regex;

const MARKER: &str = r#"class="message-reply-link""#;

static LINK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?s)<a class="message-reply-link" (href="[^"]*")>(.*?)</a>"#).unwrap()
});
static LABEL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?s)\A<strong class="message-reply-sender" dir="auto">(.*?)</strong><span class="message-reply-excerpt" dir="auto">(.*?)</span>\z"#,
    )
    .unwrap()
});

fn is_mobile_client(client_name: &str) -> bool {
    matches!(
        client_name,
        "ZulipFlutter" | "ZulipMobile" | "ZulipAndroid" | "ZulipiOS"
    )
}

fn render(html: &str) -> Cow<'_, str> {
    if !html.contains(MARKER) {
        return Cow::Borrowed(html);
    }
    LINK.replace_all(html, |link: &regex::Captures<'_>| {
        let href = &link[1];
        let label = &link[2];
        if let Some(parts) = LABEL.captures(label) {
            format!("<a {href}>{} · {}</a>", &parts[1], &parts[2])
        } else {
            format!("<a {href}>{label}</a>")
        }
    })
}

#[pyfunction]
#[pyo3(signature = (client_name))]
pub(crate) fn mobile_client_needs_plain_reply_links(
    client_name: Option<&Bound<'_, PyAny>>,
) -> Option<bool> {
    let Some(client_name) = client_name else {
        return Some(false);
    };
    Some(is_mobile_client(
        client_name.cast_exact::<PyString>().ok()?.to_str().ok()?,
    ))
}

#[pyfunction]
pub(crate) fn render_mobile_plain_reply_links<'py>(
    py: Python<'py>,
    html: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let html = html.cast_exact::<PyString>().ok()?;
    Some(match render(html.to_str().ok()?) {
        Cow::Borrowed(_) => html.clone(),
        Cow::Owned(result) => PyString::new(py, &result),
    })
}

#[pyfunction]
pub(crate) fn mobile_plain_reply_link_fields<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    fields: &Bound<'py, PyTuple>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(data) = data.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    // Looking up an exact str can still invoke another key's __eq__.
    // Do not attempt lookups we might need to repeat on fallback.
    if !data
        .keys()
        .iter()
        .all(|key| key.is_exact_instance_of::<PyString>())
        || !fields
            .iter()
            .all(|field| field.is_exact_instance_of::<PyString>())
    {
        return Ok(None);
    }
    let result = PyDict::new(py);
    for field in fields.iter() {
        let Some(value) = data.get_item(&field)? else {
            continue;
        };
        if value.is_none() {
            continue;
        }
        let Some(html) = value
            .cast_exact::<PyString>()
            .ok()
            .and_then(|string| string.to_str().ok())
        else {
            return Ok(None);
        };
        if html.contains(MARKER) {
            match render(html) {
                Cow::Borrowed(_) => result.set_item(field, value)?,
                Cow::Owned(rendered) => result.set_item(field, rendered)?,
            }
        }
    }
    Ok(Some(result))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_mobile_clients_are_exact() {
        for client in ["ZulipFlutter", "ZulipMobile", "ZulipAndroid", "ZulipiOS"] {
            assert!(is_mobile_client(client));
        }
        for client in ["", "zulipflutter", "ZulipFlutter/1.0", "ZulipDesktop"] {
            assert!(!is_mobile_client(client));
        }
    }

    #[test]
    fn matching_replacement_preserves_other_html() {
        let input = concat!(
            r##"<p><a class="message-reply-link" href="#topic&amp;near">"##,
            r#"<strong class="message-reply-sender" dir="auto">A &amp; B</strong>"#,
            r#"<span class="message-reply-excerpt" dir="auto"><mark>سلام</mark>"#,
            "\n",
            r#"world</span></a><a href="/ordinary">Other</a></p>"#,
        );
        assert_eq!(
            render(input),
            "<p><a href=\"#topic&amp;near\">A &amp; B · <mark>سلام</mark>\nworld</a><a href=\"/ordinary\">Other</a></p>"
        );
    }

    #[test]
    fn parses_only_exact_attributes_and_uses_plain_label_as_is() {
        let input = concat!(
            r#"<a class="message-reply-link" href="/one">plain</a>"#,
            r#"<a href="/ignored" class="message-reply-link">leave intact</a>"#,
            r#"<a class="message-reply-link" href="/two"></a>"#,
        );
        assert_eq!(
            render(input),
            concat!(
                r#"<a href="/one">plain</a>"#,
                r#"<a href="/ignored" class="message-reply-link">leave intact</a>"#,
                r#"<a href="/two"></a>"#,
            )
        );
        assert!(matches!(render("<p>ordinary</p>"), Cow::Borrowed(_)));
    }
}

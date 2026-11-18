use std::sync::LazyLock;

use regex::Regex;

static FENCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"\A(?P<fence>~{3,}|`{3,}) *(?:\{?\.?(?P<lang>[a-zA-Z0-9_+-./#]+) *(?P<header>[^ ~`][^~`]*)?\}?)?(?:\n)?\z",
    )
    .expect("valid fenced block header pattern")
});

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct FenceHeader<'a> {
    pub fence: &'a str,
    pub language: Option<&'a str>,
    pub header: Option<&'a str>,
}

pub(crate) fn parse_fence_line(line: &str) -> Option<FenceHeader<'_>> {
    let captures = FENCE.captures(line)?;
    Some(FenceHeader {
        fence: captures.name("fence")?.as_str(),
        language: captures.name("lang").map(|value| value.as_str()),
        header: captures.name("header").map(|value| value.as_str()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_language_and_header_capture_boundaries() {
        for (line, fence, language, header) in [
            ("~~~ .py", "~~~", Some("py"), None),
            ("```{python}", "```", Some("python"), Some("}")),
            ("```lang,part", "```", Some("lang,part"), None),
            (
                "```spoiler Header\n",
                "```",
                Some("spoiler"),
                Some("Header\n"),
            ),
            ("```\n", "```", None, None),
            ("```python\t", "```", Some("python"), Some("\t")),
        ] {
            assert_eq!(
                parse_fence_line(line),
                Some(FenceHeader {
                    fence,
                    language,
                    header
                }),
                "{line:?}",
            );
        }
    }

    #[test]
    fn rejects_indented_and_invalid_fences() {
        for line in ["``", " ```", "```quote~", "```\tpy", "```ش", "~~~`"] {
            assert_eq!(parse_fence_line(line), None, "{line:?}");
        }
    }
}

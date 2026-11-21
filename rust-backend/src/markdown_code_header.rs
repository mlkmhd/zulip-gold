use std::sync::OnceLock;

use regex::Regex;

pub use crate::markdown_autolink_data::UNICODE_VERSION;
use crate::markdown_autolink_data::WORD;

#[derive(Debug, PartialEq, Eq)]
pub struct Header<'a> {
    pub language: &'a str,
    pub shebang: bool,
    pub highlight_lines: Option<&'a str>,
}

pub fn parse_header(source: &str) -> Option<Header<'_>> {
    static HEADER: OnceLock<Regex> = OnceLock::new();
    let pattern = HEADER.get_or_init(|| {
        Regex::new(&format!(
            concat!(
                r"\A(?:::+|(?P<shebang>\#!))",
                r"(?:(?:/[{word}]+)*[/ ])?",
                r"(?P<lang>[{word}#.+\-]*)",
                r"[\x09-\x0d\x1c-\x20\x85\xa0\x{{1680}}\x{{2000}}-\x{{200a}}\x{{2028}}\x{{2029}}\x{{202f}}\x{{205f}}\x{{3000}}]*",
                r#"(?:hl_lines=(?:"(?P<double>.*?)"|'(?P<single>.*?)'))?"#,
            ),
            word = WORD,
        ))
        .expect("code header pattern")
    });
    let first_line = source.split('\n').next().unwrap();
    let matched = pattern.captures(first_line)?;
    Some(Header {
        language: matched.name("lang").unwrap().as_str(),
        shebang: matched.name("shebang").is_some(),
        highlight_lines: matched
            .name("double")
            .or_else(|| matched.name("single"))
            .map(|value| value.as_str()),
    })
}

pub fn normalize_language(language: &str) -> Option<String> {
    language.is_ascii().then(|| language.to_ascii_lowercase())
}

/// None delegates arbitrary-precision integers and non-ASCII syntax to the host.
pub fn parse_highlight_lines(expression: Option<&str>) -> Option<Vec<i64>> {
    let Some(expression) = expression else {
        return Some(Vec::new());
    };
    if !expression.is_ascii() || expression.len() > 8 * 1024 * 1024 {
        return None;
    }
    let mut lines = Vec::new();
    for token in expression.split(crate::draft_text::is_python_whitespace) {
        if token.is_empty() {
            continue;
        }
        // CPython permits a configurable integer digit limit of at least 640.
        if token.len() >= 640 {
            return None;
        }
        let digits = token.strip_prefix(['+', '-']).unwrap_or(token);
        if digits.is_empty()
            || !digits.as_bytes()[0].is_ascii_digit()
            || !digits.as_bytes()[digits.len() - 1].is_ascii_digit()
            || digits.contains("__")
            || !digits
                .bytes()
                .all(|byte| byte.is_ascii_digit() || byte == b'_')
        {
            return Some(Vec::new());
        }
        lines.push(token.replace('_', "").parse().ok()?);
    }
    Some(lines)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_ascii_languages_only() {
        assert_eq!(
            normalize_language("C++PYTHON"),
            Some("c++python".to_owned())
        );
        assert_eq!(normalize_language("İΣ"), None);
    }

    #[test]
    fn parses_bounded_ascii_line_numbers() {
        assert_eq!(parse_highlight_lines(None), Some(vec![]));
        assert_eq!(parse_highlight_lines(Some("")), Some(vec![]));
        assert_eq!(
            parse_highlight_lines(Some("+1 -2 3_0\x1c0 -0 0002")),
            Some(vec![1, -2, 30, 0, 0, 2])
        );
        for expression in ["1 bad 3", "1__0", "_1", "1_", "+", "0x10", "1.0"] {
            assert_eq!(parse_highlight_lines(Some(expression)), Some(vec![]));
        }
        for expression in ["۱ ٢", "9223372036854775808", "-9223372036854775809"] {
            assert_eq!(parse_highlight_lines(Some(expression)), None);
        }
        assert_eq!(
            parse_highlight_lines(Some("9223372036854775807 -9223372036854775808")),
            Some(vec![i64::MAX, i64::MIN])
        );
        assert_eq!(parse_highlight_lines(Some(&"0".repeat(640))), None);
    }

    #[test]
    fn preserves_shebang_path_and_first_line() {
        for (source, language, shebang) in [
            ("#!/usr/bin/Python\nprint(1)", "Python", true),
            ("#!/usr/bin/env python", "python", true),
            (":::#C++", "#C++", false),
            ("#!", "", true),
            (":: /سلام/زبان", "", false),
            ("#!/سلام/زبان", "زبان", true),
            (":::²\u{301}x", "²", false),
        ] {
            let header = parse_header(source).unwrap();
            assert_eq!(header.language, language, "{source:?}");
            assert_eq!(header.shebang, shebang, "{source:?}");
        }
        for source in ["", ":python", " #!python", "\n#!python", "x\n::python"] {
            assert_eq!(parse_header(source), None, "{source:?}");
        }
    }

    #[test]
    fn keeps_quote_delimiters_and_python_whitespace() {
        for (source, lines) in [
            (":::python hl_lines='1 2' trailing", Some("1 2")),
            ("#!python\u{1c}hl_lines=\"۱ ۲\"", Some("۱ ۲")),
            (":::python hl_lines=\"1'2\"", Some("1'2")),
            (":::python hl_lines='1\"2'", Some("1\"2")),
            (":::python hl_lines=''", Some("")),
            (":::python hl_lines='1\n2'", None),
            (":::python hl_lines='1\r2'", Some("1\r2")),
            (":::python hl_lines='1\"", None),
            (":::python\u{200b}hl_lines='1'", None),
        ] {
            assert_eq!(
                parse_header(source).unwrap().highlight_lines,
                lines,
                "{source:?}"
            );
        }
    }
}

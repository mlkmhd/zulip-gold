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

#[cfg(test)]
mod tests {
    use super::*;

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

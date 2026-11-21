use std::sync::LazyLock;

use regex::{Captures, Regex};

use crate::draft_text::is_python_whitespace;

static QUOTE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(^|\n)[ ]{0,3}>[ ]?(.*)").expect("valid quote pattern"));

// Python's whitespace class also includes the four ASCII information separators.
static EMPTY_PREFIX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\A(?:[ ]{0,3}>[\s\x1c-\x1f]*(?:\z|\n))*(?:\z|\n)")
        .expect("valid empty quote pattern")
});

fn find_quote(block: &str) -> Option<Captures<'_>> {
    let mut offset = 0;
    loop {
        let captures = QUOTE.captures_at(block, offset)?;
        let line_start = captures.get(1).expect("quote boundary").end();
        if let Some(empty) = EMPTY_PREFIX.find(&block[line_start..]) {
            let end = line_start + empty.end();
            // Keep the final newline available as the next match's boundary.
            offset = end - usize::from(block.as_bytes()[end - 1] == b'\n');
        } else {
            return Some(captures);
        }
    }
}

pub(crate) fn split_quote(block: &str) -> Option<(&str, &str)> {
    let captures = find_quote(block)?;
    let start = captures.get(0).expect("quote match").start();
    Some((&block[..start], &block[start..]))
}

pub(crate) fn strip_quote_marker(line: &str) -> &str {
    if line.trim_matches(is_python_whitespace) == ">" {
        return "";
    }
    match find_quote(line) {
        Some(captures) if captures.get(0).expect("quote match").start() == 0 => {
            captures.get(2).expect("quote content").as_str()
        }
        _ => line,
    }
}

pub(crate) fn format_fenced_quote(text: &str) -> Option<String> {
    const MAX_BYTES: usize = 8 * 1024 * 1024;
    if text.len() >= MAX_BYTES {
        return None;
    }
    let lines = text.bytes().filter(|&byte| byte == b'\n').count() + 1;
    let capacity = text.len() + 2 * lines;
    if capacity > MAX_BYTES {
        return None;
    }
    let mut quoted = String::with_capacity(capacity);
    for (index, line) in text.split('\n').enumerate() {
        if index != 0 {
            quoted.push('\n');
        }
        quoted.push_str("> ");
        quoted.push_str(line);
    }
    Some(quoted)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_quotes_without_consuming_the_line_boundary() {
        assert_eq!(
            split_quote("before\n > عنوان\ncontinuation"),
            Some(("before", "\n > عنوان\ncontinuation"))
        );
        assert_eq!(split_quote("    > indented"), None);
        assert_eq!(strip_quote_marker("   > > nested"), "> nested");
        assert_eq!(strip_quote_marker(">\ttext"), "\ttext");
        assert_eq!(strip_quote_marker("\u{1c}>\u{85}"), "");
    }

    #[test]
    fn skips_empty_quotes_but_keeps_lazy_continuations() {
        for block in [">", ">\n>", ">\n\ntext", ">\u{1f}\n>\n\ntext"] {
            assert_eq!(split_quote(block), None, "{block:?}");
        }
        assert_eq!(split_quote(">\ntext"), Some(("", ">\ntext")));
        assert_eq!(split_quote(">\n> text"), Some(("", ">\n> text")));
        assert_eq!(split_quote(">\n\n> text"), Some((">\n", "\n> text")));
        assert_eq!(strip_quote_marker("before\n> text"), "before\n> text");
        assert_eq!(strip_quote_marker("> first\n> second"), "first");
    }

    #[test]
    fn skips_long_runs_of_empty_quotes() {
        let block = format!("{}\n> text", ">\n".repeat(10_000));
        let (before, after) = split_quote(&block).unwrap();
        assert_eq!(before, ">\n".repeat(10_000));
        assert_eq!(after, "\n> text");
    }
}

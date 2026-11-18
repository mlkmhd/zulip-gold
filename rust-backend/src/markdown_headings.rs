use std::sync::LazyLock;

use regex::Regex;

use crate::draft_text::is_python_whitespace;

// Python's whitespace class also includes the four ASCII information separators.
static HEADING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:^|\n)(#{1,6})[\s\x1c-\x1f]((?:\\[^\n]|[^\\])*?)#*(?:\n|$)")
        .expect("valid heading pattern")
});

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Heading<'a> {
    pub level: usize,
    pub text: &'a str,
    pub before: &'a str,
    pub after: &'a str,
}

pub(crate) fn parse_heading(block: &str) -> Option<Heading<'_>> {
    let captures = HEADING.captures(block)?;
    let matched = captures.get(0).expect("heading match");
    Some(Heading {
        level: captures[1].len(),
        text: captures
            .get(2)
            .expect("heading text")
            .as_str()
            .trim_matches(is_python_whitespace),
        before: &block[..matched.start()],
        after: &block[matched.end()..],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_surrounding_blocks_and_strips_closing_hashes() {
        assert_eq!(
            parse_heading("before\n### عنوان 📝 ###\nafter"),
            Some(Heading {
                level: 3,
                text: "عنوان 📝",
                before: "before",
                after: "after",
            })
        );
        assert_eq!(parse_heading("####### title"), None);
        assert_eq!(parse_heading("#title"), None);
        assert_eq!(parse_heading(" # title"), None);
    }

    #[test]
    fn preserves_escaped_hashes_and_newline_separator_semantics() {
        assert_eq!(parse_heading("# title\\#").unwrap().text, "title\\#");
        assert_eq!(parse_heading("#\nnext").unwrap().text, "next");
        assert_eq!(parse_heading("# broken\\\n## valid").unwrap().level, 2);
        assert_eq!(
            parse_heading("#\u{1c}\u{1f}title\u{1f}###").unwrap().text,
            "title"
        );
    }
}

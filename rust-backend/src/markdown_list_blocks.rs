use std::sync::LazyLock;

use regex::Regex;

use crate::markdown_lists::is_decimal_digit_after_unicode_13;

static ORDERED_ITEM: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\A {0,1}(\d+)\. +(.*)").expect("valid ordered item pattern"));
static UNORDERED_ITEM: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\A {0,1}([*+-]) +(.*)").expect("valid unordered item pattern"));
static INDENTED_ITEM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\A {2,3}(?:\d+\.|[*+-]) +").expect("valid indented item pattern")
});

fn item_pattern(block: &str, ordered: bool) -> Option<&'static Regex> {
    if block.chars().any(is_decimal_digit_after_unicode_13) {
        return None;
    }
    Some(if ordered {
        &ORDERED_ITEM
    } else {
        &UNORDERED_ITEM
    })
}

pub(crate) fn starts_list(block: &str, ordered: bool) -> Option<bool> {
    Some(item_pattern(block, ordered)?.is_match(block))
}

pub(crate) fn split_items(block: &str, ordered: bool) -> Option<(Vec<String>, Option<String>)> {
    let pattern = item_pattern(block, ordered)?;
    let mut items: Vec<String> = Vec::new();
    let mut start = None;
    for line in block.split('\n') {
        if let Some(captures) = pattern.captures(line) {
            if items.is_empty() && ordered {
                start = Some(captures[1].to_owned());
            }
            items.push(captures[2].to_owned());
        } else {
            let previous = items.last_mut()?;
            if INDENTED_ITEM.is_match(line) && !previous.starts_with("  ") {
                items.push(line.to_owned());
            } else {
                previous.push('\n');
                previous.push_str(line);
            }
        }
    }
    Some((items, start))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_numbered_markers_and_lazy_continuations() {
        assert_eq!(
            split_items("۳. عنوان\ncontinuation\n۴. next", true),
            Some((
                vec!["عنوان\ncontinuation".to_owned(), "next".to_owned()],
                Some("۳".to_owned())
            ))
        );
        assert_eq!(starts_list("  1. nested", true), Some(false));
        assert_eq!(starts_list("12. item", true), Some(true));
        assert_eq!(starts_list("². text", true), Some(false));
        assert_eq!(starts_list("*\ttext", false), Some(false));
        assert_eq!(split_items("plain", false), None);
    }

    #[test]
    fn groups_nested_items_and_keeps_list_types_separate() {
        assert_eq!(
            split_items(
                "* first\n  * nested\n  1. numbered\n* last\n1. continuation",
                false
            ),
            Some((
                vec![
                    "first".to_owned(),
                    "  * nested\n  1. numbered".to_owned(),
                    "last\n1. continuation".to_owned(),
                ],
                None
            ))
        );
        assert_eq!(split_items("  * no parent", false), None);
    }

    #[test]
    fn delegates_recent_decimal_digits() {
        for character in ['\u{11f50}', '\u{16ac0}', '\u{10d40}'] {
            let block = format!("* first\n  {character}. nested");
            assert_eq!(starts_list(&block, false), None);
            assert_eq!(split_items(&block, false), None);
        }
    }
}

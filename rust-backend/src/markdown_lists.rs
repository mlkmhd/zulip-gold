use std::sync::LazyLock;

use regex::Regex;

use crate::markdown_fences::parse_fence_line;

static LIST_ITEM: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\A *([*+-]|\d\.) +").expect("valid list item pattern"));

fn is_numbered_list_item(line: &str) -> Result<Option<bool>, ()> {
    let Some(captures) = LIST_ITEM.captures(line) else {
        return Ok(None);
    };
    // These decimal digits postdate Python 3.12's Unicode 15.0 database.
    // Delegate them so the active Python runtime decides their list semantics.
    if matches!(
        captures[1].chars().next().expect("list marker"),
        '\u{10d40}'..='\u{10d49}' | '\u{116d0}'..='\u{116e3}'
            | '\u{11bf0}'..='\u{11bf9}' | '\u{16130}'..='\u{16139}'
            | '\u{16d70}'..='\u{16d79}' | '\u{1ccf0}'..='\u{1ccf9}'
            | '\u{1e5f1}'..='\u{1e5fa}'
    ) {
        return Err(());
    }
    Ok(Some(captures[1].ends_with('.')))
}

pub(crate) fn separator_positions(lines: &[&str]) -> Option<Vec<usize>> {
    let mut separators = Vec::new();
    let mut open_fences = Vec::new();
    let mut code_fences = 0;
    for (index, pair) in lines.windows(2).enumerate() {
        if let Some(parsed) = parse_fence_line(pair[0]) {
            if parsed.language.is_none()
                && open_fences
                    .last()
                    .is_some_and(|&(fence, _)| fence == parsed.fence)
            {
                let (_, is_code) = open_fences.pop().expect("matching open fence");
                code_fences -= usize::from(is_code);
            } else {
                let is_code = !matches!(parsed.language, Some("quote" | "quoted"));
                open_fences.push((parsed.fence, is_code));
                code_fences += usize::from(is_code);
            }
        }

        if code_fences == 0 && !pair[0].is_empty() {
            let current = is_numbered_list_item(pair[0]).ok()?;
            if let Some(next) = is_numbered_list_item(pair[1]).ok()?
                && current != Some(next)
            {
                separators.push(index + 1);
            }
        }
    }
    Some(separators)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn separates_paragraphs_and_different_list_types() {
        assert_eq!(
            separator_positions(&["intro", "* one", "+ two", "۳. three", "4. four", "- five"]),
            Some(vec![1, 3, 5]),
        );
        assert_eq!(separator_positions(&["", "* one"]), Some(Vec::new()));
        assert_eq!(separator_positions(&["intro", "12. two"]), Some(Vec::new()));
        assert_eq!(separator_positions(&["intro", "². two"]), Some(Vec::new()));
        assert_eq!(separator_positions(&["intro", "*\tone"]), Some(Vec::new()));
    }

    #[test]
    fn keeps_code_ancestors_even_inside_a_nested_quote() {
        assert_eq!(
            separator_positions(&[
                "```quote", "intro", "* one", "~~~py", "```quote", "text", "* two", "```", "~~~",
                "text", "* three", "```", "* four",
            ]),
            Some(vec![2, 10, 12]),
        );
        assert_eq!(
            separator_positions(&["```QUOTE", "* one"]),
            Some(Vec::new())
        );
    }

    #[test]
    fn delegates_decimal_markers_added_after_the_python_baseline() {
        for character in [
            '\u{10d40}',
            '\u{116d0}',
            '\u{11bf0}',
            '\u{16130}',
            '\u{16d70}',
            '\u{1ccf0}',
            '\u{1e5f1}',
        ] {
            assert_eq!(
                separator_positions(&["intro", &format!("{character}. item")]),
                None
            );
        }
    }
}

use crate::draft_text::is_python_whitespace;
use crate::markdown_code::escape_code;

pub(crate) fn plan_code_block(
    block: &str,
    indentation: usize,
    continuing: bool,
    previous: Option<&str>,
) -> (String, String) {
    let lines: Vec<_> = block.split('\n').collect();
    let mut code = Vec::new();
    for line in &lines {
        if line.len() >= indentation && line.bytes().take(indentation).all(|byte| byte == b' ') {
            code.push(&line[indentation..]);
        } else if line.chars().all(is_python_whitespace) {
            code.push("");
        } else {
            break;
        }
    }
    let remainder = lines[code.len()..].join("\n");
    let code = code.join("\n");
    let escaped = escape_code(code.trim_end_matches(is_python_whitespace));
    let text = if continuing {
        format!("{}\n{escaped}\n", previous.unwrap_or("None"))
    } else {
        format!("{escaped}\n")
    };
    (text, remainder)
}

pub(crate) fn plan_empty_block<'a>(
    block: &'a str,
    continuing: bool,
    previous: Option<&str>,
) -> (Option<String>, &'a str) {
    let (remainder, filler) = if let Some(first) = block.chars().next() {
        (&block[first.len_utf8()..], "\n")
    } else {
        ("", "\n\n")
    };
    (
        continuing.then(|| format!("{}{filler}", previous.unwrap_or("None"))),
        remainder,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_and_splits_code_before_dedented_text() {
        assert_eq!(
            plan_code_block(
                "  <&> عنوان\n\u{1c}\n  next \u{85}\nnormal\n  later",
                2,
                false,
                None
            ),
            (
                "&lt;&amp;&gt; عنوان\n\nnext\n".to_owned(),
                "normal\n  later".to_owned()
            )
        );
        assert_eq!(
            plan_code_block("  text\n", 2, true, Some("old\n")).0,
            "old\n\ntext\n"
        );
        assert_eq!(plan_code_block("  text", 2, true, None).0, "None\ntext\n");
    }

    #[test]
    fn handles_blank_blocks_and_nonstandard_indentation() {
        assert_eq!(
            plan_code_block("  \n\u{1f}\n  ", 2, false, None),
            ("\n".to_owned(), String::new())
        );
        assert_eq!(
            plan_code_block("<&>\nnext", 0, false, None).0,
            "&lt;&amp;&gt;\nnext\n"
        );
        assert_eq!(
            plan_code_block("  text", usize::MAX, false, None),
            ("\n".to_owned(), "  text".to_owned())
        );
    }

    #[test]
    fn restores_blank_lines_only_for_code_siblings() {
        assert_eq!(
            plan_empty_block("", true, Some("text\n")),
            (Some("text\n\n\n".to_owned()), "")
        );
        assert_eq!(
            plan_empty_block("\n\n  next", true, None),
            (Some("None\n".to_owned()), "\n  next")
        );
        assert_eq!(plan_empty_block("\ntext", false, None), (None, "text"));
    }
}

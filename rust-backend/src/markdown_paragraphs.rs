use crate::draft_text::is_python_whitespace;

pub(crate) fn plan_paragraph(
    block: &str,
    tight_list: bool,
    has_sibling: bool,
    previous_text: Option<&str>,
) -> (&'static str, String) {
    if block.trim_matches(is_python_whitespace).is_empty() {
        return ("empty", String::new());
    }
    if !tight_list {
        return (
            "paragraph",
            block.trim_start_matches(is_python_whitespace).to_owned(),
        );
    }
    let text = match previous_text.filter(|text| !text.is_empty()) {
        Some(previous) => format!("{previous}\n{block}"),
        None if has_sibling => format!("\n{block}"),
        None => block.trim_start_matches(is_python_whitespace).to_owned(),
    };
    (if has_sibling { "tail" } else { "parent" }, text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn drops_blank_blocks_and_trims_python_whitespace() {
        assert_eq!(
            plan_paragraph("\u{1c}\u{85}\n", false, false, None).0,
            "empty"
        );
        assert_eq!(
            plan_paragraph("\u{1f} عنوان  ", false, false, None),
            ("paragraph", "عنوان  ".to_owned())
        );
    }

    #[test]
    fn appends_tight_list_text_without_trimming() {
        assert_eq!(
            plan_paragraph("  next", true, false, Some("before")),
            ("parent", "before\n  next".to_owned())
        );
        assert_eq!(
            plan_paragraph("  next", true, true, Some("before")),
            ("tail", "before\n  next".to_owned())
        );
        assert_eq!(
            plan_paragraph("  next", true, true, None),
            ("tail", "\n  next".to_owned())
        );
        assert_eq!(
            plan_paragraph("  next", true, false, Some("")),
            ("parent", "next".to_owned())
        );
    }
}

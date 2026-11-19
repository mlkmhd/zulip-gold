use crate::markdown_list_indent::{indent_parent, loose_detab};

pub(crate) fn plan_list_indentation(
    block: &str,
    tight_list: bool,
    nodes: &[(&str, bool)],
) -> Option<(usize, &'static str, bool, String)> {
    let &(parent_tag, _) = nodes.first()?;
    let tags = nodes[1..].iter().map(|&(tag, _)| tag).collect::<Vec<_>>();
    let (level, steps) = indent_parent(block, tight_list, &tags);
    let (target, method, wrap_text) = if parent_tag == "li" {
        let child_list = nodes
            .get(1)
            .is_some_and(|&(tag, _)| matches!(tag, "ol" | "ul"));
        (usize::from(child_list), "blocks", false)
    } else if nodes[steps].0 == "li" {
        (steps, "blocks", false)
    } else if let Some(&("li", has_text)) = nodes.get(steps + 1) {
        (steps + 1, "chunk", has_text)
    } else {
        (steps, "new", false)
    };
    Some((target, method, wrap_text, loose_detab(block, level)?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chooses_existing_item_and_child_lists() {
        assert_eq!(
            plan_list_indentation(
                "  nested",
                false,
                &[("li", true), ("ul", false), ("li", false)]
            ),
            Some((1, "blocks", false, "nested".into()))
        );
        assert_eq!(
            plan_list_indentation("  nested", true, &[("li", true), ("p", false)]),
            Some((0, "blocks", false, "nested".into()))
        );
        assert_eq!(
            plan_list_indentation("  nested", false, &[("div", false), ("li", true)]),
            Some((1, "blocks", false, "  nested".into()))
        );
    }

    #[test]
    fn wraps_chunk_text_or_creates_an_item() {
        assert_eq!(
            plan_list_indentation(
                "  nested",
                false,
                &[("div", false), ("ul", false), ("li", true)]
            ),
            Some((2, "chunk", true, "nested".into()))
        );
        assert_eq!(
            plan_list_indentation("  nested", false, &[("div", false), ("ul", false)]),
            Some((1, "new", false, "nested".into()))
        );
        assert_eq!(plan_list_indentation("  nested", false, &[]), None);
    }
}

use crate::draft_text::is_python_whitespace;
use crate::markdown_list_blocks::split_items;

pub(crate) struct ListContext<'a> {
    pub ordered: bool,
    pub lazy: bool,
    pub parent_tag: &'a str,
    pub sibling_tag: Option<&'a str>,
    pub previous_text: Option<&'a str>,
    pub previous_tail: Option<&'a str>,
}

pub(crate) type ListPlan = (
    &'static str,
    Option<String>,
    Vec<(String, bool)>,
    Option<String>,
    Option<String>,
    bool,
    Option<String>,
);

pub(crate) fn plan_list(block: &str, context: ListContext<'_>) -> Option<ListPlan> {
    let (mut items, number) = split_items(block, context.ordered)?;
    let tag = if context.ordered { "ol" } else { "ul" };
    let target = if context.sibling_tag == Some(tag) {
        "sibling"
    } else if matches!(context.parent_tag, "ol" | "ul") {
        "parent"
    } else {
        "new"
    };
    let first = (target == "sibling").then(|| items.remove(0));
    let start = if target == "new" && !context.lazy {
        number.as_ref().filter(|number| *number != "1").cloned()
    } else {
        None
    };
    let wrap_text =
        target == "sibling" && context.previous_text.is_some_and(|text| !text.is_empty());
    let tail = if target == "sibling" {
        context
            .previous_tail
            .filter(|tail| !tail.is_empty())
            .map(|tail| tail.trim_start_matches(is_python_whitespace).to_owned())
    } else {
        None
    };
    let items = items
        .into_iter()
        .map(|item| {
            let indented = item.starts_with("  ");
            (item, indented)
        })
        .collect();
    Some((target, first, items, number, start, wrap_text, tail))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context() -> ListContext<'static> {
        ListContext {
            ordered: true,
            lazy: false,
            parent_tag: "div",
            sibling_tag: None,
            previous_text: None,
            previous_tail: None,
        }
    }

    #[test]
    fn preserves_start_strings_and_nested_item_actions() {
        let (target, first, items, number, start, wrap, tail) =
            plan_list("۰۰۳. first\n  * nested\n۴. last", context()).unwrap();
        assert_eq!(target, "new");
        assert_eq!(first, None);
        assert_eq!(
            items,
            [
                ("first".into(), false),
                ("  * nested".into(), true),
                ("last".into(), false)
            ]
        );
        assert_eq!(number.as_deref(), Some("۰۰۳"));
        assert_eq!(start, number);
        assert!(!wrap);
        assert_eq!(tail, None);
        assert_eq!(plan_list("1. first", context()).unwrap().4, None);
        assert_eq!(
            plan_list("001. first", context()).unwrap().4.as_deref(),
            Some("001")
        );
    }

    #[test]
    fn continues_only_the_matching_sibling_and_wraps_existing_content() {
        let mut context = context();
        context.parent_tag = "ul";
        context.sibling_tag = Some("ol");
        context.previous_text = Some("previous");
        context.previous_tail = Some("\u{1f}\t tail");
        let plan = plan_list("3. first\n4. last", context).unwrap();
        assert_eq!(plan.0, "sibling");
        assert_eq!(plan.1.as_deref(), Some("first"));
        assert_eq!(plan.2, [("last".into(), false)]);
        assert_eq!(plan.4, None);
        assert!(plan.5);
        assert_eq!(plan.6.as_deref(), Some("tail"));
        let mut context = self::context();
        context.parent_tag = "ul";
        context.sibling_tag = Some("ul");
        assert_eq!(plan_list("3. first", context).unwrap().0, "parent");
    }

    #[test]
    fn preserves_empty_first_items_and_delegates_unsupported_input() {
        let mut context = context();
        context.ordered = false;
        context.sibling_tag = Some("ul");
        context.previous_text = Some("");
        context.previous_tail = Some("\u{1f}");
        let plan = plan_list("* ", context).unwrap();
        assert_eq!(plan.1.as_deref(), Some(""));
        assert!(plan.2.is_empty());
        assert!(!plan.5);
        assert_eq!(plan.6.as_deref(), Some(""));
        assert!(plan_list("plain", self::context()).is_none());
        assert!(plan_list("\u{11f50}. item", self::context()).is_none());
        let mut context = self::context();
        context.lazy = true;
        assert_eq!(plan_list("3. first", context).unwrap().4, None);
    }
}

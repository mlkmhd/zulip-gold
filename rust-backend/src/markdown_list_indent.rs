pub(crate) fn indent_parent(block: &str, tight_list: bool, descendants: &[&str]) -> (usize, usize) {
    let indentation = block.bytes().take_while(|&byte| byte == b' ').count() / 2;
    let mut level = usize::from(tight_list);
    let mut steps = 0;
    for &tag in descendants {
        if indentation <= level {
            break;
        }
        match tag {
            "ul" | "ol" => level += 1,
            "li" => {}
            _ => break,
        }
        steps += 1;
    }
    (level, steps)
}

pub(crate) fn loose_detab(block: &str, level: usize) -> Option<String> {
    let indentation = level.checked_mul(2)?;
    Some(
        block
            .split('\n')
            .map(|line| {
                if line.bytes().take(indentation).all(|byte| byte == b' ')
                    && line.len() >= indentation
                {
                    &line[indentation..]
                } else {
                    line
                }
            })
            .collect::<Vec<_>>()
            .join("\n"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_tight_and_loose_list_parents() {
        let tags = ["ul", "li", "ol", "li", "p"];
        assert_eq!(indent_parent("    nested", false, &tags), (2, 3));
        assert_eq!(indent_parent("    nested", true, &tags), (2, 1));
        assert_eq!(indent_parent("        code", false, &tags), (2, 4));
        assert_eq!(indent_parent("text", true, &tags), (1, 0));
        assert_eq!(indent_parent("  item", false, &["p", "ul"]), (0, 0));
    }

    #[test]
    fn removes_only_complete_ascii_indentation() {
        assert_eq!(
            loose_detab("    عنوان\n  next\n\n    \u{1f}text\n\tcode", 2),
            Some("عنوان\n  next\n\n\u{1f}text\n\tcode".to_owned())
        );
        assert_eq!(loose_detab(" text\n", 0), Some(" text\n".to_owned()));
        assert_eq!(loose_detab(" text\n", usize::MAX), None);
    }
}

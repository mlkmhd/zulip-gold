#[derive(Debug, PartialEq, Eq)]
pub(crate) struct HorizontalRule<'a> {
    pub before: &'a str,
    pub after: &'a str,
}

fn is_horizontal_rule(line: &str) -> bool {
    let content = line.trim_start_matches(' ');
    if line.len() - content.len() > 3 {
        return false;
    }
    let content = content.trim_end_matches(' ');
    let Some(marker @ (b'-' | b'_' | b'*')) = content.bytes().next() else {
        return false;
    };
    let mut count = 0;
    let mut spaces = 0;
    for byte in content.bytes() {
        if byte == marker {
            if spaces > 2 {
                return false;
            }
            count += 1;
            spaces = 0;
        } else if byte == b' ' {
            spaces += 1;
        } else {
            return false;
        }
    }
    count >= 3
}

pub(crate) fn parse_horizontal_rule(block: &str) -> Option<HorizontalRule<'_>> {
    let mut offset = 0;
    for line in block.split('\n') {
        if is_horizontal_rule(line) {
            return Some(HorizontalRule {
                before: block[..offset].trim_end_matches('\n'),
                after: block[offset + line.len()..].trim_start_matches('\n'),
            });
        }
        offset += line.len() + 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_marker_runs_and_ascii_spacing() {
        for line in ["---", "***", "___", "   -  --   ", "* * *", "_  _  _"] {
            assert!(is_horizontal_rule(line), "{line:?}");
        }
        for line in [
            "",
            "--",
            "-_*",
            "    ---",
            "-   - -",
            "\t---",
            "---\r",
            "*\u{a0}**",
        ] {
            assert!(!is_horizontal_rule(line), "{line:?}");
        }
    }

    #[test]
    fn splits_at_first_rule_and_trims_only_newlines() {
        assert_eq!(
            parse_horizontal_rule("عنوان\n\n---\n\nnext\n***"),
            Some(HorizontalRule {
                before: "عنوان",
                after: "next\n***"
            })
        );
        assert_eq!(
            parse_horizontal_rule("\n___\n"),
            Some(HorizontalRule {
                before: "",
                after: ""
            })
        );
        assert_eq!(parse_horizontal_rule("text\n--\n"), None);
    }
}

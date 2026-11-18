use crate::draft_text::is_python_whitespace;
use crate::markdown_fences::parse_fence_line;

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum BlockStep<'a> {
    Line(&'a str),
    Open {
        fence: &'a str,
        language: Option<String>,
        header: Option<&'a str>,
        default_language: Option<&'a str>,
    },
    Close,
}

#[derive(Clone, Copy)]
enum BlockKind {
    Outer,
    Quote,
    Spoiler,
    Code,
}

struct Block<'a> {
    fence: &'a str,
    kind: BlockKind,
    default_language: Option<&'a str>,
}

pub(crate) fn plan_lines<'a>(
    lines: &[&'a str],
    default_language: Option<&'a str>,
) -> Vec<BlockStep<'a>> {
    let mut blocks = vec![Block {
        fence: "",
        kind: BlockKind::Outer,
        default_language,
    }];
    let mut steps = Vec::with_capacity(lines.len());
    for &line in lines {
        let current = blocks.last().expect("outer block remains open");
        let trimmed = line.trim_end_matches(is_python_whitespace);
        if !matches!(current.kind, BlockKind::Outer) && trimmed == current.fence {
            blocks.pop();
            steps.push(BlockStep::Close);
            continue;
        }
        if !matches!(current.kind, BlockKind::Code)
            && let Some(parsed) = parse_fence_line(line)
        {
            let default_language = current.default_language;
            let language = parsed
                .language
                .or(default_language.filter(|language| !language.is_empty()))
                .map(str::to_ascii_lowercase);
            let kind = match language.as_deref() {
                Some("quote" | "quoted") => BlockKind::Quote,
                Some("spoiler") => BlockKind::Spoiler,
                _ => BlockKind::Code,
            };
            blocks.push(Block {
                fence: parsed.fence,
                kind,
                default_language: matches!(kind, BlockKind::Quote)
                    .then_some(default_language)
                    .flatten(),
            });
            steps.push(BlockStep::Open {
                fence: parsed.fence,
                language,
                header: parsed.header,
                default_language,
            });
        } else {
            steps.push(BlockStep::Line(
                if matches!(current.kind, BlockKind::Code) {
                    trimmed
                } else {
                    line
                },
            ));
        }
    }
    steps
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn code_and_math_do_not_nest() {
        for language in ["py", "math"] {
            let opening = format!("```{language}");
            assert_eq!(
                plan_lines(&[&opening, "```quote", "body\u{1c}", "```  "], None),
                vec![
                    BlockStep::Open {
                        fence: "```",
                        language: Some(language.to_owned()),
                        header: None,
                        default_language: None,
                    },
                    BlockStep::Line("```quote"),
                    BlockStep::Line("body"),
                    BlockStep::Close,
                ],
            );
        }
    }

    #[test]
    fn quotes_inherit_the_default_language_and_spoilers_reset_it() {
        let steps = plan_lines(
            &[
                "```",
                "~~~spoiler Header",
                "````",
                "body  ",
                "````",
                "~~~",
                "```",
            ],
            Some("QuOtE"),
        );
        assert_eq!(
            steps,
            vec![
                BlockStep::Open {
                    fence: "```",
                    language: Some("quote".to_owned()),
                    header: None,
                    default_language: Some("QuOtE"),
                },
                BlockStep::Open {
                    fence: "~~~",
                    language: Some("spoiler".to_owned()),
                    header: Some("Header"),
                    default_language: Some("QuOtE"),
                },
                BlockStep::Open {
                    fence: "````",
                    language: None,
                    header: None,
                    default_language: None,
                },
                BlockStep::Line("body"),
                BlockStep::Close,
                BlockStep::Close,
                BlockStep::Close,
            ],
        );
    }

    #[test]
    fn unclosed_quotes_keep_whitespace_and_remain_open() {
        assert_eq!(
            plan_lines(&["```QUOTE", "body  ", "~~~", "text"], None),
            vec![
                BlockStep::Open {
                    fence: "```",
                    language: Some("quote".to_owned()),
                    header: None,
                    default_language: None,
                },
                BlockStep::Line("body  "),
                BlockStep::Open {
                    fence: "~~~",
                    language: None,
                    header: None,
                    default_language: None,
                },
                BlockStep::Line("text"),
            ],
        );
    }
}

const PLACEHOLDER_PREFIX: &str = "\u{2}wzxhzdk:";
const MAX_OUTPUT_BYTES: usize = 16 * 1024 * 1024;

struct Placeholder {
    start: usize,
    end: usize,
    key: Option<usize>,
    wrapped: bool,
}

fn next_placeholder(text: &str, mut offset: usize) -> Result<Option<Placeholder>, ()> {
    while let Some(relative) = text[offset..].find(PLACEHOLDER_PREFIX) {
        let start = offset + relative;
        let digits_start = start + PLACEHOLDER_PREFIX.len();
        let digits_length = text[digits_start..]
            .bytes()
            .take_while(u8::is_ascii_digit)
            .count();
        let digits_end = digits_start + digits_length;
        if digits_length > 0 && text[digits_end..].starts_with('\u{3}') {
            // Python's configurable integer-string limit may reject long keys.
            if digits_length > 128 {
                return Err(());
            }
            let end = digits_end + 1;
            let wrapped = start >= offset + 3
                && text[..start].ends_with("<p>")
                && text[end..].starts_with("</p>");
            let key = text[digits_start..digits_end]
                .bytes()
                .try_fold(0_usize, |key, digit| {
                    key.checked_mul(10)?.checked_add(usize::from(digit - b'0'))
                });
            return Ok(Some(Placeholder {
                start: if wrapped { start - 3 } else { start },
                end: if wrapped { end + 4 } else { end },
                key,
                wrapped,
            }));
        }
        offset = digits_start;
    }
    Ok(None)
}

fn restore(
    text: &str,
    stash: &[(&str, bool)],
    active: &mut Vec<usize>,
    output: &mut String,
    substitutions: &mut usize,
) -> Option<()> {
    if active.len() > 100 {
        return None;
    }
    let mut offset = 0;
    while let Some(placeholder) = next_placeholder(text, offset).ok()? {
        *substitutions += 1;
        if *substitutions > 100_000 {
            return None;
        }
        output.push_str(&text[offset..placeholder.start]);
        if let Some(key) = placeholder.key.filter(|&key| key < stash.len()) {
            if active.contains(&key) {
                return None;
            }
            active.push(key);
            let (html, block) = stash[key];
            if placeholder.wrapped && !block {
                restore(
                    &format!("<p>{html}</p>"),
                    stash,
                    active,
                    output,
                    substitutions,
                )?;
            } else {
                restore(html, stash, active, output, substitutions)?;
            }
            active.pop();
        } else {
            output.push_str(&text[placeholder.start..placeholder.end]);
        }
        if output.len() > MAX_OUTPUT_BYTES {
            return None;
        }
        offset = placeholder.end;
    }
    output.push_str(&text[offset..]);
    (output.len() <= MAX_OUTPUT_BYTES).then_some(())
}

pub fn restore_html(text: &str, stash: &[(&str, bool)]) -> Option<String> {
    let mut output = String::new();
    restore(text, stash, &mut Vec::new(), &mut output, &mut 0)?;
    Some(output)
}

pub fn restore_ampersands(text: &str) -> String {
    text.replace("\u{2}amp\u{3}", "&")
}

#[cfg(test)]
mod tests {
    use super::{restore_ampersands, restore_html};

    #[test]
    fn restores_nested_wrappers_in_reference_order() {
        let stash = [("\u{2}wzxhzdk:1\u{3}", false), ("<pre>code</pre>", true)];
        assert_eq!(
            restore_html("<p>\u{2}wzxhzdk:0\u{3}</p>", &stash).as_deref(),
            Some("<pre>code</pre>")
        );
        assert_eq!(
            restore_html("<p>\u{2}wzxhzdk:01\u{3}</p>", &stash).as_deref(),
            Some("<pre>code</pre>")
        );
    }

    #[test]
    fn retains_unknown_and_malformed_placeholders() {
        let text = "<p>\u{2}wzxhzdk:9999999999999999999999999\u{3}</p>\u{2}wzxhzdk:۰\u{3}\u{2}wzxhzdk:\u{3}";
        assert_eq!(
            restore_html(text, &[("html", false)]).as_deref(),
            Some(text)
        );
    }

    #[test]
    fn cyclic_and_long_keys_are_delegated() {
        let placeholder = "\u{2}wzxhzdk:0\u{3}";
        assert_eq!(restore_html(placeholder, &[(placeholder, false)]), None);
        assert_eq!(
            restore_html(&format!("\u{2}wzxhzdk:{}\u{3}", "0".repeat(129)), &[]),
            None
        );
    }

    #[test]
    fn excessive_expansion_is_delegated_without_partial_output() {
        let large = "x".repeat(super::MAX_OUTPUT_BYTES + 1);
        assert_eq!(
            restore_html("\u{2}wzxhzdk:0\u{3}", &[(&large, false)]),
            None
        );
        let chain = (1..=110)
            .map(|key| format!("\u{2}wzxhzdk:{key}\u{3}"))
            .collect::<Vec<_>>();
        let stash = chain
            .iter()
            .map(|text| (text.as_str(), false))
            .collect::<Vec<_>>();
        assert_eq!(restore_html("\u{2}wzxhzdk:0\u{3}", &stash), None);
        assert_eq!(
            restore_html(&"\u{2}wzxhzdk:0\u{3}".repeat(100_001), &[("", false)]),
            None
        );
    }

    #[test]
    fn restores_only_complete_ampersand_placeholders() {
        assert_eq!(
            restore_ampersands("\u{2}amp\u{3}&amp;\u{2}AMP\u{3}\u{2}amp"),
            "&&amp;\u{2}AMP\u{3}\u{2}amp"
        );
    }
}

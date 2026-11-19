pub fn unescape(text: &str) -> Option<String> {
    let mut output = String::new();
    let mut offset = 0;
    while let Some(relative) = text[offset..].find('\u{2}') {
        let start = offset + relative;
        let digits_start = start + 1;
        let digits_length = text[digits_start..]
            .bytes()
            .take_while(u8::is_ascii_digit)
            .count();
        let end = digits_start + digits_length;
        let next = text[end..].chars().next();
        // Python determines Unicode decimal digits using its own database.
        if next.is_some_and(|c| !c.is_ascii()) {
            return None;
        }
        if digits_length > 0 && next == Some('\u{3}') {
            // Keep Python's configurable integer-string limit and chr errors.
            if digits_length > 128 {
                return None;
            }
            let value = text[digits_start..end].parse::<u32>().ok()?;
            let character = char::from_u32(value)?;
            output.push_str(&text[offset..start]);
            output.push(character);
            offset = end + 1;
        } else {
            output.push_str(&text[offset..digits_start]);
            offset = digits_start;
        }
    }
    output.push_str(&text[offset..]);
    Some(output)
}

#[cfg(test)]
mod tests {
    use super::unescape;

    #[test]
    fn restores_scalar_values_and_leading_zeroes() {
        assert_eq!(
            unescape("فارسی \u{2}00042\u{3} \u{2}1114111\u{3}").as_deref(),
            Some("فارسی * \u{10ffff}")
        );
        assert_eq!(unescape("\u{2}0\u{3}").as_deref(), Some("\0"));
    }

    #[test]
    fn leaves_malformed_and_newly_formed_placeholders_unchanged() {
        let text = "\u{2}-1\u{3}\u{2}\u{3}\u{2}12x\u{3}\u{2}2\u{3}42\u{3}";
        assert_eq!(
            unescape(text).as_deref(),
            Some("\u{2}-1\u{3}\u{2}\u{3}\u{2}12x\u{3}\u{2}42\u{3}")
        );
    }

    #[test]
    fn delegates_unicode_digits_surrogates_and_invalid_codepoints() {
        for text in [
            "\u{2}۴۲\u{3}",
            "\u{2}4۲\u{3}",
            "\u{2}55296\u{3}",
            "\u{2}1114112\u{3}",
            "\u{2}99999999999999\u{3}",
        ] {
            assert_eq!(unescape(text), None);
        }
        assert_eq!(unescape(&format!("\u{2}{}\u{3}", "0".repeat(129))), None);
    }
}

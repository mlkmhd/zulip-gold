use crate::draft_text::{MESSAGE_TRUNCATION_SUFFIX, truncate};

pub(crate) fn unused_fence_length(message: &[u8]) -> usize {
    let mut length = 3;
    for line in message.split(|&byte| byte == b'\n') {
        let spaces = line
            .iter()
            .take(3)
            .take_while(|&&byte| byte == b' ')
            .count();
        let backticks = line[spaces..]
            .iter()
            .take_while(|&&byte| byte == b'`')
            .count();
        if backticks >= 3 {
            length = length.max(backticks + 1);
        }
    }
    length
}

pub(crate) fn append_quote(prefix: &str, message: &str, max_length: usize) -> Option<String> {
    let fence_length = unused_fence_length(message.as_bytes());
    let remaining = max_length
        .checked_sub(prefix.chars().count())?
        .checked_sub(2 * fence_length + "quote\n\n".len())?;
    let fence = "`".repeat(fence_length);
    let message = truncate(message, remaining, MESSAGE_TRUNCATION_SUFFIX);
    Some(format!("{prefix}{fence}quote\n{message}\n{fence}"))
}

#[cfg(any(feature = "python", test))]
pub(crate) fn append_exact_quote(
    prefix: &[u8],
    message: &[u8],
    max_length: i64,
) -> Option<Vec<u8>> {
    let is_lead = |byte: u8| byte & 0xc0 != 0x80;
    let count = |text: &[u8]| text.iter().filter(|&&byte| is_lead(byte)).count() as i128;
    let fence_len = unused_fence_length(message);
    let overhead = count(prefix) + 2 * fence_len as i128 + b"quote\n\n".len() as i128;
    let remaining = i128::from(max_length) - overhead;
    let message_len = count(message);
    let truncation = MESSAGE_TRUNCATION_SUFFIX.as_bytes();
    let (content, suffix) = if message_len > remaining {
        let stop = remaining - truncation.len() as i128;
        let end = (if stop < 0 { message_len + stop } else { stop }).clamp(0, message_len) as usize;
        let byte = message
            .iter()
            .enumerate()
            .filter(|(_, byte)| is_lead(**byte))
            .nth(end)
            .map_or(message.len(), |(index, _)| index);
        (&message[..byte], truncation)
    } else {
        (message, b"".as_slice())
    };

    let mut output = Vec::new();
    let capacity = prefix
        .len()
        .checked_add(fence_len.checked_mul(2)?)?
        .checked_add(b"quote\n\n".len())?
        .checked_add(content.len())?
        .checked_add(suffix.len())?;
    output.try_reserve_exact(capacity).ok()?;
    output.extend_from_slice(prefix);
    output.extend(std::iter::repeat_n(b'`', fence_len));
    output.extend_from_slice(b"quote\n");
    output.extend_from_slice(content);
    output.extend_from_slice(suffix);
    output.push(b'\n');
    output.extend(std::iter::repeat_n(b'`', fence_len));
    Some(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chooses_a_fence_longer_than_indented_backticks() {
        let message = "x ```\n   `````js\n    ````````\u{2028}````````";
        assert_eq!(unused_fence_length(message.as_bytes()), 6);
        assert_eq!(unused_fence_length(b"~~~\n  ``"), 3);
        assert_eq!(unused_fence_length(b"\xed\xa0\x80\n   ````"), 5);
        assert_eq!(
            append_quote("in topic:\n", message, 100).as_deref(),
            Some("in topic:\n``````quote\nx ```\n   `````js\n    ````````\u{2028}````````\n``````"),
        );
    }

    #[test]
    fn exact_quote_keeps_surrogates_and_python_negative_slice_stops() {
        let text = b"\xed\xa0\x80\n```\nmore";
        assert_eq!(
            append_exact_quote(b"x\n", text, 100).as_deref(),
            Some(b"x\n````quote\n\xed\xa0\x80\n```\nmore\n````".as_slice()),
        );
        assert_eq!(
            append_exact_quote(b"x\n", b"abcdefghijklmnopqrstuvwxyz", 1).as_deref(),
            Some(b"x\n```quote\n\n[message truncated]\n```".as_slice()),
        );
    }

    #[test]
    fn counts_unicode_characters_and_preserves_the_closing_fence() {
        let message = "📝".repeat(30);
        assert_eq!(
            append_quote("سلام\n", &message, 40).as_deref(),
            Some("سلام\n```quote\n📝📝\n[message truncated]\n```"),
        );
        assert_eq!(append_quote("سلام\n", &message, 17), None);
    }
}

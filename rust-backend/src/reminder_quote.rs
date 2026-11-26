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
    fn counts_unicode_characters_and_preserves_the_closing_fence() {
        let message = "📝".repeat(30);
        assert_eq!(
            append_quote("سلام\n", &message, 40).as_deref(),
            Some("سلام\n```quote\n📝📝\n[message truncated]\n```"),
        );
        assert_eq!(append_quote("سلام\n", &message, 17), None);
    }
}

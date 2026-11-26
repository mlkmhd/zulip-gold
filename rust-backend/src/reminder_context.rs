use crate::draft_text::trim_message_body;

pub(crate) fn normalize_note(note: &str, max_length: usize) -> Option<&str> {
    let note = trim_message_body(note);
    (note.chars().count() <= max_length).then_some(note)
}

#[cfg(any(feature = "python", test))]
pub(crate) fn normalize_exact_note(note: &[u8], max_length: i64) -> Option<&[u8]> {
    let trimmed = if let Ok(text) = std::str::from_utf8(note) {
        trim_message_body(text).as_bytes()
    } else {
        // Each invalid chunk is part of a lone surrogate. It is not
        // whitespace and prevents stripping any preceding character.
        let mut end = 0;
        let mut offset = 0;
        for chunk in note.utf8_chunks() {
            for (start, ch) in chunk.valid().char_indices() {
                if !crate::draft_text::is_python_whitespace(ch) {
                    end = offset + start + ch.len_utf8();
                }
            }
            offset += chunk.valid().len() + chunk.invalid().len();
            if !chunk.invalid().is_empty() {
                end = offset;
            }
        }
        let trimmed = &note[..end];
        let start = trimmed.iter().take_while(|&&byte| byte == b'\n').count();
        &trimmed[start..]
    };
    let chars = trimmed.iter().filter(|&&byte| byte & 0xc0 != 0x80).count() as i128;
    (chars <= i128::from(max_length)).then_some(trimmed)
}

pub(crate) fn widget_label(content: &str) -> Option<&'static str> {
    if content.starts_with("/poll") {
        Some("poll")
    } else if content.starts_with("/todo") {
        Some("todo list")
    } else {
        None
    }
}

fn append_topic_link_label(output: &mut Vec<u8>, label: &[u8]) {
    let mut bytes = label.iter().copied().peekable();
    while let Some(byte) = bytes.next() {
        let escaped: &[u8] = match byte {
            b'`' => b"&#96;",
            b'>' => b"&gt;",
            b'*' => b"&#42;",
            b'&' => b"&amp;",
            b'[' => b"&#91;",
            b']' => b"&#93;",
            b'$' if bytes.next_if_eq(&b'$').is_some() => b"&#36;&#36;",
            _ => {
                output.push(byte);
                continue;
            }
        };
        output.extend_from_slice(escaped);
    }
}

pub(crate) fn channel_topic_link_bytes(channel: &[u8], topic: &[u8], url: &[u8]) -> Vec<u8> {
    let mut link = b"[#".to_vec();
    append_topic_link_label(&mut link, channel);
    link.extend_from_slice(b" > ");
    append_topic_link_label(&mut link, topic);
    link.extend_from_slice(b"](");
    link.extend_from_slice(url);
    link.push(b')');
    link
}

pub(crate) fn channel_topic_link(channel: &str, topic: &str, url: &str) -> String {
    String::from_utf8(channel_topic_link_bytes(
        channel.as_bytes(),
        topic.as_bytes(),
        url.as_bytes(),
    ))
    .expect("replacing ASCII characters preserves valid UTF-8")
}

pub(crate) fn direct_recipient_mentions<'a>(
    sender_id: i32,
    recipients: impl IntoIterator<Item = (i32, &'a str)>,
) -> Vec<String> {
    let mut mentions: Vec<_> = recipients
        .into_iter()
        .filter(|&(id, _)| id != sender_id)
        .map(|(id, name)| format!("@_**{name}|{id}**"))
        .collect();
    mentions.sort_unstable();
    mentions
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_note_before_counting_unicode_characters() {
        assert_eq!(normalize_note("\n\n📝ش　\u{1f}", 2), Some("📝ش"));
        assert_eq!(normalize_note("\n\n📝ش　\u{1f}", 1), None);
        assert_eq!(normalize_note("\n　", 0), Some(""));
        assert_eq!(normalize_note("\n  note", 6), Some("  note"));
    }

    #[test]
    fn recognizes_case_sensitive_widget_prefixes() {
        assert_eq!(widget_label("/poll question"), Some("poll"));
        assert_eq!(widget_label("/todoList"), Some("todo list"));
        assert_eq!(widget_label("/Poll question"), None);
        assert_eq!(widget_label(" /todo list"), None);
    }

    #[test]
    fn escapes_each_topic_label_component_before_interpolating_the_link() {
        assert_eq!(
            channel_topic_link("˛[#]", "$ $$$ & `>*", "#narrow"),
            "[#˛&#91;#&#93; > $ &#36;&#36;$ &amp; &#96;&gt;&#42;](#narrow)"
        );
        assert_eq!(
            channel_topic_link("general", "", "#narrow"),
            "[#general > ](#narrow)"
        );
    }

    #[test]
    fn sorts_formatted_mentions_and_omits_the_sender() {
        assert_eq!(
            direct_recipient_mentions(10, [(3, "Zoe"), (10, "Hamlet"), (23, "Iago"), (4, "Iago")]),
            ["@_**Iago|23**", "@_**Iago|4**", "@_**Zoe|3**"]
        );
        assert!(direct_recipient_mentions(10, [(10, "Hamlet")]).is_empty());
    }
}

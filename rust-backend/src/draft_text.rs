use std::borrow::Cow;

pub(crate) const MESSAGE_TRUNCATION_SUFFIX: &str = "\n[message truncated]";

#[derive(Debug, PartialEq, Eq)]
pub enum BodyError {
    Empty,
    NullByte,
}

pub(crate) fn is_python_whitespace(character: char) -> bool {
    character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}')
}

pub(crate) fn trim_message_body(body: &str) -> &str {
    body.trim_end_matches(is_python_whitespace)
        .trim_start_matches('\n')
}

#[cfg(any(feature = "python", test))]
pub(crate) fn trim_message_body_bytes(body: &[u8]) -> &[u8] {
    if let Ok(text) = std::str::from_utf8(body) {
        trim_message_body(text).as_bytes()
    } else {
        // Each invalid chunk is part of a lone surrogate. It is not
        // whitespace and prevents stripping any preceding character.
        let mut end = 0;
        let mut offset = 0;
        for chunk in body.utf8_chunks() {
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
        let trimmed = &body[..end];
        let start = trimmed.iter().take_while(|&&byte| byte == b'\n').count();
        &trimmed[start..]
    }
}

#[cfg(feature = "python")]
pub(crate) fn normalize_exact_body(
    body: &[u8],
    max_length: i64,
) -> Result<Cow<'_, [u8]>, BodyError> {
    let body = trim_message_body_bytes(body);
    if body.is_empty() {
        return Err(BodyError::Empty);
    }
    if body.contains(&0) {
        return Err(BodyError::NullByte);
    }
    let lead = |byte: u8| byte & 0xc0 != 0x80;
    let length = body.iter().filter(|&&byte| lead(byte)).count() as i128;
    let limit = i128::from(max_length);
    if length <= limit {
        return Ok(Cow::Borrowed(body));
    }
    let suffix = MESSAGE_TRUNCATION_SUFFIX.as_bytes();
    let stop = limit - suffix.len() as i128;
    let end = (if stop < 0 { length + stop } else { stop }).clamp(0, length) as usize;
    let byte = body
        .iter()
        .enumerate()
        .filter(|(_, byte)| lead(**byte))
        .nth(end)
        .map_or(body.len(), |(index, _)| index);
    let mut result = body[..byte].to_vec();
    result.extend_from_slice(suffix);
    Ok(Cow::Owned(result))
}

pub fn normalize_body(body: &str, max_length: usize) -> Result<Cow<'_, str>, BodyError> {
    let body = trim_message_body(body);
    if body.is_empty() {
        return Err(BodyError::Empty);
    }
    if body.contains('\0') {
        return Err(BodyError::NullByte);
    }
    Ok(truncate(body, max_length, MESSAGE_TRUNCATION_SUFFIX))
}

pub fn normalize_topic(topic: &str, max_length: usize) -> Option<Cow<'_, str>> {
    let topic = truncate(topic, max_length, "...");
    (!topic.contains('\0')).then_some(topic)
}

pub(crate) fn truncate<'a>(text: &'a str, max_length: usize, suffix: &str) -> Cow<'a, str> {
    let length = text.chars().count();
    if length <= max_length {
        return Cow::Borrowed(text);
    }
    let end = match max_length.checked_sub(suffix.len()) {
        Some(end) => end,
        // A negative slice endpoint in Python is relative to the end.
        None => length.saturating_sub(suffix.len() - max_length),
    };
    let (byte_end, _) = text.char_indices().nth(end).unwrap();
    Cow::Owned(format!("{}{suffix}", &text[..byte_end]))
}

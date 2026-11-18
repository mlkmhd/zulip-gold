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

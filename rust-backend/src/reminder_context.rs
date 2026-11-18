use crate::draft_text::trim_message_body;

pub(crate) fn normalize_note(note: &str, max_length: usize) -> Option<&str> {
    let note = trim_message_body(note);
    (note.chars().count() <= max_length).then_some(note)
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
}

use crate::draft_text::is_python_whitespace;

pub(crate) fn normalize_channel_topic<'a>(
    topic: &'a str,
    empty_topic_fallback: &str,
) -> Result<&'a str, usize> {
    let topic = topic.trim_matches(is_python_whitespace);
    if topic == empty_topic_fallback || topic == "(no topic)" {
        return Ok("");
    }
    for (index, character) in topic.chars().enumerate() {
        let code = character as u32;
        if character.is_control() || (0xfdd0..=0xfdef).contains(&code) || (code & 0xffff) >= 0xfffe
        {
            return Err(index + 1);
        }
    }
    Ok(topic)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trims_whitespace_and_recognizes_empty_topic_aliases() {
        assert_eq!(
            normalize_channel_topic("\u{1f}general chat　", "general chat"),
            Ok("")
        );
        assert_eq!(
            normalize_channel_topic(" (no topic) ", "general chat"),
            Ok("")
        );
        assert_eq!(
            normalize_channel_topic("　سلام📝\u{1f}", "general chat"),
            Ok("سلام📝")
        );
        assert_eq!(
            normalize_channel_topic(" General chat ", "general chat"),
            Ok("General chat")
        );
    }

    #[test]
    fn reports_the_first_invalid_character_at_a_one_based_position() {
        for invalid in ['\x7f', '\u{80}', '\u{fdd0}', '\u{ffff}', '\u{10fffe}'] {
            let topic = format!(" \n📝{invalid}x ");
            assert_eq!(normalize_channel_topic(&topic, "general chat"), Err(2));
        }
        assert_eq!(
            normalize_channel_topic("\u{feff}Topic", "general chat"),
            Ok("\u{feff}Topic")
        );
    }
}

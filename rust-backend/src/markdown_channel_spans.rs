use crate::url_fragments::encode_hash_component;

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct ChannelLink {
    pub attributes: Vec<(&'static str, String)>,
    pub text: String,
    pub empty_topic: bool,
    pub tail: Option<&'static str>,
}

pub(crate) fn channel_link(
    channel_id: i64,
    channel_name: &str,
    topic_name: Option<&str>,
    with_operand: Option<i64>,
    message_id: Option<&str>,
) -> ChannelLink {
    let channel = format!(
        "{channel_id}-{}",
        encode_hash_component(&channel_name.replace(' ', "-"))
    );
    let mut href = format!("/#narrow/channel/{channel}");
    let mut text = format!("#{channel_name}");
    if let Some(topic) = topic_name {
        href.push_str(&format!("/topic/{}", encode_hash_component(topic)));
        text.push_str(&format!(" > {topic}"));
        if let Some(message_id) = message_id {
            href.push_str(&format!("/near/{message_id}"));
        } else if let Some(with_operand) = with_operand {
            href.push_str(&format!("/with/{with_operand}"));
        }
    }
    let empty_topic = topic_name == Some("");
    if message_id.is_some() && !empty_topic {
        text.push_str(" @ 💬");
    }
    let class = if message_id.is_some() {
        "message-link"
    } else if topic_name.is_some() {
        "stream-topic"
    } else {
        "stream"
    };
    let mut attributes = vec![("class", class.to_owned())];
    if message_id.is_none() {
        attributes.push(("data-stream-id", channel_id.to_string()));
    }
    attributes.push(("href", href));
    ChannelLink {
        attributes,
        text,
        empty_topic,
        tail: (empty_topic && message_id.is_some()).then_some(" @ 💬"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_channel_hints_and_topic_operands() {
        let link = channel_link(42, "dev tools", Some("a.b /📝"), Some(99), None);
        assert_eq!(link.attributes[0], ("class", "stream-topic".to_owned()));
        assert_eq!(link.attributes[1], ("data-stream-id", "42".to_owned()));
        assert_eq!(
            link.attributes[2],
            (
                "href",
                "/#narrow/channel/42-dev-tools/topic/a.2Eb.20.2F.F0.9F.93.9D/with/99".to_owned()
            )
        );
        assert_eq!(link.text, "#dev tools > a.b /📝");
        assert!(!link.empty_topic);
        assert_eq!(link.tail, None);
    }

    #[test]
    fn preserves_empty_topic_children_and_message_id_text() {
        let link = channel_link(42, "<channel &>", Some(""), None, Some("000123"));
        assert_eq!(link.attributes.len(), 2);
        assert_eq!(link.attributes[0], ("class", "message-link".to_owned()));
        assert!(link.attributes[1].1.ends_with("/topic//near/000123"));
        assert_eq!(link.text, "#<channel &> > ");
        assert!(link.empty_topic);
        assert_eq!(link.tail, Some(" @ 💬"));
        assert_eq!(channel_link(42, "name", None, None, None).text, "#name");
        assert_eq!(channel_link(42, "name", Some(""), None, None).tail, None);
        assert_eq!(
            channel_link(42, "name", Some("topic"), None, Some("1")).text,
            "#name > topic @ 💬"
        );
    }
}

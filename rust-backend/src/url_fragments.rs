const HEX: &[u8; 16] = b"0123456789ABCDEF";

pub(crate) fn encode_hash_component(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'~') {
            encoded.push(char::from(byte));
        } else {
            encoded.push('.');
            encoded.push(char::from(HEX[usize::from(byte >> 4)]));
            encoded.push(char::from(HEX[usize::from(byte & 0xF)]));
        }
    }
    encoded
}

#[cfg(any(feature = "python", test))]
pub(crate) fn encode_channel(channel_id: i64, channel_name: &str, with_operator: bool) -> String {
    let encoded_name = encode_hash_component(&channel_name.replace(' ', "-"));
    let operator = if with_operator { "channel/" } else { "" };
    format!("{operator}{channel_id}-{encoded_name}")
}

#[cfg(any(feature = "python", test))]
pub(crate) fn encode_user_ids(mut user_ids: Vec<i64>, with_operator: bool) -> String {
    user_ids.sort_unstable();
    let mut slug = user_ids
        .iter()
        .map(i64::to_string)
        .collect::<Vec<_>>()
        .join(",");
    if user_ids.len() >= 3 {
        slug.push_str("-group");
    }
    if with_operator {
        slug.insert_str(0, "dm/");
    }
    slug
}

#[cfg(any(feature = "python", test))]
pub(crate) fn parse_recipient_slug(slug: &str) -> Option<(Vec<i64>, &str)> {
    let (ids, suffix) = slug.split_once('-')?;
    let ids = ids
        .split(',')
        .map(|id| {
            // Decline noncanonical spelling and larger integers so Python
            // keeps its Unicode, sign and maximum-digit parsing behavior.
            if id.is_empty() || id.len() > 19 || !id.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            id.parse().ok()
        })
        .collect::<Option<Vec<i64>>>()?;
    Some((ids, suffix))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_dot_escapes_and_utf8_bytes() {
        assert_eq!(
            encode_hash_component("ABC xyz-_.~%/() سلام"),
            "ABC.20xyz-_.2E~.25.2F.28.29.20.D8.B3.D9.84.D8.A7.D9.85"
        );
        assert_eq!(encode_hash_component(""), "");
    }

    #[test]
    fn encodes_channel_ascii_spaces_but_not_other_whitespace_as_dashes() {
        assert_eq!(
            encode_channel(-9, "A  B.\u{a0}", true),
            "channel/-9-A--B.2E.C2.A0"
        );
        assert_eq!(encode_channel(0, "", false), "0-");
    }

    #[test]
    fn decodes_canonical_recipient_slugs_without_sorting_or_modifying_the_label() {
        assert_eq!(
            parse_recipient_slug("1,08,1-سلام-a"),
            Some((vec![1, 8, 1], "سلام-a"))
        );
        assert_eq!(parse_recipient_slug("0-"), Some((vec![0], "")));
        assert_eq!(parse_recipient_slug("9223372036854775808-channel"), None);
        assert_eq!(parse_recipient_slug("a-legacy"), None);
    }

    #[test]
    fn sorts_all_ids_and_keeps_duplicate_ids() {
        assert_eq!(encode_user_ids(vec![10, 2, 2], true), "dm/2,2,10-group");
        assert_eq!(encode_user_ids(vec![10, 2], false), "2,10");
        assert_eq!(encode_user_ids(vec![10], false), "10");
    }
}

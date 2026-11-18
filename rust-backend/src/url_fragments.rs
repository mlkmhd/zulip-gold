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
    fn sorts_all_ids_and_keeps_duplicate_ids() {
        assert_eq!(encode_user_ids(vec![10, 2, 2], true), "dm/2,2,10-group");
        assert_eq!(encode_user_ids(vec![10, 2], false), "2,10");
        assert_eq!(encode_user_ids(vec![10], false), "10");
    }
}

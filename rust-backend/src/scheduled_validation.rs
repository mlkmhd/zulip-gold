const MICROS_PER_SECOND: i64 = 1_000_000;
const FIRST_DATETIME_SECOND: i64 = -62_135_596_800;
const LAST_DATETIME_SECOND: i64 = 253_402_300_799;

pub(crate) fn delivery_time_is_future(timestamp: i64, now_microseconds: i64) -> Option<bool> {
    // Out-of-range integers keep the datetime conversion errors of the caller.
    (FIRST_DATETIME_SECOND..=LAST_DATETIME_SECOND)
        .contains(&timestamp)
        .then(|| timestamp * MICROS_PER_SECOND > now_microseconds)
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum RecipientKind {
    Channel,
    Direct,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum RecipientError {
    InvalidType,
    ChannelList,
    DirectInteger,
    EmptyDirectList,
}

pub(crate) fn classify_recipient(
    message_type: &str,
    list_length: Option<usize>,
) -> Result<RecipientKind, RecipientError> {
    match (message_type, list_length) {
        ("channel" | "stream", None) => Ok(RecipientKind::Channel),
        ("channel" | "stream", Some(_)) => Err(RecipientError::ChannelList),
        ("direct" | "private", None) => Err(RecipientError::DirectInteger),
        ("direct" | "private", Some(0)) => Err(RecipientError::EmptyDirectList),
        ("direct" | "private", Some(_)) => Ok(RecipientKind::Direct),
        _ => Err(RecipientError::InvalidType),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_the_delivery_second_with_microsecond_precision() {
        assert_eq!(delivery_time_is_future(3, 2_999_999), Some(true));
        assert_eq!(delivery_time_is_future(3, 3_000_000), Some(false));
        assert_eq!(delivery_time_is_future(3, 3_000_001), Some(false));
        assert_eq!(delivery_time_is_future(-1, -1_000_001), Some(true));
        assert_eq!(delivery_time_is_future(-1, -1_000_000), Some(false));
        assert_eq!(
            delivery_time_is_future(FIRST_DATETIME_SECOND, 0),
            Some(false)
        );
        assert_eq!(delivery_time_is_future(LAST_DATETIME_SECOND, 0), Some(true));
        assert_eq!(delivery_time_is_future(FIRST_DATETIME_SECOND - 1, 0), None);
        assert_eq!(delivery_time_is_future(LAST_DATETIME_SECOND + 1, 0), None);
        assert_eq!(delivery_time_is_future(i64::MIN, 0), None);
        assert_eq!(delivery_time_is_future(i64::MAX, 0), None);
    }

    #[test]
    fn recognizes_aliases_and_checks_recipient_shape() {
        for message_type in ["channel", "stream"] {
            assert_eq!(
                classify_recipient(message_type, None),
                Ok(RecipientKind::Channel)
            );
            assert_eq!(
                classify_recipient(message_type, Some(0)),
                Err(RecipientError::ChannelList)
            );
            assert_eq!(
                classify_recipient(message_type, Some(1)),
                Err(RecipientError::ChannelList)
            );
        }
        for message_type in ["direct", "private"] {
            assert_eq!(
                classify_recipient(message_type, Some(2)),
                Ok(RecipientKind::Direct)
            );
            assert_eq!(
                classify_recipient(message_type, None),
                Err(RecipientError::DirectInteger)
            );
            assert_eq!(
                classify_recipient(message_type, Some(0)),
                Err(RecipientError::EmptyDirectList)
            );
        }
        assert_eq!(
            classify_recipient("Direct", Some(1)),
            Err(RecipientError::InvalidType)
        );
    }
}

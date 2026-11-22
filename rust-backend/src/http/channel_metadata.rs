//! The verified authenticated channel metadata predicate.

// These are exactly the three Django settings that grant channel metadata.
macro_rules! channel_metadata_group_match {
    () => {
        "EXISTS (
                   SELECT 1 FROM member_groups AS g WHERE g.id IN (
                       s.can_administer_channel_group_id,
                       s.can_add_subscribers_group_id,
                       s.can_subscribe_group_id
                   )
               )"
    };
}

pub(super) use channel_metadata_group_match;

// The caller still chooses the channel scope and archive behavior.
macro_rules! channel_metadata_visible {
    () => {
        concat!(
            "(s.is_web_public OR EXISTS (
               SELECT 1 FROM zulip.zerver_subscription AS own
               WHERE own.user_profile_id = actor.id AND own.recipient_id = s.recipient_id
                 AND own.active
           ) OR actor.role IN (100, 200) OR (
               actor.role <> 600 AND (NOT s.invite_only OR ",
            $crate::http::channel_metadata::channel_metadata_group_match!(),
            ")
           ))",
        )
    };
}

pub(super) use channel_metadata_visible;

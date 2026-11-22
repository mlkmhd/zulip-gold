//! The verified authenticated channel metadata predicate.

// The caller still chooses the channel scope and archive behavior.
macro_rules! channel_metadata_visible {
    () => {
        "(s.is_web_public OR EXISTS (
               SELECT 1 FROM zulip.zerver_subscription AS own
               WHERE own.user_profile_id = actor.id AND own.recipient_id = s.recipient_id
                 AND own.active
           ) OR actor.role IN (100, 200) OR (
               actor.role <> 600 AND (NOT s.invite_only OR EXISTS (
                   SELECT 1 FROM member_groups AS g WHERE g.id IN (
                       s.can_administer_channel_group_id,
                       s.can_add_subscribers_group_id,
                       s.can_subscribe_group_id
                   )
               ))
           ))"
    };
}

pub(super) use channel_metadata_visible;

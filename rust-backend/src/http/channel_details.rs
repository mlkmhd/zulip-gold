//! Read only documented channel details through the existing metadata gate.

use std::{error::Error, io};

use serde::Serialize;
use serde_json::{Map, Value};
use tokio_postgres::Client;

use super::channel_metadata::channel_metadata_visible;
use crate::channel_traffic::average_weekly_messages;

pub(super) const PATH: &str = "/api/v1/streams/{id}";
pub(super) const SESSION_PATH: &str = "/json/streams/{id}";

// Select an explicit payload, never the whole stream model. This remains
// separate from the email-address endpoint, uploads, and message content.
const SQL: &str = concat!(
    "WITH RECURSIVE actor AS (
         SELECT id, realm_id, role FROM zulip.zerver_userprofile
         WHERE id = $1 AND realm_id = $2 AND is_active
     ), member_groups(id) AS (
         SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
         JOIN actor ON actor.id = m.user_profile_id WHERE actor.role <> 600
         UNION
         SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
         JOIN member_groups AS g ON g.id = m.subgroup_id
     ), visible AS (
         SELECT s.id FROM zulip.zerver_stream AS s CROSS JOIN actor
         WHERE s.realm_id = actor.realm_id AND s.id = $3::bigint
           AND ",
    channel_metadata_visible!(),
    "
     )
     SELECT (jsonb_build_object(
         'creator_id',s.creator_id,
         'date_created',trunc(extract(epoch FROM s.date_created)::double precision)::bigint,
         'default_push_notifications',s.default_push_notifications,
         'description',s.description,
         'first_message_id',s.first_message_id,
         'folder_id',s.folder_id,
         'icon_url',CASE WHEN s.icon_key IS NOT NULL
             THEN '/json/streams/' || s.id || '/icon?v=' || s.icon_key::text END,
         'history_public_to_subscribers',s.history_public_to_subscribers,
         'invite_only',s.invite_only,
         'is_archived',s.deactivated,
         'is_recently_active',s.is_recently_active,
         'is_web_public',s.is_web_public,
         'message_retention_days',s.message_retention_days,
         'name',s.name,
         'rendered_description',s.rendered_description,
         'stream_id',s.id,
         'subscriber_count',s.subscriber_count
     ) || groups.payload)::text,
     floor(extract(epoch FROM statement_timestamp() - s.date_created)/86400)::bigint,
     coalesce((
         SELECT sum(t.value) FROM zulip.analytics_streamcount AS t
         WHERE t.realm_id = s.realm_id AND t.stream_id = s.id
           AND t.property = 'messages_in_stream:is_bot:day'
           AND t.end_time > statement_timestamp() - interval '672 hours'
     ),0)::text,
     CASE WHEN send.is_system_group THEN send.name ELSE NULL END,
     s.topics_policy
     FROM visible JOIN zulip.zerver_stream AS s USING (id)
     LEFT JOIN zulip.zerver_namedusergroup AS send
         ON send.usergroup_ptr_id = s.can_send_message_group_id
     CROSS JOIN LATERAL (
         SELECT jsonb_object_agg(setting.key,
           CASE WHEN named.usergroup_ptr_id IS NOT NULL THEN to_jsonb(setting.group_id)
           ELSE jsonb_build_object(
             'direct_members',ARRAY(
                 SELECT membership.user_profile_id FROM zulip.zerver_usergroupmembership membership
                 JOIN zulip.zerver_userprofile member ON member.id = membership.user_profile_id
                 WHERE membership.user_group_id = setting.group_id AND member.is_active
                 ORDER BY membership.user_profile_id
             ),
             'direct_subgroups',ARRAY(
                 SELECT membership.subgroup_id FROM zulip.zerver_groupgroupmembership membership
                 WHERE membership.supergroup_id = setting.group_id ORDER BY membership.subgroup_id
             )
           ) END
         ) AS payload
         FROM (VALUES
             ('can_add_subscribers_group',s.can_add_subscribers_group_id),
             ('can_administer_channel_group',s.can_administer_channel_group_id),
             ('can_create_topic_group',s.can_create_topic_group_id),
             ('can_delete_any_message_group',s.can_delete_any_message_group_id),
             ('can_delete_own_message_group',s.can_delete_own_message_group_id),
             ('can_move_messages_out_of_channel_group',s.can_move_messages_out_of_channel_group_id),
             ('can_move_messages_within_channel_group',s.can_move_messages_within_channel_group_id),
             ('can_send_message_group',s.can_send_message_group_id),
             ('can_remove_subscribers_group',s.can_remove_subscribers_group_id),
             ('can_resolve_topics_group',s.can_resolve_topics_group_id),
             ('can_subscribe_group',s.can_subscribe_group_id)
         ) AS setting(key,group_id)
         LEFT JOIN zulip.zerver_namedusergroup AS named ON named.usergroup_ptr_id = setting.group_id
     ) AS groups"
);

#[derive(Serialize)]
struct Projection {
    #[serde(flatten)]
    fields: Map<String, Value>,
    stream_weekly_traffic: Option<i128>,
}

pub(super) async fn read(
    database: &Client,
    viewer: i32,
    realm: i32,
    channel: i64,
) -> Result<Option<String>, Box<dyn Error + Send + Sync>> {
    let Some(row) = database
        .query_opt(SQL, &[&viewer, &realm, &channel])
        .await?
    else {
        return Ok(None);
    };
    let mut payload: Map<String, Value> = serde_json::from_str(row.try_get(0)?)?;
    let count = row.try_get::<_, &str>(2)?.parse::<i128>()?;
    let traffic = average_weekly_messages(row.try_get(1)?, count);
    let legacy_policy = match row.try_get::<_, Option<&str>>(3)? {
        Some("role:administrators") => 2,
        Some("role:fullmembers") => 3,
        Some("role:moderators") => 4,
        _ => 1,
    };
    let topics_policy = match row.try_get::<_, i16>(4)? {
        1 => "inherit",
        2 => "allow_empty_topic",
        3 => "disable_empty_topic",
        4 => "empty_topic_only",
        _ => {
            return Err(
                io::Error::new(io::ErrorKind::InvalidData, "Unknown channel topic policy").into(),
            );
        }
    };
    payload.insert("stream_post_policy".into(), legacy_policy.into());
    payload.insert("is_announcement_only".into(), (legacy_policy == 2).into());
    payload.insert("topics_policy".into(), topics_policy.into());
    // Serialize the aggregate directly as i128, not through a JSON Value.
    Ok(Some(serde_json::to_string(&Projection {
        fields: payload,
        stream_weekly_traffic: traffic,
    })?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_wide_traffic_as_an_exact_json_integer() {
        let projection = Projection {
            fields: Map::new(),
            stream_weekly_traffic: Some(i128::from(u64::MAX) + 1),
        };
        assert_eq!(
            serde_json::to_string(&projection).unwrap(),
            r#"{"stream_weekly_traffic":18446744073709551616}"#
        );
    }
}

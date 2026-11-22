//! Read only documented channel details through the existing metadata gate.

use std::{error::Error, io};

use serde::Serialize;
use serde_json::{Map, Value};
use tokio_postgres::{Client, Row, types::ToSql};

use super::channel_metadata::channel_metadata_visible;
use crate::channel_traffic::average_weekly_messages;

pub(super) const PATH: &str = "/api/v1/streams/{id}";
pub(super) const SESSION_PATH: &str = "/json/streams/{id}";

// Select an explicit payload, never the whole stream model. This remains
// separate from the email-address endpoint, uploads, and message content.
// Share the documented projection without sharing the caller-specific visibility rule.
macro_rules! projection_query {
    ($selector:expr, $extra:literal, $after_projection:literal) => {
        concat!(
            $selector,
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
     s.topics_policy",
            $extra,
            "
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
     ) AS groups",
            $after_projection
        )
    };
}

const DETAILS_SQL: &str = projection_query!(
    concat!(
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
    ),
    "",
    ""
);

const LIST_SQL: &str = projection_query!(
    concat!(
        "WITH RECURSIVE actor AS (
             SELECT id, realm_id, role, is_bot, bot_owner_id FROM zulip.zerver_userprofile
             WHERE id = $1 AND realm_id = $2 AND is_active
         ), member_groups(id) AS (
             SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
             JOIN actor ON actor.id = m.user_profile_id WHERE actor.role <> 600
             UNION
             SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
             JOIN member_groups AS g ON g.id = m.subgroup_id
         ), flags AS (
             SELECT $3::boolean AS exclude_archived, $4::boolean AS include_all,
                    $5::boolean AS include_content, $6::boolean AS include_default,
                    $7::boolean AS include_owner_subscribed, $8::boolean AS include_public,
                    $9::boolean AS include_subscribed, $10::boolean AS include_web_public
         ), owner_unavailable AS (
             SELECT 1 FROM actor CROSS JOIN flags AS f
             WHERE f.include_owner_subscribed AND NOT f.include_all
               AND actor.is_bot AND actor.bot_owner_id IS NULL
         ), visible AS (
             SELECT s.id FROM zulip.zerver_stream AS s CROSS JOIN actor CROSS JOIN flags AS f
             WHERE s.realm_id = actor.realm_id AND (NOT f.exclude_archived OR NOT s.deactivated)
               AND NOT EXISTS (SELECT 1 FROM owner_unavailable)
               AND CASE WHEN f.include_all THEN ",
        channel_metadata_visible!(),
        " ELSE (
                   (CASE WHEN f.include_content THEN (
                       s.is_web_public OR EXISTS (
                           SELECT 1 FROM zulip.zerver_subscription AS own
                           WHERE own.user_profile_id = actor.id AND own.recipient_id = s.recipient_id
                             AND own.active
                       ) OR (actor.role <> 600 AND (NOT s.invite_only OR EXISTS (
                           SELECT 1 FROM member_groups AS g
                           WHERE g.id IN (s.can_subscribe_group_id, s.can_add_subscribers_group_id)
                       )))
                   ) ELSE (
                       (f.include_public AND actor.role <> 600 AND NOT s.invite_only)
                       OR (f.include_web_public AND s.is_web_public AND NOT s.invite_only
                           AND s.history_public_to_subscribers AND NOT s.deactivated)
                       OR (f.include_subscribed AND EXISTS (
                           SELECT 1 FROM zulip.zerver_subscription AS own
                           JOIN zulip.zerver_recipient AS r ON r.id = own.recipient_id
                           WHERE r.type = 2 AND r.type_id = s.id AND own.active
                             AND own.user_profile_id = actor.id
                       ))
                   ) END)
                   OR (f.include_owner_subscribed AND actor.is_bot AND EXISTS (
                       SELECT 1 FROM zulip.zerver_subscription AS owner
                       JOIN zulip.zerver_recipient AS r ON r.id = owner.recipient_id
                       WHERE r.type = 2 AND r.type_id = s.id AND owner.active
                         AND owner.user_profile_id = actor.bot_owner_id
                   ))
               ) END
        "
    ),
    ", CASE WHEN $6::boolean THEN EXISTS (
           SELECT 1 FROM zulip.zerver_defaultstream AS d
           WHERE d.realm_id = s.realm_id AND d.stream_id = s.id
       ) END",
    "
     UNION ALL
     SELECT NULL::text, NULL::bigint, NULL::text, NULL::text, NULL::smallint, NULL::boolean
       FROM owner_unavailable"
);

pub(super) const LIST_PATH: &str = "/api/v1/streams";
pub(super) const LIST_SESSION_PATH: &str = "/json/streams";

pub(super) const LIST_PARAMETERS: &[&str] = &[
    "exclude_archived",
    "include_all",
    "include_all_active",
    "include_can_access_content",
    "include_default",
    "include_owner_subscribed",
    "include_public",
    "include_subscribed",
    "include_web_public",
    "client",
];

pub(super) struct ListOptions {
    exclude_archived: bool,
    include_all: bool,
    include_content: bool,
    include_default: bool,
    include_owner_subscribed: bool,
    include_public: bool,
    include_subscribed: bool,
    include_web_public: bool,
}

impl ListOptions {
    pub(super) fn parse(form: &super::form::Form) -> Result<Self, super::form::FormError> {
        let exclude_archived = form.json_bool("exclude_archived")?.unwrap_or(true);
        let include_all = form.json_bool("include_all")?.unwrap_or(false);
        let include_all_active = form.json_bool("include_all_active")?.unwrap_or(false);
        Ok(Self {
            exclude_archived,
            include_all: include_all || include_all_active,
            include_content: form
                .json_bool("include_can_access_content")?
                .unwrap_or(false),
            include_default: form.json_bool("include_default")?.unwrap_or(false),
            include_owner_subscribed: form.json_bool("include_owner_subscribed")?.unwrap_or(false),
            include_public: form.json_bool("include_public")?.unwrap_or(true),
            include_subscribed: form.json_bool("include_subscribed")?.unwrap_or(true),
            include_web_public: form.json_bool("include_web_public")?.unwrap_or(false),
        })
    }
}

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
        .query_opt(DETAILS_SQL, &[&viewer, &realm, &channel])
        .await?
    else {
        return Ok(None);
    };
    Ok(Some(serde_json::to_string(&project(&row, None)?)?))
}

pub(super) enum ListOutcome {
    MissingBotOwner,
    Listed(String),
}

pub(super) async fn list(
    database: &Client,
    viewer: i32,
    realm: i32,
    options: ListOptions,
) -> Result<ListOutcome, Box<dyn Error + Send + Sync>> {
    let params: [&(dyn ToSql + Sync); 10] = [
        &viewer,
        &realm,
        &options.exclude_archived,
        &options.include_all,
        &options.include_content,
        &options.include_default,
        &options.include_owner_subscribed,
        &options.include_public,
        &options.include_subscribed,
        &options.include_web_public,
    ];
    let rows = database.query(LIST_SQL, &params).await?;
    // The same SQL snapshot makes the sentinel and the projection mutually
    // exclusive. Never return even a partial list for Django's owner error.
    if let Some(first) = rows.first()
        && first.try_get::<_, Option<&str>>(0)?.is_none()
    {
        return Ok(ListOutcome::MissingBotOwner);
    }
    let mut streams = rows
        .iter()
        .map(|row| project(row, row.try_get(5)?))
        .collect::<Result<Vec<_>, _>>()?;
    // PostgreSQL's locale sort is not Python's Unicode scalar order.
    streams.sort_by(|left, right| left.name().cmp(right.name()));
    Ok(ListOutcome::Listed(serde_json::to_string(&streams)?))
}

impl Projection {
    fn name(&self) -> &str {
        self.fields["name"]
            .as_str()
            .expect("stream names are strings")
    }
}

fn project(
    row: &Row,
    is_default: Option<bool>,
) -> Result<Projection, Box<dyn Error + Send + Sync>> {
    let mut payload: Map<String, Value> = serde_json::from_str(row.try_get(0)?)?;
    if let Some(is_default) = is_default {
        payload.insert("is_default".into(), is_default.into());
    }
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
    Ok(Projection {
        fields: payload,
        stream_weekly_traffic: traffic,
    })
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

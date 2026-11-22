//! Unrouted request operations for group membership and subgroup lists.

use std::collections::BTreeSet;

use serde::Serialize;
use tokio_postgres::GenericClient;

use super::group_listing_request::{ReadError, read_options, require_non_guest};

const GROUP_DETAILS: &str = "
    WITH RECURSIVE target AS (
        SELECT usergroup_ptr_id, deactivated
          FROM zulip.zerver_namedusergroup
         WHERE realm_id = $1 AND usergroup_ptr_id = $2
    ), seed(group_id) AS (
        SELECT usergroup_ptr_id FROM target WHERE NOT deactivated AND $4
        UNION
        SELECT edge.subgroup_id
          FROM target
          JOIN zulip.zerver_groupgroupmembership AS edge ON edge.supergroup_id = target.usergroup_ptr_id
         WHERE NOT target.deactivated AND NOT $4
    ), reachable(group_id) AS (
        SELECT group_id FROM seed
        UNION
        SELECT edge.subgroup_id
          FROM reachable
          JOIN zulip.zerver_groupgroupmembership AS edge ON edge.supergroup_id = reachable.group_id
         WHERE NOT $3
    )
    SELECT deactivated,
        CASE WHEN $4 THEN ARRAY(
            SELECT DISTINCT member.user_profile_id::bigint
              FROM reachable
              JOIN zulip.zerver_usergroupmembership AS member ON member.user_group_id = reachable.group_id
              JOIN zulip.zerver_userprofile AS profile ON profile.id = member.user_profile_id
             WHERE profile.is_active
             ORDER BY member.user_profile_id::bigint
        ) ELSE ARRAY(
            SELECT group_id FROM reachable ORDER BY group_id
        ) END AS ids
    FROM target";

#[derive(Clone, Copy, Debug, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetailKind {
    Members,
    Subgroups,
}

impl DetailKind {
    pub fn direct_parameter(self) -> &'static str {
        match self {
            Self::Members => "direct_member_only",
            Self::Subgroups => "direct_subgroup_only",
        }
    }
}

#[derive(Debug)]
pub enum DetailReadError {
    Request(ReadError),
    InvalidGroup,
    DeactivatedGroup,
}

impl From<ReadError> for DetailReadError {
    fn from(error: ReadError) -> Self {
        Self::Request(error)
    }
}

impl From<tokio_postgres::Error> for DetailReadError {
    fn from(error: tokio_postgres::Error) -> Self {
        Self::Request(ReadError::Database(error.into()))
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
enum DetailIds {
    Members(Vec<i64>),
    Subgroups(Vec<i64>),
}

#[derive(Debug, Serialize)]
pub struct GroupDetailData {
    #[serde(flatten)]
    ids: DetailIds,
    #[serde(skip_serializing_if = "BTreeSet::is_empty")]
    ignored_parameters_unsupported: BTreeSet<String>,
}

/// Execute a read after the caller's existing authentication and realm checks.
/// The shared non-guest restriction precedes query validation, which in turn
/// precedes the named group lookup scoped to the authenticated realm.
///
/// The SQL seeds member traversal with the root and subgroup traversal with its
/// direct children. PostgreSQL UNION deduplicates shared descendants before
/// member lookup. Result order is unspecified by Django's API and is made
/// deterministic here. This operation is not registered with a router.
pub async fn read_authenticated_group_detail(
    db: &impl GenericClient,
    authenticated_user_id: i32,
    authenticated_realm_id: i32,
    group_id: i64,
    kind: DetailKind,
    query: Option<&str>,
) -> Result<GroupDetailData, DetailReadError> {
    require_non_guest(db, authenticated_user_id, authenticated_realm_id).await?;
    let options = read_options(query, kind.direct_parameter())?;
    let row = db
        .query_opt(
            GROUP_DETAILS,
            &[
                &authenticated_realm_id,
                &group_id,
                &options.enabled,
                &matches!(kind, DetailKind::Members),
            ],
        )
        .await?
        .ok_or(DetailReadError::InvalidGroup)?;
    if row.try_get("deactivated")? {
        return Err(DetailReadError::DeactivatedGroup);
    }
    let ids = row.try_get("ids")?;
    Ok(GroupDetailData {
        ids: match kind {
            DetailKind::Members => DetailIds::Members(ids),
            DetailKind::Subgroups => DetailIds::Subgroups(ids),
        },
        ignored_parameters_unsupported: options.ignored_parameters,
    })
}

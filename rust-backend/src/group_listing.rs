//! Data loading for user-group reads. It is not an authorization check.

use std::{collections::HashSet, error::Error, io::ErrorKind};

use serde::Serialize;
use tokio_postgres::GenericClient;

use crate::group_membership::group_members;

const GROUPS: &str = "
    SELECT usergroup_ptr_id, name, description, creator_id,
           trunc(extract(epoch FROM date_created))::bigint AS created_timestamp,
           is_system_group, deactivated,
           can_add_members_group_id, can_join_group_id, can_leave_group_id,
           can_manage_group_id, can_mention_group_id, can_remove_members_group_id
      FROM zulip.zerver_namedusergroup
     WHERE realm_id = $1 AND ($2 OR NOT deactivated)
     ORDER BY usergroup_ptr_id";

const MEMBERSHIPS: &str = "
    SELECT TRUE AS is_user, m.user_group_id AS group_id, m.user_profile_id::bigint AS member_id
      FROM zulip.zerver_usergroupmembership AS m
      JOIN zulip.zerver_userprofile AS u ON u.id = m.user_profile_id
     WHERE m.user_group_id = ANY($1) AND u.is_active
    UNION
    SELECT FALSE AS is_user, g.supergroup_id AS group_id, g.subgroup_id AS member_id
      FROM zulip.zerver_groupgroupmembership AS g
     WHERE g.supergroup_id = ANY($1)";

#[derive(Serialize, Debug)]
struct DirectMembership {
    direct_members: Vec<i64>,
    direct_subgroups: Vec<i64>,
}

#[derive(Serialize, Debug)]
#[serde(untagged)]
enum GroupSetting {
    Named(i64),
    Inline(DirectMembership),
}

#[derive(Serialize, Debug)]
pub struct ApiUserGroup {
    id: i64,
    name: String,
    creator_id: Option<i32>,
    date_created: Option<i64>,
    description: String,
    members: Vec<i64>,
    direct_subgroup_ids: Vec<i64>,
    is_system_group: bool,
    can_add_members_group: GroupSetting,
    can_join_group: GroupSetting,
    can_leave_group: GroupSetting,
    can_manage_group: GroupSetting,
    can_mention_group: GroupSetting,
    can_remove_members_group: GroupSetting,
    deactivated: bool,
}

struct SelectedGroup {
    id: i64,
    name: String,
    creator_id: Option<i32>,
    date_created: Option<i64>,
    description: String,
    is_system_group: bool,
    deactivated: bool,
    settings: [i64; 6],
}

/// Load the data used by Django's list operation for an already-authorized realm.
///
/// Callers must authorize the requester independently before calling this
/// function. The opt-in pilot does so in its post-authentication operation.
pub async fn load_user_group_listing(
    db: &impl GenericClient,
    realm_id: i32,
    include_deactivated: bool,
) -> Result<Vec<ApiUserGroup>, Box<dyn Error + Send + Sync>> {
    let mut selected = Vec::new();
    for row in db.query(GROUPS, &[&realm_id, &include_deactivated]).await? {
        selected.push(SelectedGroup {
            id: row.try_get("usergroup_ptr_id")?,
            name: row.try_get("name")?,
            creator_id: row.try_get("creator_id")?,
            date_created: row.try_get("created_timestamp")?,
            description: row.try_get("description")?,
            is_system_group: row.try_get("is_system_group")?,
            deactivated: row.try_get("deactivated")?,
            settings: [
                row.try_get("can_add_members_group_id")?,
                row.try_get("can_join_group_id")?,
                row.try_get("can_leave_group_id")?,
                row.try_get("can_manage_group_id")?,
                row.try_get("can_mention_group_id")?,
                row.try_get("can_remove_members_group_id")?,
            ],
        });
    }
    if selected.is_empty() {
        return Ok(Vec::new());
    }

    let selected_ids: HashSet<_> = selected.iter().map(|group| group.id).collect();
    let membership_ids: Vec<_> = selected_ids
        .iter()
        .copied()
        .chain(selected.iter().flat_map(|group| group.settings))
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    let mut rows = Vec::new();
    for row in db.query(MEMBERSHIPS, &[&membership_ids]).await? {
        rows.push((
            row.try_get("is_user")?,
            row.try_get("group_id")?,
            row.try_get("member_id")?,
        ));
    }
    let memberships = group_members(&membership_ids, rows).ok_or_else(|| {
        std::io::Error::new(
            ErrorKind::InvalidData,
            "unexpected user group membership row",
        )
    })?;
    let setting = |id| {
        if selected_ids.contains(&id) {
            GroupSetting::Named(id)
        } else {
            let members = &memberships[&id];
            GroupSetting::Inline(DirectMembership {
                direct_members: members.users.clone(),
                direct_subgroups: members.subgroups.clone(),
            })
        }
    };

    Ok(selected
        .into_iter()
        .map(|group| {
            let members = &memberships[&group.id];
            let [add, join, leave, manage, mention, remove] = group.settings;
            ApiUserGroup {
                id: group.id,
                name: group.name,
                creator_id: group.creator_id,
                date_created: group.date_created,
                description: group.description,
                members: members.users.clone(),
                direct_subgroup_ids: members.subgroups.clone(),
                is_system_group: group.is_system_group,
                can_add_members_group: setting(add),
                can_join_group: setting(join),
                can_leave_group: setting(leave),
                can_manage_group: setting(manage),
                can_mention_group: setting(mention),
                can_remove_members_group: setting(remove),
                deactivated: group.deactivated,
            }
        })
        .collect())
}

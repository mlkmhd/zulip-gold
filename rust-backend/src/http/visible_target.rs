//! Shared Django-style user visibility for authenticated read endpoints.

// The caller supplies only compile-time fragments to match the route's
// bot-target and group requirements. No request text is inserted here.
macro_rules! visible_target_ctes {
    ($group_filter:literal, $bot_visible:literal, $target_filter:literal) => {
        concat!(
            "WITH RECURSIVE actor AS (
         SELECT id, realm_id, role FROM zulip.zerver_userprofile
         WHERE id = $1 AND realm_id = $2 AND is_active
     ), member_groups(id) AS (
         SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
         JOIN actor ON actor.id = m.user_profile_id",
            $group_filter,
            "
         UNION
         SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
         JOIN member_groups AS g ON g.id = m.subgroup_id
     ), target AS (
         SELECT u.id, u.presence_enabled, CASE
             WHEN NOT u.is_active THEN 'deactivated'
             WHEN ",
            $bot_visible,
            "u.id = actor.id OR actor.role <> 600 OR policy.name = 'role:everyone'
               OR EXISTS (
                   SELECT 1 FROM member_groups AS g WHERE g.id = policy.usergroup_ptr_id
               )
               OR ",
            shares_recipient!(),
            " THEN 'visible'
             ELSE 'restricted'
         END AS visibility
         FROM zulip.zerver_userprofile AS u CROSS JOIN actor
         JOIN zulip.zerver_realm AS realm ON realm.id = actor.realm_id
         JOIN zulip.zerver_namedusergroup AS policy
           ON policy.usergroup_ptr_id = realm.can_access_all_users_group_id
          AND policy.realm_id = realm.id AND policy.is_system_group
          AND policy.name IN ('role:everyone', 'role:members')
         WHERE u.id = $3 AND u.realm_id = actor.realm_id ",
            $target_filter,
            "
     )
",
        )
    };
}

pub(super) use visible_target_ctes;

// Stored values match UserProfile roles.
const OWNER: i16 = 100;
const ADMINISTRATOR: i16 = 200;
const MODERATOR: i16 = 300;
const MEMBER: i16 = 400;
const GUEST: i16 = 600;

pub(crate) fn has_system_group_permission(
    role: i16,
    provisional_member: bool,
    group: &str,
) -> bool {
    match group {
        "role:nobody" => false,
        "role:internet" | "role:everyone" => true,
        _ if role == GUEST => false,
        "role:members" => true,
        "role:owners" => role == OWNER,
        "role:administrators" => matches!(role, OWNER | ADMINISTRATOR),
        "role:moderators" => matches!(role, OWNER | ADMINISTRATOR | MODERATOR),
        _ => role != MEMBER || !provisional_member,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn applies_role_hierarchy_and_provisional_membership() {
        assert!(!has_system_group_permission(OWNER, false, "role:nobody"));
        assert!(has_system_group_permission(GUEST, true, "role:internet"));
        assert!(has_system_group_permission(GUEST, true, "role:everyone"));
        assert!(!has_system_group_permission(GUEST, false, "role:members"));
        assert!(has_system_group_permission(MEMBER, true, "role:members"));
        assert!(!has_system_group_permission(
            MEMBER,
            true,
            "role:fullmembers"
        ));
        assert!(has_system_group_permission(
            MEMBER,
            false,
            "role:fullmembers"
        ));
        assert!(has_system_group_permission(
            MODERATOR,
            false,
            "role:fullmembers"
        ));
        assert!(!has_system_group_permission(
            MODERATOR,
            false,
            "role:administrators"
        ));
        assert!(has_system_group_permission(
            ADMINISTRATOR,
            false,
            "role:moderators"
        ));
        assert!(!has_system_group_permission(
            ADMINISTRATOR,
            false,
            "role:owners"
        ));
        assert!(has_system_group_permission(OWNER, false, "role:owners"));
    }
}

use std::collections::HashMap;

#[derive(Default, PartialEq, Eq, Debug)]
pub(crate) struct Members {
    pub(crate) users: Vec<i64>,
    pub(crate) subgroups: Vec<i64>,
}

pub(crate) fn group_members(
    groups: &[i64],
    rows: impl IntoIterator<Item = (bool, i64, i64)>,
) -> Option<HashMap<i64, Members>> {
    let mut memberships: HashMap<_, _> =
        groups.iter().map(|&id| (id, Members::default())).collect();
    for (is_user, group, member) in rows {
        let entry = memberships.get_mut(&group)?;
        if is_user {
            entry.users.push(member);
        } else {
            entry.subgroups.push(member);
        }
    }
    for entry in memberships.values_mut() {
        entry.users.sort_unstable();
        entry.subgroups.sort_unstable();
    }
    Some(memberships)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_empty_groups_and_sorts_each_kind_separately() {
        let groups = [11, 17];
        let rows = [
            (true, 11, 42),
            (false, 11, 42),
            (true, 11, 3),
            (true, 11, 3),
        ];
        let grouped = group_members(&groups, rows).unwrap();
        assert_eq!(
            grouped[&11],
            Members {
                users: vec![3, 3, 42],
                subgroups: vec![42]
            }
        );
        assert_eq!(grouped[&17], Members::default());
        assert!(group_members(&groups, [(false, 21, 4)]).is_none());
    }
}

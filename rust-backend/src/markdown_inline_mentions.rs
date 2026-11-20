//! Prepare atomic mention spans and notification effects without mutating a renderer.

use std::collections::HashMap;

#[derive(Debug, PartialEq, Eq)]
pub enum MentionNotification {
    User(i32),
    Group(i32),
    Channel,
    Topic,
}

#[derive(Debug, PartialEq, Eq)]
pub struct MentionSpan {
    pub attributes: Vec<(&'static str, String)>,
    pub text: String,
    pub notification: Option<MentionNotification>,
}

pub struct MentionUser<'a> {
    pub id: i32,
    pub full_name: &'a str,
    pub is_active: bool,
}

/// Prepare display fields from an already-resolved user, including wildcards.
///
/// The caller must perform the `name|id` consistency check first, or use
/// `prepare_user`. Text is atomic and attribute values still need HTML escaping.
pub fn user_mention_span(
    name: &str,
    mut silent: bool,
    user: Option<MentionUser<'_>>,
) -> Option<MentionSpan> {
    let mut attributes = Vec::new();
    let (name, class, notification) = match name {
        "all" | "everyone" | "stream" | "channel" => {
            attributes.push(("data-user-id", "*".to_owned()));
            (
                name,
                "user-mention channel-wildcard-mention",
                MentionNotification::Channel,
            )
        }
        "topic" => (name, "topic-mention", MentionNotification::Topic),
        _ => {
            let user = user?;
            silent |= !user.is_active;
            attributes.push(("data-user-id", user.id.to_string()));
            (
                user.full_name,
                "user-mention",
                MentionNotification::User(user.id),
            )
        }
    };
    attributes.push((
        "class",
        if silent {
            format!("{class} silent")
        } else {
            class.to_owned()
        },
    ));
    Some(MentionSpan {
        attributes,
        text: if silent {
            name.to_owned()
        } else {
            format!("@{name}")
        },
        notification: (!silent).then_some(notification),
    })
}

pub fn group_mention_span(
    group_id: i32,
    display_name: &str,
    silent: bool,
    deactivated: bool,
) -> MentionSpan {
    let silent = silent || deactivated;
    MentionSpan {
        attributes: vec![
            ("data-user-group-id", group_id.to_string()),
            (
                "class",
                if silent {
                    "user-group-mention silent"
                } else {
                    "user-group-mention"
                }
                .to_owned(),
            ),
        ],
        text: if silent {
            display_name.to_owned()
        } else {
            format!("@{display_name}")
        },
        notification: (!silent).then_some(MentionNotification::Group(group_id)),
    }
}

/// Decimal property snapshot from the current renderer's Unicode database.
///
/// Include every non-ASCII decimal digit with its value in `0..=9`. The limit
/// is the live CPython `int` decimal conversion limit; zero disables it.
pub struct UserPolicy {
    pub unicode_decimal: HashMap<char, u8>,
    pub max_id_digits: usize,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Unsupported;

#[derive(Debug, PartialEq, Eq)]
pub enum UserReference<'a> {
    Name(&'a str),
    Id(i32),
}

/// Resolve the reference handler's ID syntax before preparing mention fields.
///
/// Lookup receives the unmodified name or a numeric ID, and must use an
/// authorized snapshot with the host's lowercase rules and duplicate-name
/// ordering. Wildcards still perform the lookup before overriding its result.
/// Lookup must not mutate host state, since a later handler can delegate.
/// IDs outside PostgreSQL's integer range cannot resolve a user.
/// Unsupported conversion limits require whole-plan delegation so Python can
/// preserve its exception behavior. This never changes notification state.
pub fn prepare_user<'a>(
    name: &str,
    silent: bool,
    policy: &UserPolicy,
    lookup: impl FnOnce(UserReference<'_>) -> Option<MentionUser<'a>>,
) -> Result<Option<MentionSpan>, Unsupported> {
    let mut full_name = None;
    let mut reference = UserReference::Name(name);
    if let Some((prefix, suffix)) = name.rsplit_once('|')
        && !prefix.contains('\n')
    {
        // A Python '$' anchor also matches before one final newline.
        let suffix = suffix.strip_suffix('\n').unwrap_or(suffix);
        let mut digits = 0_usize;
        let mut id = Some(0_i32);
        let mut decimal = !suffix.is_empty();
        for character in suffix.chars() {
            let digit = if character.is_ascii_digit() {
                Some(character as u8 - b'0')
            } else {
                policy.unicode_decimal.get(&character).copied()
            };
            let Some(digit) = digit else {
                decimal = false;
                break;
            };
            if digit > 9 {
                return Err(Unsupported);
            }
            digits += 1;
            id = id.and_then(|id| id.checked_mul(10)?.checked_add(i32::from(digit)));
        }
        if decimal {
            if policy.max_id_digits != 0 && digits > policy.max_id_digits {
                return Err(Unsupported);
            }
            let Some(id) = id else {
                return Ok(None);
            };
            full_name = (!prefix.is_empty()).then_some(prefix);
            reference = UserReference::Id(id);
        }
    }
    let user = lookup(reference);
    if let (Some(expected), Some(user)) = (full_name, user.as_ref())
        && expected != user.full_name
    {
        return Ok(None);
    }
    Ok(user_mention_span(name, silent, user))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inactive_users_are_silent_and_use_the_canonical_name() {
        let span = user_mention_span(
            "alias|42",
            false,
            Some(MentionUser {
                id: 42,
                full_name: "<User & 📝>",
                is_active: false,
            }),
        )
        .unwrap();
        assert_eq!(span.text, "<User & 📝>");
        assert_eq!(span.notification, None);
        assert_eq!(
            span.attributes,
            vec![
                ("data-user-id", "42".to_owned()),
                ("class", "user-mention silent".to_owned())
            ]
        );
        assert_eq!(user_mention_span("unknown", false, None), None);
    }

    #[test]
    fn wildcards_do_not_require_a_user_and_keep_their_name() {
        for name in ["all", "everyone", "stream", "channel", "topic"] {
            let span = user_mention_span(name, false, None).unwrap();
            assert_eq!(span.text, format!("@{name}"));
            assert_eq!(
                span.notification,
                Some(if name == "topic" {
                    MentionNotification::Topic
                } else {
                    MentionNotification::Channel
                })
            );
            assert_eq!(
                user_mention_span(name, true, None).unwrap().notification,
                None
            );
        }
        assert_eq!(user_mention_span("Everyone", false, None), None);
    }

    #[test]
    fn group_deactivation_overrides_notification_syntax() {
        let span = group_mention_span(15, "Administrators", false, true);
        assert_eq!(span.text, "Administrators");
        assert_eq!(span.notification, None);
        let span = group_mention_span(15, "<Group & 📝>", false, false);
        assert_eq!(span.text, "@<Group & 📝>");
        assert_eq!(span.notification, Some(MentionNotification::Group(15)));
    }
}

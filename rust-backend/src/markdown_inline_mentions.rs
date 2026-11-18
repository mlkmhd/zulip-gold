#[derive(Debug, PartialEq, Eq)]
pub(crate) enum MentionNotification {
    User(i32),
    Group(i32),
    Channel,
    Topic,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct MentionSpan {
    pub attributes: Vec<(&'static str, String)>,
    pub text: String,
    pub notification: Option<MentionNotification>,
}

pub(crate) struct MentionUser<'a> {
    pub id: i32,
    pub full_name: &'a str,
    pub is_active: bool,
}

pub(crate) fn user_mention_span(
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

pub(crate) fn group_mention_span(
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

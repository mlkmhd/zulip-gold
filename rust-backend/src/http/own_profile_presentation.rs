//! Pure presentation choices for the already-authenticated self profile.

use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AvatarKind {
    SystemBot,
    Stored,
    Default,
}

pub struct ResolvedAvatar<'a> {
    pub user_id: i32,
    pub realm_id: i32,
    pub version: i16,
    pub kind: AvatarKind,
    pub url: &'a str,
}

/// Trusted values for this account, never derived from a request. The caller
/// still owns storage, hashing, configuration, and URL construction; the
/// projection will not use an avatar resolved for a different account, version or source.
pub struct Presentation<'a> {
    pub avatar: ResolvedAvatar<'a>,
    pub system_bot_emails: &'a BTreeSet<String>,
    pub timezone_aliases: &'a TimezoneAliases,
}

/// Match Django's registry comparison on the supplied email. Callers use the
/// public account email for bot metadata and the delivery email for avatars.
pub fn system_bot(email: &str, registered: &BTreeSet<String>) -> bool {
    registered.contains(&email.to_lowercase())
}

/// For the self-profile endpoint Django always sets client_gravatar=false.
pub fn avatar_kind(
    delivery_email: &str,
    source: &str,
    registered: &BTreeSet<String>,
) -> AvatarKind {
    if system_bot(delivery_email, registered) {
        AvatarKind::SystemBot
    } else if matches!(source, "U" | "J") {
        AvatarKind::Stored
    } else {
        AvatarKind::Default
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidAliasLine(pub usize);

pub struct TimezoneAliases(BTreeMap<String, String>);

impl TimezoneAliases {
    /// Parse the same trusted tzdata.zi source used by Django. zic permits
    /// any nonempty prefix of "Link", and the last record for an alias wins.
    pub fn parse(source: &str) -> Result<Self, InvalidAliasLine> {
        let mut aliases = BTreeMap::new();
        for (index, line) in source.lines().enumerate() {
            let mut parts = line.split_whitespace();
            let Some(kind) = parts.next() else { continue };
            if !"link".starts_with(&kind.to_ascii_lowercase()) {
                continue;
            }
            let (Some(name), Some(alias), None) = (parts.next(), parts.next(), parts.next()) else {
                return Err(InvalidAliasLine(index + 1));
            };
            aliases.insert(alias.to_owned(), name.to_owned());
        }
        Ok(Self(aliases))
    }

    pub fn canonical<'a>(&'a self, name: &'a str) -> &'a str {
        self.0.get(name).map_or(name, String::as_str)
    }

    pub fn entries(&self) -> &BTreeMap<String, String> {
        &self.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aliases_follow_django_prefix_duplicate_and_unknown_rules() {
        let aliases = TimezoneAliases::parse(
            "Z Etc/UTC 0 - UTC\nLi Etc/UTC Test/Clock\nLINK Region/Final Test/Clock\n",
        )
        .unwrap();
        assert_eq!(aliases.canonical("Test/Clock"), "Region/Final");
        assert_eq!(aliases.canonical("Unknown/Clock"), "Unknown/Clock");
        assert_eq!(aliases.canonical(""), "");
        assert!(matches!(
            TimezoneAliases::parse("# comment\nl Etc/UTC"),
            Err(InvalidAliasLine(2))
        ));
    }

    #[test]
    fn registered_system_avatar_takes_precedence_over_the_stored_source() {
        let registered = BTreeSet::from(["welcome-bot@example.test".into()]);
        assert_eq!(
            avatar_kind("WELCOME-BOT@example.test", "U", &registered),
            AvatarKind::SystemBot
        );
        assert_eq!(
            avatar_kind("member@example.test", "J", &registered),
            AvatarKind::Stored
        );
        assert_eq!(
            avatar_kind("member@example.test", "G", &registered),
            AvatarKind::Default
        );
    }
}

use std::sync::LazyLock;

use regex::{Captures, Regex};

static USER_AGENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<name>[^/ ]*[^0-9/(]*)(?:/(?P<version>[^/ ]*))?(?:[ /].*)?$").unwrap()
});

pub(crate) fn parse(value: &str) -> Option<Captures<'_>> {
    USER_AGENT.captures(value)
}

#[cfg(feature = "python")]
pub(crate) fn numeric_version_lt(first: &str, second: &str) -> Option<bool> {
    fn numeric_parts(value: &str) -> Option<Vec<u64>> {
        value
            .split('.')
            .map(|part| {
                if part.is_empty()
                    || part.len() > 19
                    || !part.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return None;
                }
                part.parse().ok()
            })
            .collect()
    }
    Some(numeric_parts(first)? < numeric_parts(second)?)
}

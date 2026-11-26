use std::sync::LazyLock;

use regex::{Captures, Regex};

static USER_AGENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<name>[^/ ]*[^0-9/(]*)(?:/(?P<version>[^/ ]*))?(?:[ /].*)?$").unwrap()
});

pub(crate) fn parse(value: &str) -> Option<Captures<'_>> {
    USER_AGENT.captures(value)
}

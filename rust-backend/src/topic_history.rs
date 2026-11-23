//! Case-insensitive projection of visible topics, newest message first.

use std::collections::HashSet;

#[cfg_attr(feature = "http", derive(serde::Serialize))]
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Topic<T> {
    pub name: T,
    pub max_id: i64,
}

pub(crate) struct NewestTopicHistory<T> {
    seen: HashSet<String>,
    topics: Vec<Topic<T>>,
    empty_fallback: Option<T>,
}

impl<T: Clone> NewestTopicHistory<T> {
    pub(crate) fn new(empty_fallback: Option<T>) -> Self {
        Self {
            seen: HashSet::new(),
            topics: Vec::new(),
            empty_fallback,
        }
    }

    /// Rows must arrive in strictly descending order of message ID.
    /// The caller supplies the lowercase key under the active Python Unicode
    /// rules. The empty label changes only the display, not that key.
    pub(crate) fn push(&mut self, name: T, key: String, is_empty: bool, max_id: i64) {
        if self.seen.insert(key) {
            let name = if is_empty {
                self.empty_fallback.clone().unwrap_or(name)
            } else {
                name
            };
            self.topics.push(Topic { name, max_id });
        }
    }

    pub(crate) fn finish(self) -> Vec<Topic<T>> {
        self.topics
    }
}

#[cfg(test)]
mod tests {
    use super::{NewestTopicHistory, Topic};

    #[test]
    fn newest_spelling_wins_and_the_legacy_label_does_not_change_the_key() {
        let mut history = NewestTopicHistory::new(Some("general chat"));
        history.push("CaSe", "case".into(), false, 4);
        history.push("", "".into(), true, 3);
        history.push("general chat", "general chat".into(), false, 2);
        history.push("CASE", "case".into(), false, 1);
        assert_eq!(
            history.finish(),
            [
                Topic {
                    name: "CaSe",
                    max_id: 4,
                },
                Topic {
                    name: "general chat",
                    max_id: 3,
                },
                Topic {
                    name: "general chat",
                    max_id: 2,
                },
            ]
        );
    }
}

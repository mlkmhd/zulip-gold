use std::collections::{HashMap, HashSet};

use aho_corasick::{AhoCorasick, BuildError};

const ALLOWED_BEFORE: &[u8] = b" \n(\".,';[*`>";
// The Python after-punctuation set contains a two-character entry for a
// quotation mark followed by a comma. A lone quotation mark is rejected.
const ALLOWED_AFTER: &[u8] = b" \n)?:.,';]!*`";

pub(crate) struct AlertWordMatcher {
    automaton: AhoCorasick,
    user_ids_by_pattern: Vec<HashSet<i32>>,
}

impl AlertWordMatcher {
    pub(crate) fn new(words_by_user: Vec<(i32, Vec<Vec<u8>>)>) -> Result<Self, BuildError> {
        let mut users_by_word: HashMap<_, HashSet<_>> = HashMap::new();
        for (user_id, words) in words_by_user {
            for word in words.into_iter().filter(|word| !word.is_empty()) {
                users_by_word.entry(word).or_default().insert(user_id);
            }
        }
        let (words, user_ids_by_pattern): (Vec<_>, Vec<_>) = users_by_word.into_iter().unzip();
        Ok(Self {
            automaton: AhoCorasick::new(words)?,
            user_ids_by_pattern,
        })
    }

    pub(crate) fn match_user_ids(&self, text: &[u8]) -> HashSet<i32> {
        let mut user_ids = HashSet::new();
        for found in self.automaton.find_overlapping_iter(text) {
            // The accepted boundary characters are ASCII, so a UTF-8 byte
            // adjacent to the match can be tested directly.
            let valid_start =
                found.start() == 0 || ALLOWED_BEFORE.contains(&text[found.start() - 1]);
            let valid_end = found.end() == text.len() || ALLOWED_AFTER.contains(&text[found.end()]);
            if valid_start && valid_end {
                user_ids.extend(&self.user_ids_by_pattern[found.pattern().as_usize()]);
            }
        }
        user_ids
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collects_all_matching_users_with_overlapping_words() {
        let matcher = AlertWordMatcher::new(vec![
            (1, vec![b"hello".to_vec(), b"world".to_vec()]),
            (2, vec![b"hello world".to_vec()]),
            (3, vec![b"world".to_vec(), "☃".as_bytes().to_vec()]),
            (4, vec![Vec::new()]),
        ])
        .unwrap();
        assert_eq!(matcher.match_user_ids(b"(hello world)!"), [1, 2, 3].into());
        assert_eq!(matcher.match_user_ids(b"hello\""), HashSet::new());
        assert_eq!(matcher.match_user_ids("☃".as_bytes()), [3].into());
        assert_eq!(
            matcher.match_user_ids("abc☃\n☃ش".as_bytes()),
            HashSet::new()
        );
    }
}

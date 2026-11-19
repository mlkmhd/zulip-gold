use std::sync::LazyLock;

use regex::Regex;

const PREFIX: &str = "\u{2}klzzwxh:";
static PLACEHOLDER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new("\u{2}klzzwxh:([0-9]+)\u{3}").unwrap());

pub struct Step {
    pub text: String,
    pub rejected_text: Option<String>,
    pub identifier: Option<String>,
}

pub struct Scanner {
    data: String,
    offset: usize,
    pending: Option<(usize, usize)>,
}

impl Scanner {
    pub fn new(data: String) -> Self {
        Self {
            data,
            offset: 0,
            pending: None,
        }
    }

    pub fn next(&mut self, accepted: bool) -> Option<Step> {
        if let Some((accepted_end, rejected_end)) = self.pending.take() {
            self.offset = if accepted { accepted_end } else { rejected_end };
        }
        if self.offset == self.data.len() {
            return None;
        }
        let Some(relative) = self.data[self.offset..].find(PREFIX) else {
            let text = self.data[self.offset..].to_owned();
            self.offset = self.data.len();
            return Some(Step {
                text,
                rejected_text: None,
                identifier: None,
            });
        };
        let index = self.offset + relative;
        let captures = PLACEHOLDER.captures(&self.data[index..]);
        let identifier = captures.as_ref().map(|captures| captures[1].to_owned());
        // The reference searches beyond a malformed prefix for the next match.
        let accepted_end = captures
            .map(|captures| index + captures.get(0).unwrap().end())
            .unwrap_or(index + 1);
        let rejected_end = index + PREFIX.len();
        self.pending = Some((accepted_end, rejected_end));
        Some(Step {
            text: self.data[self.offset..index].to_owned(),
            rejected_text: Some(self.data[self.offset..rejected_end].to_owned()),
            identifier,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::Scanner;

    #[test]
    fn preserves_unicode_text_and_identifier_zeroes() {
        let mut scanner = Scanner::new("فارسی \u{2}klzzwxh:0001\u{3} tail".to_owned());
        let step = scanner.next(false).unwrap();
        assert_eq!(step.text, "فارسی ");
        assert_eq!(step.identifier.as_deref(), Some("0001"));
        assert_eq!(scanner.next(true).unwrap().text, " tail");
        assert!(scanner.next(false).is_none());
    }

    #[test]
    fn finds_later_valid_placeholder_after_malformed_prefix() {
        let data = "\u{2}klzzwxh:bad \u{2}klzzwxh:42\u{3} end";
        let mut scanner = Scanner::new(data.to_owned());
        let step = scanner.next(false).unwrap();
        assert_eq!(step.identifier.as_deref(), Some("42"));
        assert_eq!(step.rejected_text.as_deref(), Some("\u{2}klzzwxh:"));
        assert_eq!(scanner.next(true).unwrap().text, " end");
        let mut scanner = Scanner::new(data.to_owned());
        scanner.next(false).unwrap();
        let step = scanner.next(false).unwrap();
        assert_eq!(step.text, "bad ");
        assert_eq!(step.identifier.as_deref(), Some("42"));
        assert_eq!(scanner.next(false).unwrap().text, "42\u{3} end");
    }

    #[test]
    fn leaves_non_ascii_digits_and_unterminated_placeholders_as_text() {
        let mut scanner = Scanner::new("\u{2}klzzwxh:۴۲\u{3}".to_owned());
        assert!(scanner.next(false).unwrap().identifier.is_none());
        assert_eq!(scanner.next(false).unwrap().text, "۴۲\u{3}");
        assert!(scanner.next(false).is_none());
        assert!(Scanner::new(String::new()).next(false).is_none());
    }
}

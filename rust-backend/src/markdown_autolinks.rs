//! Match the reference autolinker without Python regex callbacks.

use std::collections::VecDeque;
use std::ops::Range;
use std::sync::{Arc, Mutex, OnceLock};

use regex::{Regex, RegexBuilder};

use crate::draft_text::is_python_whitespace;
pub use crate::markdown_autolink_data::UNICODE_VERSION;
use crate::markdown_autolink_data::WORD;

#[derive(Clone, PartialEq, Eq)]
pub struct Spec {
    pub tlds: Vec<String>,
    pub schemes: Vec<String>,
    pub enable_file_links: bool,
}

pub struct Matcher {
    expression: Regex,
}

type Cache = VecDeque<(Spec, Arc<Matcher>)>;

impl Matcher {
    pub fn cached(spec: Spec) -> Option<Arc<Self>> {
        static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();
        let mut cache = CACHE.get_or_init(Mutex::default).lock().ok()?;
        if let Some(index) = cache.iter().position(|(key, _)| *key == spec) {
            let entry = cache.remove(index)?;
            let result = Arc::clone(&entry.1);
            cache.push_front(entry);
            return Some(result);
        }
        let result = Arc::new(Self::new(&spec)?);
        cache.push_front((spec, Arc::clone(&result)));
        cache.truncate(4);
        Some(result)
    }

    pub fn new(spec: &Spec) -> Option<Self> {
        if spec.tlds.is_empty()
            || spec.tlds.len() > 4096
            || spec.schemes.is_empty()
            || spec.schemes.len() > 256
            || spec.tlds.iter().any(|value| {
                value.is_empty()
                    || value.len() > 128
                    || !value
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || c == b'-')
            })
            || spec.schemes.iter().any(|value| {
                value != "https?"
                    && (value.is_empty()
                        || value.len() > 128
                        || !value.as_bytes()[0].is_ascii_alphabetic()
                        || !value
                            .bytes()
                            .all(|c| c.is_ascii_alphanumeric() || c == b'-'))
            })
        {
            return None;
        }
        let word = WORD;
        // Python's whitespace class also contains the ASCII record separators.
        let space = r"\x09-\x0d\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000";
        let ordinary = format!(r#"[^{space}()"]"#);
        let group = |inner: &str| format!(r"{ordinary}*?(?:\({inner}\){ordinary}*?)*");
        let mut inner = format!("{ordinary}*");
        for _ in 0..6 {
            inner = group(&inner);
        }
        let paired = format!(r"\({inner}\)");
        let nested = group(&inner);
        // Empty queries need separate trailing punctuation to preserve lookahead.
        let query = format!(r"\?(?:{ordinary}+?|{paired}{ordinary}*?)(?:{paired}{ordinary}*?)*");
        let tlds = spec.tlds.join("|");
        let schemes = spec
            .schemes
            .iter()
            .map(|value| {
                if value == "https?" {
                    value.to_owned()
                } else {
                    regex::escape(value)
                }
            })
            .collect::<Vec<_>>()
            .join("|");
        let files = if spec.enable_file_links {
            r"|(?:file://(/[^/ ]*)+/?)"
        } else {
            ""
        };
        let domain = format!(r"(?:(?:{schemes})://[{word}.:@-]+?|(?:[{word}-]+\.)+(?:{tlds}))");
        let terminal = format!(r#"[!:;?),.'">]*(?:\z|[{space}])"#);
        let expression = format!(
            r"(?P<domain>{domain})(?:/(?P<path>{nested}){terminal}|(?P<empty_query>\?)[!:;?,.'>]{terminal}|(?P<query>{query}){terminal}|{terminal})|(?P<other>[{word}.-]+@[{word}.-]+\.[{word}]+{files}|bitcoin:[13][a-km-zA-HJ-NP-Z1-9]{{25,34}}){terminal}",
        );
        Some(Self {
            expression: RegexBuilder::new(&expression)
                .size_limit(16 * 1024 * 1024)
                .build()
                .ok()?,
        })
    }

    /// Return a byte range, or delegate when the cumulative scan budget expires.
    pub fn find(&self, data: &str, remaining: &mut usize) -> Option<Option<Range<usize>>> {
        let mut position = 0;
        while position <= data.len() {
            *remaining = remaining.checked_sub(data.len() - position)?;
            let Some(captures) = self.expression.captures_at(data, position) else {
                return Some(None);
            };
            let range = if let Some(domain) = captures.name("domain") {
                let end = captures
                    .name("path")
                    .or_else(|| captures.name("empty_query"))
                    .or_else(|| captures.name("query"))
                    .map_or(domain.end(), |value| value.end());
                domain.start()..end
            } else {
                captures.name("other")?.range()
            };
            if data[..range.start]
                .chars()
                .next_back()
                .is_none_or(|c| !c.is_ascii() || is_python_whitespace(c) || "'\"(,:<".contains(c))
            {
                return Some(Some(range));
            }
            position = range.start + data[range.start..].chars().next()?.len_utf8();
        }
        Some(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> Spec {
        Spec {
            tlds: vec!["com".to_owned(), "org".to_owned()],
            schemes: vec!["https?".to_owned(), "obsidian".to_owned()],
            enable_file_links: false,
        }
    }

    #[test]
    fn preserves_candidate_order_boundaries_and_lazy_paths() {
        let matcher = Matcher::new(&spec()).unwrap();
        for (text, expected) in [
            ("example.com...", Some("example.com")),
            ("https://example.com/a.,?!", Some("https://example.com/a")),
            ("https://x/a(b(c))end.", Some("https://x/a(b(c))end")),
            ("https://x/a(\"b\") example.org", Some("example.org")),
            ("https://x?x", Some("https://x?x")),
            ("https://x?", Some("https://x")),
            ("https://x??", Some("https://x?")),
            ("https://x?.", Some("https://x?")),
            ("https://x?)", Some("https://x")),
            ("https://x:_:@:??\u{85}", Some("https://x:_:@")),
            ("https://x?()", Some("https://x?()")),
            ("[https://x]", None),
            ("éhttps://x", Some("https://x")),
            ("🙂https://x", Some("https://x")),
            ("user@example.com/path example.org", Some("example.org")),
            ("user@example.com", Some("user@example.com")),
            (
                "bitcoin:1BoatSLRHtKNngkdXEeobR76b53LETtpyT",
                Some("bitcoin:1BoatSLRHtKNngkdXEeobR76b53LETtpyT"),
            ),
        ] {
            let mut remaining = usize::MAX;
            assert_eq!(
                matcher
                    .find(text, &mut remaining)
                    .unwrap()
                    .map(|r| &text[r]),
                expected,
                "{text:?}"
            );
        }
    }

    #[test]
    fn validates_specs_caches_and_bounds_scanning() {
        let first = Matcher::cached(spec()).unwrap();
        assert!(Arc::ptr_eq(&first, &Matcher::cached(spec()).unwrap()));
        assert!(first.find("https://x", &mut 0).is_none());
        let mut invalid = spec();
        invalid.schemes.push(".*".to_owned());
        assert!(Matcher::new(&invalid).is_none());
        invalid = spec();
        invalid.tlds.push("com|.*".to_owned());
        assert!(Matcher::new(&invalid).is_none());
        let mut files = spec();
        files.enable_file_links = true;
        let matcher = Matcher::new(&files).unwrap();
        let data = "file:///tmp/a\nb.txt";
        assert_eq!(matcher.find(data, &mut 100).unwrap(), Some(0..data.len()));
    }
}

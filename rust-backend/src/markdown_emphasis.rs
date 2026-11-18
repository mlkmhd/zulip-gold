use crate::draft_text::is_python_whitespace;

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Emphasis<'a> {
    pub before: &'a str,
    pub marker: &'static str,
    pub text: &'a str,
    pub closing: Option<&'static str>,
    pub after: &'a str,
}

pub(crate) fn find_emphasis<'a>(data: &'a str, kind: &str) -> Option<Emphasis<'a>> {
    let (marker, character) = match kind {
        "strong" => ("**", b'*'),
        "em" => ("*", b'*'),
        "strong,em" => ("***", b'*'),
        "del" => ("~~", b'~'),
        _ => return None,
    };
    let bytes = data.as_bytes();
    for (start, &byte) in bytes.iter().enumerate() {
        if byte != character || !data[start..].starts_with(marker) {
            continue;
        }
        let text_start = start + marker.len();
        let rest = &data[text_start..];
        let text_length = if kind == "strong" {
            // The first body character can itself be part of a marker.
            let first = rest.chars().next()?;
            if first == '\n' {
                continue;
            }
            rest[first.len_utf8()..]
                .split('\n')
                .next()
                .unwrap()
                .find(marker)
                .map(|offset| first.len_utf8() + offset)
        } else {
            if kind == "del" && start > 0 && bytes[start - 1] == b'~' {
                continue;
            }
            rest.bytes().position(|byte| {
                byte == b'\n' || byte == character || (kind != "del" && byte == b'^')
            })
        };
        let Some(length) = text_length.filter(|length| *length > 0) else {
            continue;
        };
        let text = &rest[..length];
        let after_text = &rest[length..];
        if !after_text.starts_with(marker) {
            continue;
        }
        let after = &after_text[marker.len()..];
        if kind == "del" {
            if after.starts_with('~') {
                continue;
            }
        } else if kind != "strong"
            && (text.starts_with(is_python_whitespace) || text.ends_with(is_python_whitespace))
        {
            continue;
        }
        return Some(Emphasis {
            before: &data[..start],
            marker,
            text,
            closing: (kind == "del").then_some(marker),
            after,
        });
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_lazy_strong_and_greedy_emphasis() {
        assert_eq!(
            find_emphasis("before **a**b** after", "strong")
                .unwrap()
                .text,
            "a"
        );
        assert_eq!(find_emphasis("*****", "strong").unwrap().text, "*");
        assert_eq!(
            find_emphasis("****x****", "strong,em").unwrap(),
            Emphasis {
                before: "*",
                marker: "***",
                text: "x",
                closing: None,
                after: "*",
            }
        );
        assert_eq!(find_emphasis("*عنوان📝*", "em").unwrap().text, "عنوان📝");
        assert!(find_emphasis("*a^b*", "em").is_none());
        assert!(find_emphasis("*a\nb*", "em").is_none());
        assert!(find_emphasis("**a\nb**", "strong").is_none());
    }

    #[test]
    fn keeps_python_whitespace_and_strikethrough_boundaries() {
        for space in [' ', '\t', '\u{1c}', '\u{85}', '\u{a0}', '\u{3000}'] {
            assert!(find_emphasis(&format!("*{space}x*"), "em").is_none());
            assert!(find_emphasis(&format!("***x{space}***"), "strong,em").is_none());
            assert!(find_emphasis(&format!("**{space}**"), "strong").is_some());
            assert!(find_emphasis(&format!("~~{space}~~"), "del").is_some());
        }
        assert!(find_emphasis("~~~x~~", "del").is_none());
        assert!(find_emphasis("~~x~~~", "del").is_none());
        assert!(find_emphasis("~~x~y~~", "del").is_none());
        assert_eq!(
            find_emphasis("a~~x~~b", "del").unwrap(),
            Emphasis {
                before: "a",
                marker: "~~",
                text: "x",
                closing: Some("~~"),
                after: "b",
            }
        );
    }
}

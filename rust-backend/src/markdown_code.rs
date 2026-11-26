use std::collections::HashMap;

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct CodeMatch<'a> {
    pub start: usize,
    pub end: usize,
    pub backslashes: Option<&'a str>,
    pub delimiter: Option<&'a str>,
    pub code: Option<&'a str>,
}

impl<'a> CodeMatch<'a> {
    fn span(data: &'a str, begin: usize, delimiter_end: usize, close: usize, end: usize) -> Self {
        Self {
            start: data[..begin].chars().count(),
            end: data[..end].chars().count(),
            backslashes: None,
            delimiter: Some(&data[begin..delimiter_end]),
            code: Some(&data[delimiter_end..close]),
        }
    }

    fn backslashes(data: &'a str, begin: usize, end: usize) -> Self {
        Self {
            start: data[..begin].chars().count(),
            end: data[..end].chars().count(),
            backslashes: Some(&data[begin..end]),
            delimiter: None,
            code: None,
        }
    }

    pub fn rendered_text(&self) -> String {
        match self.code {
            Some(code) => escape_code(code),
            None => self
                .backslashes
                .expect("backslash match")
                .replace("\\\\", "\u{2}92\u{3}"),
        }
    }
}

pub(crate) fn escape_code(code: &str) -> String {
    code.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

#[cfg(any(feature = "python", test))]
pub(crate) fn escape_fenced_code(code: &[u8]) -> std::borrow::Cow<'_, [u8]> {
    fn entity(byte: u8) -> Option<&'static [u8]> {
        match byte {
            b'&' => Some(b"&amp;"),
            b'<' => Some(b"&lt;"),
            b'>' => Some(b"&gt;"),
            b'"' => Some(b"&quot;"),
            _ => None,
        }
    }

    let mut replacements = code
        .iter()
        .enumerate()
        .filter_map(|(index, byte)| entity(*byte).map(|value| (index, value)));
    let Some((first, value)) = replacements.next() else {
        return std::borrow::Cow::Borrowed(code);
    };

    let mut output = Vec::with_capacity(code.len().saturating_add(value.len() - 1));
    output.extend_from_slice(&code[..first]);
    output.extend_from_slice(value);
    let mut start = first + 1;
    for (index, value) in replacements {
        output.extend_from_slice(&code[start..index]);
        output.extend_from_slice(value);
        start = index + 1;
    }
    output.extend_from_slice(&code[start..]);
    std::borrow::Cow::Owned(output)
}

pub(crate) fn find_inline_code(data: &str, position: usize) -> Option<CodeMatch<'_>> {
    let start = data.char_indices().nth(position)?.0;
    let bytes = data.as_bytes();
    let mut runs = Vec::new();
    let mut closers: HashMap<usize, Vec<usize>> = HashMap::new();
    let mut index = start;
    let mut first_opener = None;
    while index < bytes.len() {
        let marker = bytes[index];
        if marker != b'`' && marker != b'\\' {
            index += 1;
            continue;
        }
        let begin = index;
        while index < bytes.len() && bytes[index] == marker {
            index += 1;
        }
        if let Some((open, open_end)) = first_opener {
            if marker == b'`' && index - begin == open_end - open && begin > open_end {
                return Some(CodeMatch::span(data, open, open_end, begin, index));
            }
        } else if marker == b'`' {
            let open = begin + usize::from(begin > 0 && bytes[begin - 1] == b'\\');
            if open < index {
                first_opener = Some((open, index));
            }
        } else if (begin == 0 || bytes[begin - 1] != b'\\')
            && (index - begin).is_multiple_of(2)
            && bytes.get(index) == Some(&b'`')
        {
            return Some(CodeMatch::backslashes(data, begin, index));
        }
        runs.push((begin, index, marker));
        if marker == b'`' && (begin == 0 || bytes[begin - 1] != b'`') {
            closers.entry(index - begin).or_default().push(begin);
        }
    }
    for (begin, end, marker) in runs {
        let preceded_by_backslash = begin > 0 && bytes[begin - 1] == b'\\';
        if marker == b'\\' {
            if !preceded_by_backslash
                && (end - begin).is_multiple_of(2)
                && bytes.get(end) == Some(&b'`')
            {
                return Some(CodeMatch::backslashes(data, begin, end));
            }
            continue;
        }
        let begin = begin + usize::from(preceded_by_backslash);
        // The opening run is greedy, but can backtrack to a shorter delimiter.
        for length in (1..=end - begin).rev() {
            let Some(candidates) = closers.get(&length) else {
                continue;
            };
            let next = candidates.partition_point(|&offset| offset <= begin + length);
            let Some(&close) = candidates.get(next) else {
                continue;
            };
            return Some(CodeMatch::span(
                data,
                begin,
                begin + length,
                close,
                close + length,
            ));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fenced_code_escapes_quotes_without_reescaping_entities() {
        assert_eq!(
            escape_fenced_code("<&>\"' &amp; فارسی 😀\n\r".as_bytes()).as_ref(),
            "&lt;&amp;&gt;&quot;' &amp;amp; فارسی 😀\n\r".as_bytes()
        );
        assert!(matches!(
            escape_fenced_code(b""),
            std::borrow::Cow::Borrowed(_)
        ));
        assert_eq!(
            escape_fenced_code(b"\xed\xa0\x80&\xed\xbf\xbf").as_ref(),
            b"\xed\xa0\x80&amp;\xed\xbf\xbf",
        );
        assert_eq!(escape_code("\"'"), "\"'");
    }

    #[test]
    fn preserves_code_whitespace_and_unicode_offsets() {
        assert_eq!(
            find_inline_code("📝 ` عنوان <&> \n ` after", 0),
            Some(CodeMatch {
                start: 2,
                end: 17,
                backslashes: None,
                delimiter: Some("`"),
                code: Some(" عنوان <&> \n "),
            })
        );
        assert_eq!(find_inline_code("`a` `b`", 3).unwrap().code, Some("b"));
        assert_eq!(
            find_inline_code("` <&> `", 0).unwrap().rendered_text(),
            " &lt;&amp;&gt; "
        );
        assert_eq!(find_inline_code("`a`", 4), None);
    }

    #[test]
    fn backtracks_openers_and_requires_complete_closing_runs() {
        assert_eq!(find_inline_code("```a``b`", 0).unwrap().code, Some("`a"));
        assert_eq!(find_inline_code("`a``b`", 0).unwrap().code, Some("a``b"));
        assert_eq!(find_inline_code("```", 0), None);
        assert_eq!(find_inline_code("`a`", 1), None);
        assert_eq!(find_inline_code("``a`", 1).unwrap().code, Some("a"));
    }

    #[test]
    fn distinguishes_escaped_openers_and_backslash_pairs() {
        let escaped = find_inline_code("\\\\`a`", 0).unwrap();
        assert_eq!(escaped.backslashes, Some("\\\\"));
        assert_eq!(escaped.end, 2);
        assert_eq!(escaped.rendered_text(), "\u{2}92\u{3}");
        assert_eq!(find_inline_code("\\\\`a`", 2), None);
        assert_eq!(find_inline_code("\\`a`", 0), None);
        assert_eq!(find_inline_code("\\``a`", 0).unwrap().code, Some("a"));
        assert_eq!(find_inline_code("\\\\\\`a`", 0), None);
    }
}

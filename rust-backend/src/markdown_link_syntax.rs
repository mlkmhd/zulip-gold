//! Parse inline link boundaries without interpreting or authorizing the destination.
//!
//! Ranges use UTF-8 byte offsets into the original input. Destinations and titles
//! still contain Markdown placeholders; callers must unescape, sanitize, and
//! rewrite them before constructing a link. This module never produces HTML.
//!
//! Adapted from Python-Markdown 3.10.3's LinkInlineProcessor under BSD-3-Clause;
//! see docs/THIRDPARTY for its copyright and license notice.

use std::ops::Range;

use crate::draft_text::is_python_whitespace;

#[derive(Debug, PartialEq, Eq)]
pub struct Link {
    pub text: Range<usize>,
    pub href: Range<usize>,
    pub title: Option<Range<usize>>,
    pub end: usize,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ParseError {
    /// The reference parser resolves an unclosed title to a negative end index.
    InvalidEnd,
}

fn skip_whitespace(data: &str, start: usize) -> usize {
    start + data[start..].len() - data[start..].trim_start_matches(is_python_whitespace).len()
}

fn angle_destination(
    data: &str,
    start: usize,
) -> Option<(Range<usize>, Option<Range<usize>>, usize)> {
    if !data[start..].starts_with('<') {
        return None;
    }
    let mut end = start + 1;
    for c in data[end..].chars() {
        if c == '<' {
            return None;
        }
        if c == '>' {
            let raw = &data[start + 1..end];
            let href_start = skip_whitespace(data, start + 1).min(end);
            let href_end = start + 1 + raw.trim_end_matches(is_python_whitespace).len();
            let href = href_start..href_end.max(href_start);
            let mut index = skip_whitespace(data, end + 1);
            let mut title = None;
            if let Some(quote @ ('\'' | '"')) = data[index..].chars().next() {
                let title_start = index + 1;
                let title_end = title_start + data[title_start..].find(quote)?;
                title = Some(title_start..title_end);
                index = skip_whitespace(data, title_end + 1);
            }
            return data[index..]
                .starts_with(')')
                .then_some((href, title, index + 1));
        }
        end += c.len_utf8();
    }
    None
}

struct Quote {
    character: char,
    start: usize,
    end: Option<usize>,
}

impl Quote {
    fn closed_before(&self, last: Option<char>) -> bool {
        self.end.is_some() && Some(self.character) == last
    }

    fn range(&self) -> Range<usize> {
        self.start..self.end.unwrap() - 1
    }
}

/// Parse a link beginning at `start`, including the opening square bracket.
///
/// `Ok(None)` means there is no complete link at that byte offset. Image openers
/// (`![`) are excluded, as in the reference link handler. Raw titles preserve
/// whitespace and quotes; an angle-enclosed destination is stripped as in the
/// reference parser before its later unescape step. An error requires fallback
/// to the reference handler, rather than treating the input as a missing link.
pub fn parse(data: &str, start: usize) -> Result<Option<Link>, ParseError> {
    if !data.get(start..).is_some_and(|s| s.starts_with('[')) || data[..start].ends_with('!') {
        return Ok(None);
    }
    parse_bracket(data, start)
}

/// Parse the shared image/audio syntax beginning at an exclamation mark.
///
/// The same raw destination and fallback rules as [`parse`] apply. Media type,
/// attachment access, and HTML construction remain the caller's responsibility.
pub fn parse_image(data: &str, start: usize) -> Result<Option<Link>, ParseError> {
    if !data.get(start..).is_some_and(|s| s.starts_with("![")) {
        return Ok(None);
    }
    parse_bracket(data, start + 1)
}

fn parse_bracket(data: &str, start: usize) -> Result<Option<Link>, ParseError> {
    let text_start = start + 1;
    let mut brackets = 1usize;
    let mut text_end = None;
    for (offset, c) in data[text_start..].char_indices() {
        match c {
            '[' => brackets += 1,
            ']' => brackets -= 1,
            _ => {}
        }
        if brackets == 0 {
            text_end = Some(text_start + offset);
            break;
        }
    }
    let Some(text_end) = text_end else {
        return Ok(None);
    };
    let text = text_start..text_end;
    let open = text_end + 1;
    if !data[open..].starts_with('(') {
        return Ok(None);
    }
    let href_start = skip_whitespace(data, open + 1);
    if let Some((href, title, end)) = angle_destination(data, href_start) {
        return Ok(Some(Link {
            text,
            href,
            title,
            end,
        }));
    }

    let mut brackets = 1usize;
    let mut backup = 1usize;
    let mut backup_end = None;
    let mut quote: Option<Quote> = None;
    let mut alternative: Option<Quote> = None;
    let mut last = None;
    for (offset, c) in data[href_start..].char_indices() {
        let index = href_start + offset;
        let closed = quote
            .as_ref()
            .filter(|q| q.closed_before(last))
            .or_else(|| alternative.as_ref().filter(|q| q.closed_before(last)));
        match c {
            '(' if quote.is_none() => brackets += 1,
            '(' if backup > 0 => backup -= 1,
            ')' if closed.is_some() => brackets = 0,
            ')' if quote.is_none() => brackets -= 1,
            ')' if backup > 0 => {
                backup -= 1;
                if backup == 0 {
                    backup_end = Some(index + 1);
                }
            }
            '\'' | '"' => {
                if let Some(primary) = &mut quote {
                    if c == primary.character {
                        primary.end = Some(index + 1);
                    } else if let Some(secondary) = &mut alternative {
                        secondary.end = Some(index + 1);
                    } else {
                        alternative = Some(Quote {
                            character: c,
                            start: index + 1,
                            end: None,
                        });
                    }
                } else {
                    backup = brackets;
                    brackets = 1;
                    quote = Some(Quote {
                        character: c,
                        start: index + 1,
                        end: None,
                    });
                }
            }
            _ => {}
        }
        if brackets == 0 {
            let title = quote
                .as_ref()
                .filter(|q| q.closed_before(last))
                .or_else(|| alternative.as_ref().filter(|q| q.closed_before(last)));
            let href_end = title.map_or(index, |q| q.start - 1);
            return Ok(Some(Link {
                text,
                href: href_start..href_end,
                title: title.map(Quote::range),
                end: index + 1,
            }));
        }
        if c != ' ' {
            last = Some(c);
        }
    }
    if backup == 0 && backup_end.is_none() {
        return Err(ParseError::InvalidEnd);
    }
    Ok(backup_end.map(|end| Link {
        text,
        href: href_start..end - 1,
        title: None,
        end,
    }))
}

//! Plain-text display data for poll and todo widget shorthands.

fn py_space(ch: char) -> bool {
    matches!(
        ch,
        '\u{9}'..='\u{d}' | '\u{1c}'..='\u{20}' | '\u{85}' | '\u{a0}' | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}'
            | '\u{3000}'
    )
}

fn py_lines(text: &str) -> impl Iterator<Item = &str> {
    let mut chars = text.char_indices().peekable();
    let mut from = 0;
    std::iter::from_fn(move || {
        while let Some((index, ch)) = chars.next() {
            if matches!(
                ch,
                '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'
                    ..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
            ) {
                let line = &text[from..index];
                from = index + ch.len_utf8();
                if ch == '\r' && chars.peek().is_some_and(|&(_, next)| next == '\n') {
                    chars.next();
                    from += 1;
                }
                return Some(line);
            }
        }
        if from == text.len() {
            None
        } else {
            let last = &text[from..];
            from = text.len();
            Some(last)
        }
    })
}

fn widget_parts(text: &str) -> (&str, impl Iterator<Item = &str>) {
    let mut lines = py_lines(text).peekable();
    let heading = if lines.peek().is_some_and(|line| !line.is_empty()) {
        lines.next().expect("peeked line").trim_matches(py_space)
    } else {
        ""
    };
    let rows = lines
        .map(|line| strip_list_marker(line.trim_matches(py_space)))
        .filter(|line| !line.is_empty());
    (heading, rows)
}

pub(crate) fn strip_list_marker(line: &str) -> &str {
    let line = line.trim_start_matches(py_space);
    if let Some(rest) = line.strip_prefix(['-', '*']) {
        rest.trim_start_matches(py_space)
    } else {
        line
    }
}

pub(crate) fn split_task_row(line: &str) -> (&str, &str) {
    let (task, description) = line.split_once(": ").unwrap_or((line, ""));
    (
        task.trim_matches(py_space),
        description.trim_matches(py_space),
    )
}

/// A title and the ordered poll options.
pub type PollParts = (String, Vec<String>);
/// A title and the ordered task/description pairs.
pub type TodoParts = (String, Vec<(String, String)>);

/// Extract display data from the already selected poll text.
pub fn poll_parts(text: &str) -> PollParts {
    let (title, options) = widget_parts(text);
    (title.to_owned(), options.map(str::to_owned).collect())
}

/// Extract display data from the already selected todo text.
pub fn todo_parts(text: &str) -> TodoParts {
    let (title, tasks) = widget_parts(text);
    let tasks = tasks
        .map(|line| {
            let (task, description) = split_task_row(line);
            (task.to_owned(), description.to_owned())
        })
        .collect();
    (title.to_owned(), tasks)
}

/// Prepared data for a recognized message shorthand.
#[derive(Debug, PartialEq, Eq)]
pub enum WidgetShorthand {
    NotWidget,
    Poll(PollParts),
    Todo(TodoParts),
}

#[derive(Clone, Copy)]
pub(crate) enum WidgetCommand {
    Poll,
    Todo,
}

pub(crate) fn split_widget_command(text: &str) -> (Option<WidgetCommand>, &str) {
    // The first token is empty on leading whitespace, and whitespace after
    // a recognized command is part of the text handed to splitlines().
    let (command, rest) = text.split_at(text.find(py_space).unwrap_or(text.len()));
    let command = match command {
        "/poll" => Some(WidgetCommand::Poll),
        "/todo" => Some(WidgetCommand::Todo),
        _ => None,
    };
    (command, rest)
}

/// Parse a leading poll or todo command and its text, without Python or Django.
pub fn parse_shorthand(text: &str) -> WidgetShorthand {
    match split_widget_command(text) {
        (Some(WidgetCommand::Poll), rest) => WidgetShorthand::Poll(poll_parts(rest)),
        (Some(WidgetCommand::Todo), rest) => WidgetShorthand::Todo(todo_parts(rest)),
        (None, _) => WidgetShorthand::NotWidget,
    }
}

#[cfg(test)]
mod tests {
    use super::{WidgetShorthand, parse_shorthand, poll_parts, todo_parts};

    #[test]
    fn recognizes_only_exact_leading_commands() {
        for source in [
            "",
            " /poll question",
            "/Poll",
            "/todoist",
            "/poll\u{200b} title",
            "say /todo",
        ] {
            assert_eq!(parse_shorthand(source), WidgetShorthand::NotWidget);
        }
        assert_eq!(
            parse_shorthand("/poll"),
            WidgetShorthand::Poll((String::new(), Vec::new()))
        );
        assert_eq!(
            parse_shorthand("/todo"),
            WidgetShorthand::Todo((String::new(), Vec::new()))
        );
    }

    #[test]
    fn poll_respects_unicode_whitespace_and_rows() {
        let text = "\u{1f}پرسش\r\n- گزینه\u{2028}** second\u{85}*";
        assert_eq!(
            parse_shorthand(&format!("/poll{text}")),
            WidgetShorthand::Poll((
                "پرسش".to_owned(),
                vec!["گزینه".to_owned(), "* second".to_owned()]
            )),
        );
        assert_eq!(
            poll_parts("\r\n- a\u{2028}* \u{200b}\u{85}"),
            (String::new(), vec!["a".to_owned(), "\u{200b}".to_owned()])
        );
    }

    #[test]
    fn todo_keeps_first_literal_description_separator() {
        let text = "\u{85}- task: first: last\u{1c}* : empty title\r\n X:\tY";
        let expected = (
            String::new(),
            vec![
                ("task".to_owned(), "first: last".to_owned()),
                (String::new(), "empty title".to_owned()),
                ("X:\tY".to_owned(), String::new()),
            ],
        );
        assert_eq!(
            parse_shorthand(&format!("/todo{text}")),
            WidgetShorthand::Todo(expected.clone())
        );
        assert_eq!(todo_parts(text), expected);
    }

    #[test]
    fn distinguishes_unicode_line_boundaries_from_stripping() {
        for line_boundary in [
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ] {
            let message = format!("/poll{line_boundary}choice");
            assert_eq!(
                parse_shorthand(&message),
                WidgetShorthand::Poll((String::new(), vec!["choice".to_owned()])),
            );
        }
        assert_eq!(
            parse_shorthand("/poll\u{1f}choice"),
            WidgetShorthand::Poll(("choice".to_owned(), Vec::new())),
        );
    }
}

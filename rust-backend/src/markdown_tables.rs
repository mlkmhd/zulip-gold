use std::collections::HashMap;

fn end_border(row: &str) -> Option<(usize, usize)> {
    let content = row.strip_suffix('\n').unwrap_or(row);
    let prefix = content.strip_suffix('|')?;
    let backslashes = prefix
        .bytes()
        .rev()
        .take_while(|&byte| byte == b'\\')
        .count();
    backslashes
        .is_multiple_of(2)
        .then_some((prefix.len() - backslashes, content.len()))
}

pub(crate) fn split_row(row: &str, border: bool) -> Vec<String> {
    let mut row = if border {
        row.strip_prefix('|').unwrap_or(row)
    } else {
        row
    }
    .to_owned();
    if border && let Some((begin, end)) = end_border(&row) {
        row.replace_range(begin..end, "");
    }
    let bytes = row.as_bytes();
    let mut ticks = Vec::new();
    let mut pipes = Vec::new();
    let mut closers: HashMap<usize, Vec<usize>> = HashMap::new();
    let mut index = 0;
    while index < bytes.len() {
        let begin = index;
        let escaped = bytes[index] == b'\\';
        if escaped && matches!(bytes.get(index + 1), Some(b'\\' | b'|')) {
            index += 2;
            continue;
        }
        if bytes[index] == b'`' || (escaped && bytes.get(index + 1) == Some(&b'`')) {
            index += usize::from(escaped);
            let tick_begin = index;
            while bytes.get(index) == Some(&b'`') {
                index += 1;
            }
            let width = index - tick_begin;
            closers.entry(width).or_default().push(ticks.len());
            ticks.push((width, begin, index - 1, usize::from(escaped)));
        } else {
            if bytes[index] == b'|' {
                pipes.push(index);
            }
            index += 1;
        }
    }
    let mut regions = Vec::new();
    let mut position = 0;
    while position < ticks.len() {
        let (width, begin, _, escaped) = ticks[position];
        let size = width - escaped;
        if size > 0
            && let Some(candidates) = closers.get(&size)
        {
            let next = candidates.partition_point(|&candidate| candidate <= position);
            if let Some(&close) = candidates.get(next) {
                regions.push((begin, ticks[close].2));
                position = close + 1;
                continue;
            }
        }
        position += 1;
    }
    let mut cells = Vec::new();
    let mut position = 0;
    let mut region = 0;
    for pipe in pipes {
        while region < regions.len() && regions[region].1 < pipe {
            region += 1;
        }
        if region < regions.len() && regions[region].0 <= pipe {
            continue;
        }
        cells.push(row[position..pipe].to_owned());
        position = pipe + 1;
    }
    cells.push(row[position..].to_owned());
    cells
}

pub(crate) type TableCheck = (bool, Option<u8>, Option<Vec<String>>);

pub(crate) fn check_table(block: &str) -> TableCheck {
    let rows: Vec<_> = block.split('\n').map(|row| row.trim_matches(' ')).collect();
    if rows.len() < 2 {
        return (false, None, None);
    }
    let border =
        u8::from(rows[0].starts_with('|')) | (u8::from(end_border(rows[0]).is_some()) << 1);
    let columns = split_row(rows[0], border != 0).len();
    let is_table = columns > 1
        || (border != 0
            && rows[1..]
                .iter()
                .all(|row| row.starts_with('|') || end_border(row).is_some()));
    if is_table {
        let separator = split_row(rows[1], border != 0);
        if separator.len() == columns
            && separator
                .iter()
                .all(|cell| cell.chars().all(|ch| matches!(ch, '|' | ':' | '-' | ' ')))
        {
            return (true, Some(border), Some(separator));
        }
    }
    (false, Some(border), None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_only_unescaped_pipes_outside_paired_code() {
        assert_eq!(
            split_row("| عنوان | `a|b` | c\\|d |", true),
            [" عنوان ", " `a|b` ", " c\\|d "]
        );
        assert_eq!(split_row("a|b\\\\|", true), ["a", "b"]);
        assert_eq!(split_row("a|b\\|", true), ["a", "b\\|"]);
        assert_eq!(split_row("a|b|\n", true), ["a", "b\n"]);
        assert_eq!(split_row("|", true), [""]);
    }

    #[test]
    fn keeps_table_specific_escaped_backtick_pairing() {
        assert_eq!(split_row("\\``a|b`|c", false), ["\\``a|b`", "c"]);
        assert_eq!(split_row("\\`a|b`|c", false), ["\\`a", "b`", "c"]);
        assert_eq!(split_row("``a|b`|c", false), ["``a", "b`", "c"]);
    }

    #[test]
    fn preserves_separator_and_border_updates() {
        assert_eq!(check_table("single"), (false, None, None));
        assert_eq!(
            check_table("| h |\n| :---: |"),
            (true, Some(3), Some(vec![" :---: ".to_owned()]))
        );
        assert_eq!(check_table("h|b\n--|x"), (false, Some(0), None));
        assert_eq!(check_table("| h |\n---\nplain"), (false, Some(3), None));
        assert_eq!(
            check_table("| h |\n|"),
            (true, Some(3), Some(vec![String::new()]))
        );
    }
}

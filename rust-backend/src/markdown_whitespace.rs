pub(crate) fn normalize_whitespace(lines: &[&str], tab_length: i32) -> Vec<String> {
    let source = lines.join("\n");
    let mut expanded = String::with_capacity(source.len() + 2);
    let mut column = 0;
    let mut previous_cr = false;
    for character in source.chars() {
        if matches!(character, '\x02' | '\x03') {
            continue;
        }
        match character {
            '\r' => {
                expanded.push('\n');
                column = 0;
                previous_cr = true;
            }
            '\n' => {
                if !previous_cr {
                    expanded.push('\n');
                }
                column = 0;
                previous_cr = false;
            }
            '\t' => {
                let spaces = if tab_length > 0 {
                    let tab_length = tab_length as usize;
                    tab_length - column % tab_length
                } else {
                    0
                };
                expanded.extend(std::iter::repeat_n(' ', spaces));
                column += spaces;
                previous_cr = false;
            }
            _ => {
                expanded.push(character);
                column += 1;
                previous_cr = false;
            }
        }
    }
    expanded.push_str("\n\n");
    expanded
        .split('\n')
        .enumerate()
        .map(|(index, line)| {
            if index > 0 && line.bytes().all(|byte| byte == b' ') {
                String::new()
            } else {
                line.to_owned()
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expands_tabs_by_unicode_character_columns() {
        assert_eq!(
            normalize_whitespace(&["\t📝\tX"], 4),
            ["    📝   X", "", ""]
        );
        assert_eq!(normalize_whitespace(&["a\tb"], 0), ["ab", "", ""]);
        assert_eq!(normalize_whitespace(&["a\tb"], -1), ["ab", "", ""]);
    }

    #[test]
    fn removes_placeholders_before_normalizing_line_endings() {
        assert_eq!(
            normalize_whitespace(&["a\r\x02\x03\nb\rc"], 4),
            ["a", "b", "c", "", ""]
        );
        assert_eq!(normalize_whitespace(&["a\r"], 4), ["a", "", "", ""]);
    }

    #[test]
    fn preserves_first_line_spaces_and_adds_two_newlines() {
        assert_eq!(
            normalize_whitespace(&["  ", "\t", " last  "], 4),
            ["  ", "", " last  ", "", ""]
        );
        assert_eq!(normalize_whitespace(&[], 4), ["", "", ""]);
    }
}

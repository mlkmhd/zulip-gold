//! Normalize the line structure of UTF-8 or Python surrogate-pass bytes.

pub(crate) fn normalize_whitespace(lines: &[&str], tab_length: i32) -> Vec<String> {
    let source = lines.join("\n");
    let expanded = normalize_joined_whitespace(source.as_bytes(), tab_length);
    normalized_lines(&expanded)
        .map(|line| {
            std::str::from_utf8(line)
                .expect("preserved UTF-8")
                .to_owned()
        })
        .collect()
}

pub(crate) fn normalize_joined_whitespace(source: &[u8], tab_length: i32) -> Vec<u8> {
    let mut expanded = Vec::with_capacity(source.len() + 2);
    let mut column = 0;
    let mut previous_cr = false;
    for &byte in source {
        if matches!(byte, b'\x02' | b'\x03') {
            continue;
        }
        match byte {
            b'\r' => {
                expanded.push(b'\n');
                column = 0;
                previous_cr = true;
            }
            b'\n' => {
                if !previous_cr {
                    expanded.push(b'\n');
                }
                column = 0;
                previous_cr = false;
            }
            b'\t' => {
                let spaces = if tab_length > 0 {
                    let tab_length = tab_length as usize;
                    tab_length - column % tab_length
                } else {
                    0
                };
                expanded.extend(std::iter::repeat_n(b' ', spaces));
                column += spaces;
                previous_cr = false;
            }
            _ => {
                expanded.push(byte);
                // Count code points, not continuation bytes. A Python
                // surrogate is a single column, just like any other code point.
                column += usize::from(byte & 0xc0 != 0x80);
                previous_cr = false;
            }
        }
    }
    expanded.extend_from_slice(b"\n\n");
    expanded
}

pub(crate) fn normalized_lines(expanded: &[u8]) -> impl Iterator<Item = &[u8]> {
    expanded
        .split(|&byte| byte == b'\n')
        .enumerate()
        .map(|(index, line)| {
            if index > 0 && line.iter().all(|&byte| byte == b' ') {
                &[][..]
            } else {
                line
            }
        })
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

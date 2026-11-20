//! ISO timestamps for the CPython 3.12 Markdown renderer.

use std::sync::LazyLock;

use regex::Regex;

#[derive(Debug, PartialEq, Eq)]
pub enum Rendered {
    Valid(String),
    Invalid,
}

static DATE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"\A([0-9]{4})(?:-([0-9]{2})-([0-9]{2})|([0-9]{2})([0-9]{2})",
        r"|-W([0-9]{2})(?:-([0-9]))?|W([0-9]{2})([0-9])?)\z",
    ))
    .unwrap()
});

static TIME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"\A([0-9]{2}(?:(?::[0-9]{2}){1,2}|[0-9]{2}(?:[0-9]{2})?)?(?:[.,][0-9]+)?)",
        r"(Z|[+-][0-9]{2}(?:(?::[0-9]{2}){1,2}|[0-9]{2}(?:[0-9]{2})?)?(?:[.,][0-9]+)?)?\z",
    ))
    .unwrap()
});

const SECOND: i64 = 1_000_000;
const DAY: i64 = 86_400 * SECOND;

fn date_end(value: &str) -> Option<usize> {
    let bytes = value.as_bytes();
    if bytes.get(4) == Some(&b'-') {
        if bytes.get(5) != Some(&b'W') {
            return Some(10);
        }
        if bytes.get(8) != Some(&b'-') {
            return Some(8);
        }
        // CPython prefers the hyphen after a week number as the datetime
        // separator when two following digits could instead be day and time.
        return Some(if bytes.get(10).is_some_and(u8::is_ascii_digit) {
            8
        } else {
            10
        });
    }
    if bytes.get(4) != Some(&b'W') {
        return Some(8);
    }
    value.get(..7)?;
    let end = 7 + bytes[7..].iter().take_while(|b| b.is_ascii_digit()).count();
    Some(if end < 9 {
        end
    } else if end % 2 == 0 {
        7
    } else {
        8
    })
}

fn month_days(year: i32, month: i32) -> i32 {
    match month {
        2 if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

fn week_date(mut year: i32, week: i32, day: i32) -> Option<(i32, i32, i32)> {
    if year == 0 || !(1..=53).contains(&week) || !(1..=7).contains(&day) {
        return None;
    }
    let previous_year = year - 1;
    let jan1_weekday =
        (365 * previous_year + previous_year / 4 - previous_year / 100 + previous_year / 400) % 7;
    if week == 53 && jan1_weekday != 3 && !(jan1_weekday == 2 && month_days(year, 2) == 29) {
        return None;
    }
    let mut ordinal = 4 - (jan1_weekday + 3) % 7 + (week - 1) * 7 + day - 1;
    let year_days = 365 + i32::from(month_days(year, 2) == 29);
    if ordinal < 1 {
        year -= 1;
        ordinal += 365 + i32::from(month_days(year, 2) == 29);
    } else if ordinal > year_days {
        year += 1;
        ordinal -= year_days;
    }
    if !(1..=9999).contains(&year) {
        return None;
    }
    let mut month = 1;
    while ordinal > month_days(year, month) {
        ordinal -= month_days(year, month);
        month += 1;
    }
    Some((year, month, ordinal))
}

fn clock(value: &str) -> (i64, i64, i64, i64) {
    let (whole, fraction) = value.split_once(['.', ',']).unwrap_or((value, ""));
    let parts: Vec<_> = if whole.contains(':') {
        whole.split(':').collect()
    } else {
        (0..whole.len())
            .step_by(2)
            .map(|i| &whole[i..i + 2])
            .collect()
    };
    let mut fields = parts.iter().map(|part| part.parse::<i64>().unwrap());
    let hour = fields.next().unwrap();
    let minute = fields.next().unwrap_or(0);
    let second = fields.next().unwrap_or(0);
    let mut microsecond = 0;
    for (index, digit) in fraction.bytes().take(6).enumerate() {
        microsecond += i64::from(digit - b'0') * 10_i64.pow(5 - index as u32);
    }
    (hour, minute, second, microsecond)
}

/// `None` delegates syntax outside the ISO date/time grammar.
/// Invalid values inside this grammar are rendered
/// as escaped source by the caller. Fractions are truncated to microseconds.
pub fn render(value: &str) -> Option<Rendered> {
    let end = date_end(value)?;
    let captures = DATE.captures(value.get(..end)?)?;
    let time = if end == value.len() {
        None
    } else {
        let suffix = value.get(end..)?;
        let separator = suffix.chars().next()?;
        Some(TIME.captures(&suffix[separator.len_utf8()..])?)
    };
    let mut year = captures[1].parse::<i32>().unwrap();
    let (mut month, mut day) = if let Some(week) = captures.get(6).or_else(|| captures.get(8)) {
        let week = week.as_str().parse::<i32>().unwrap();
        let day = captures
            .get(7)
            .or_else(|| captures.get(9))
            .map_or(1, |day| day.as_str().parse::<i32>().unwrap());
        let Some((week_year, month, day)) = week_date(year, week, day) else {
            return Some(Rendered::Invalid);
        };
        year = week_year;
        (month, day)
    } else {
        (
            captures
                .get(2)
                .or_else(|| captures.get(4))
                .unwrap()
                .as_str()
                .parse::<i32>()
                .unwrap(),
            captures
                .get(3)
                .or_else(|| captures.get(5))
                .unwrap()
                .as_str()
                .parse::<i32>()
                .unwrap(),
        )
    };
    if year == 0 || !(1..=12).contains(&month) || !(1..=month_days(year, month)).contains(&day) {
        return Some(Rendered::Invalid);
    }
    let (hour, minute, second, microsecond) = time
        .as_ref()
        .and_then(|time| time.get(1))
        .map_or((0, 0, 0, 0), |time| clock(time.as_str()));
    if hour > 23 || minute > 59 || second > 59 {
        return Some(Rendered::Invalid);
    }
    let mut offset = 0;
    if let Some(zone) = time
        .as_ref()
        .and_then(|time| time.get(2))
        .filter(|zone| zone.as_str() != "Z")
    {
        let (hours, minutes, seconds, micros) = clock(&zone.as_str()[1..]);
        let seconds = (hours * 60 + minutes) * 60 + seconds;
        // CPython's ISO parser treats a zero whole-second offset as UTC,
        // including an offset with a nonzero fractional component.
        if seconds != 0 {
            offset = seconds * SECOND + micros;
        }
        if offset >= DAY {
            return Some(Rendered::Invalid);
        }
        if zone.as_str().starts_with('-') {
            offset = -offset;
        }
    }
    let utc = ((hour * 60 + minute) * 60 + second) * SECOND + microsecond - offset;
    day += utc.div_euclid(DAY) as i32;
    if day == 0 {
        month -= 1;
        if month == 0 {
            year -= 1;
            month = 12;
        }
        day = month_days(year, month);
    } else if day > month_days(year, month) {
        day = 1;
        month += 1;
        if month == 13 {
            year += 1;
            month = 1;
        }
    }
    if !(1..=9999).contains(&year) {
        return Some(Rendered::Invalid);
    }
    let utc = utc.rem_euclid(DAY);
    let hour = utc / (3600 * SECOND);
    let minute = utc / (60 * SECOND) % 60;
    let second = utc / SECOND % 60;
    let microsecond = utc % SECOND;
    let fraction = if microsecond == 0 {
        String::new()
    } else {
        format!(".{microsecond:06}")
    };
    Some(Rendered::Valid(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{fraction}Z"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calendar_timestamps_and_utc_boundaries() {
        for (input, expected) in [
            ("0001-01-01", "0001-01-01T00:00:00Z"),
            ("0001-01-01T00+00:00:00.5", "0001-01-01T00:00:00Z"),
            ("2000-02-29T01:02:03", "2000-02-29T01:02:03Z"),
            ("2024-03-01 00:00:00+03:30", "2024-02-29T20:30:00Z"),
            ("2023-12-31T23:59:59-00:00:01", "2024-01-01T00:00:00Z"),
            (
                "2024-01-01\n12:34:56.123456789Z",
                "2024-01-01T12:34:56.123456Z",
            ),
            ("2024-01-01💡12:34:56,1", "2024-01-01T12:34:56.100000Z"),
            ("2024-01-01T12.5", "2024-01-01T12:00:00.500000Z"),
            ("2024-01-01T12:34.5", "2024-01-01T12:34:00.500000Z"),
            ("2024-01-01T12+00:60", "2024-01-01T11:00:00Z"),
            ("2024-01-01T12+00:00:99", "2024-01-01T11:58:21Z"),
            ("2024-01-01T12+00:00:00.5", "2024-01-01T12:00:00Z"),
            ("2024-01-01T00+00:00:01.5", "2023-12-31T23:59:58.500000Z"),
        ] {
            assert_eq!(
                render(input),
                Some(Rendered::Valid(expected.into())),
                "{input}"
            );
        }
    }

    #[test]
    fn rejects_calendar_clock_and_utc_overflow() {
        for input in [
            "0000-01-01",
            "1900-02-29",
            "2024-00-01",
            "2024-13-01",
            "2024-01-00",
            "2024-04-31",
            "2024-01-01T24",
            "2024-01-01T23:60",
            "2024-01-01T23:59:60",
            "2024-01-01T00+24",
            "2024-01-01T00-23:60",
            "2024-01-01T00+99:99:99",
            "0001-01-01T00+00:00:01",
            "9999-12-31T23:59:59-00:00:01",
        ] {
            assert_eq!(render(input), Some(Rendered::Invalid), "{input}");
        }
    }

    #[test]
    fn delegates_other_iso_grammars_and_unrecognized_input() {
        for input in [
            "",
            "2024-01-01T",
            "2024-01-01TT12",
            "2024-01-01T12:",
            "۲۰۲۴-01-01",
            "2024-01-01T12Zx",
            "2024-01-01T12.1a",
            "2024-01-01T12+01:",
            "2024-01-01T12+01:02:03.",
        ] {
            assert_eq!(render(input), None, "{input}");
        }
    }

    #[test]
    fn compact_dates_times_and_offsets() {
        for (input, expected) in [
            ("20240101", "2024-01-01T00:00:00Z"),
            ("20240101T123456Z", "2024-01-01T12:34:56Z"),
            ("20240101T1234.5+0330", "2024-01-01T09:04:00.500000Z"),
            ("2024-01-01T123456+000001.5", "2024-01-01T12:34:54.500000Z"),
            ("20240101💡12:34:56-000099", "2024-01-01T12:36:35Z"),
            ("202401010123456+000000.5", "2024-01-01T12:34:56Z"),
        ] {
            assert_eq!(
                render(input),
                Some(Rendered::Valid(expected.into())),
                "{input}"
            );
        }
        for input in ["20230229", "20240101T240000", "20240101T12-2360"] {
            assert_eq!(render(input), Some(Rendered::Invalid), "{input}");
        }
        for input in [
            "20240101T123",
            "20240101T12:3456",
            "20240101T1234:56",
            "202401-01",
            "2024-0101",
        ] {
            assert_eq!(render(input), None, "{input}");
        }
    }

    #[test]
    fn week_dates_and_separator_ambiguity() {
        for (input, expected) in [
            ("0001-W01-1", "0001-01-01T00:00:00Z"),
            ("9999-W52-5", "9999-12-31T00:00:00Z"),
            ("2020-W53-7", "2021-01-03T00:00:00Z"),
            ("2015-W01", "2014-12-29T00:00:00Z"),
            ("2019-W01-1", "2018-12-31T00:00:00Z"),
            ("2024W01", "2024-01-01T00:00:00Z"),
            ("2024W011", "2024-01-01T00:00:00Z"),
            ("2020W537T235959-000001", "2021-01-04T00:00:00Z"),
            ("2024-W01-1123", "2024-01-01T11:23:00Z"),
            ("2024W01123", "2024-01-01T23:00:00Z"),
            ("2024W0112345", "2024-01-01T23:45:00Z"),
            ("2024W0112345.5", "2024-01-01T23:45:00.500000Z"),
            ("2024W0112345+01", "2024-01-01T22:45:00Z"),
            ("2024-W01-1💡00+000001.5", "2023-12-31T23:59:58.500000Z"),
            ("2024-W01\n12Z", "2024-01-01T12:00:00Z"),
        ] {
            assert_eq!(
                render(input),
                Some(Rendered::Valid(expected.into())),
                "{input}"
            );
        }
        for input in [
            "0000-W01-1",
            "2024-W00-1",
            "2024-W54-1",
            "2021-W53-1",
            "2020-W53-0",
            "2020-W53-8",
            "9999-W52-6",
            "0001-W01-1T00+000001",
            "9999-W52-5T235959-000001",
            "2024W011234",
            "2024W01123456",
        ] {
            assert_eq!(render(input), Some(Rendered::Invalid), "{input}");
        }
        for input in [
            "2024-W01-",
            "2024-W01-112",
            "2024-W01-112:34",
            "2024W01-1",
            "2024-W011",
            "2024-W01T",
            "2024-W01-💡12",
            "2024W",
        ] {
            assert_eq!(render(input), None, "{input}");
        }
    }
}

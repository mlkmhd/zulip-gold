//! Calendar timestamps for the CPython 3.12 Markdown renderer.

use std::sync::LazyLock;

use regex::Regex;

#[derive(Debug, PartialEq, Eq)]
pub enum Rendered {
    Valid(String),
    Invalid,
}

static CALENDAR: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?s)\A([0-9]{4})(?:-([0-9]{2})-([0-9]{2})|([0-9]{2})([0-9]{2}))",
        r"(?:.([0-9]{2}(?:(?::[0-9]{2}){1,2}|[0-9]{2}(?:[0-9]{2})?)?(?:[.,][0-9]+)?)",
        r"(Z|[+-][0-9]{2}(?:(?::[0-9]{2}){1,2}|[0-9]{2}(?:[0-9]{2})?)?(?:[.,][0-9]+)?)?)?\z",
    ))
    .unwrap()
});

const SECOND: i64 = 1_000_000;
const DAY: i64 = 86_400 * SECOND;

fn month_days(year: i32, month: i32) -> i32 {
    match month {
        2 if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
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

/// `None` delegates syntax outside the calendar date/time grammar, including
/// ISO week dates. Invalid values inside this grammar are rendered
/// as escaped source by the caller. Fractions are truncated to microseconds.
pub fn render(value: &str) -> Option<Rendered> {
    let captures = CALENDAR.captures(value)?;
    let mut year = captures[1].parse::<i32>().unwrap();
    let mut month = captures
        .get(2)
        .or_else(|| captures.get(4))
        .unwrap()
        .as_str()
        .parse::<i32>()
        .unwrap();
    let mut day = captures
        .get(3)
        .or_else(|| captures.get(5))
        .unwrap()
        .as_str()
        .parse::<i32>()
        .unwrap();
    if year == 0 || !(1..=12).contains(&month) || !(1..=month_days(year, month)).contains(&day) {
        return Some(Rendered::Invalid);
    }
    let (hour, minute, second, microsecond) = captures
        .get(6)
        .map_or((0, 0, 0, 0), |time| clock(time.as_str()));
    if hour > 23 || minute > 59 || second > 59 {
        return Some(Rendered::Invalid);
    }
    let mut offset = 0;
    if let Some(zone) = captures.get(7).filter(|zone| zone.as_str() != "Z") {
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
            "2024-W01-1",
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
}

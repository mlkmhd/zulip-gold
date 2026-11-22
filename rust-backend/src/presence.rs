//! Date and compatibility rules for presence presentation.

const MICROS_PER_SECOND: i64 = 1_000_000;
const MAX_EXACT_TRUNCATION: i64 = (1_i64 << 33) * MICROS_PER_SECOND;
const MAX_LEGACY_OFFSET_SECONDS: i64 = 1_000_000;
const MONTH_STARTS: [i64; 12] = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];

#[derive(Clone, Copy)]
pub(crate) struct UtcInstant(i64);

impl UtcInstant {
    pub(crate) fn from_datetime_parts(
        year: i64,
        month: usize,
        day: i64,
        hour: i64,
        minute: i64,
        second: i64,
        microsecond: i64,
    ) -> Option<Self> {
        // These bounds also keep all of the integer arithmetic in i64.
        if !(1..=9999).contains(&year)
            || !(1..=12).contains(&month)
            || !(1..=31).contains(&day)
            || !(0..24).contains(&hour)
            || !(0..60).contains(&minute)
            || !(0..60).contains(&second)
            || !(0..MICROS_PER_SECOND).contains(&microsecond)
        {
            return None;
        }
        let previous_year = year - 1;
        let before_year =
            365 * previous_year + previous_year / 4 - previous_year / 100 + previous_year / 400;
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        let days_in_month = if month == 12 {
            31
        } else {
            MONTH_STARTS[month] - MONTH_STARTS[month - 1] + i64::from(leap && month == 2)
        };
        if day > days_in_month {
            return None;
        }
        let day_of_year = MONTH_STARTS[month - 1] + i64::from(leap && month > 2) + day - 1;
        let days = before_year + day_of_year - 719_162;
        let seconds = days * 86_400 + hour * 3600 + minute * 60 + second;
        let micros = seconds * MICROS_PER_SECOND + microsecond;
        // Inside this range CPython's intermediate float cannot round a
        // non-integral microsecond across a whole-second boundary.
        (micros.abs() < MAX_EXACT_TRUNCATION).then_some(Self(micros))
    }

    pub(crate) fn timestamp(self) -> i64 {
        self.0 / MICROS_PER_SECOND
    }
}

pub(crate) struct LegacyPresence {
    pub(crate) is_active: bool,
    pub(crate) timestamp: i64,
}

pub(crate) fn legacy_presence(
    active: UtcInstant,
    connected: UtcInstant,
    offset_seconds: i64,
) -> Option<LegacyPresence> {
    if !(-MAX_LEGACY_OFFSET_SECONDS..=MAX_LEGACY_OFFSET_SECONDS).contains(&offset_seconds) {
        return None;
    }
    let is_active = active.0 + offset_seconds * MICROS_PER_SECOND >= connected.0;
    Some(LegacyPresence {
        is_active,
        timestamp: if is_active { active } else { connected }.timestamp(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_seconds_match_calendar_and_truncate_negative_fractions() {
        assert_eq!(
            UtcInstant::from_datetime_parts(1969, 12, 31, 23, 59, 59, 500_000)
                .unwrap()
                .timestamp(),
            0
        );
        assert_eq!(
            UtcInstant::from_datetime_parts(2000, 2, 29, 12, 34, 56, 500_000)
                .unwrap()
                .timestamp(),
            951_827_696
        );
        assert!(UtcInstant::from_datetime_parts(9999, 12, 31, 23, 59, 59, 999_999).is_none());
    }

    #[test]
    fn activity_cutoff_uses_microseconds_not_display_seconds() {
        let active = UtcInstant(1_000_000);
        let equal = legacy_presence(active, UtcInstant(71_000_000), 70).unwrap();
        assert!(equal.is_active);
        assert_eq!(equal.timestamp, 1);
        let past = legacy_presence(active, UtcInstant(71_000_001), 70).unwrap();
        assert!(!past.is_active);
        assert_eq!(past.timestamp, 71);
        assert!(legacy_presence(active, UtcInstant(71_000_000), i64::MAX).is_none());
    }
}

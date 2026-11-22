fn round_to_two_digits(number: i128) -> i128 {
    // The legacy rule counts a minus sign among the digits and uses Python's
    // round-half-to-even. Keep both details for the signed input contract.
    let digits = number.to_string().len();
    if digits <= 2 {
        return number;
    }
    let factor = 10_i128.pow((digits - 2) as u32);
    let quotient = number.div_euclid(factor);
    let remainder = number.rem_euclid(factor);
    let round_up = remainder > factor / 2 || (remainder == factor / 2 && quotient % 2 != 0);
    (quotient + i128::from(round_up)) * factor
}

pub(crate) fn average_weekly_messages(age_days: i64, traffic: impl Into<i128>) -> Option<i128> {
    let traffic = traffic.into();
    let average = if age_days >= 28 {
        traffic.div_euclid(4)
    } else if age_days >= 7 {
        (traffic * 7).div_euclid(i128::from(age_days))
    } else {
        return None;
    };
    Some(if average == 0 && traffic > 0 {
        1
    } else {
        round_to_two_digits(average)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distinguishes_age_boundaries_and_nonzero_floor() {
        assert_eq!(average_weekly_messages(6, 400), None);
        assert_eq!(average_weekly_messages(7, 400), Some(400));
        assert_eq!(average_weekly_messages(27, 100), Some(25));
        assert_eq!(average_weekly_messages(28, 100), Some(25));
        assert_eq!(average_weekly_messages(28, 1), Some(1));
        assert_eq!(average_weekly_messages(28, 0), Some(0));
        assert_eq!(average_weekly_messages(28, -1), Some(-1));
    }

    #[test]
    fn preserves_ties_negative_sign_and_large_integer_precision() {
        assert_eq!(round_to_two_digits(125), 120);
        assert_eq!(round_to_two_digits(135), 140);
        assert_eq!(round_to_two_digits(-25), -20);
        assert_eq!(round_to_two_digits(-35), -40);
        assert_eq!(
            average_weekly_messages(7, i64::MAX),
            Some(9_200_000_000_000_000_000)
        );
    }
}

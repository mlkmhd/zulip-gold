use jiter::JsonValue;

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ScheduledArgument {
    Integer(i64),
    Recipients(Vec<i64>),
    Boolean(bool),
}

#[derive(Clone, Copy)]
pub(crate) enum ArgumentKind {
    Integer,
    Recipients,
    Boolean,
}

pub(crate) fn parse(input: &str, kind: ArgumentKind) -> Option<ScheduledArgument> {
    // Leave malformed JSON, schema errors, and arbitrary-size integers to Pydantic.
    match (kind, JsonValue::parse(input.as_bytes(), true).ok()?) {
        (ArgumentKind::Integer | ArgumentKind::Recipients, JsonValue::Int(value)) => {
            Some(ScheduledArgument::Integer(value))
        }
        (ArgumentKind::Recipients, JsonValue::Array(values)) => values
            .iter()
            .map(|value| match value {
                JsonValue::Int(id) => Some(*id),
                _ => None,
            })
            .collect::<Option<Vec<_>>>()
            .map(ScheduledArgument::Recipients),
        (ArgumentKind::Boolean, JsonValue::Bool(value)) => Some(ScheduledArgument::Boolean(value)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_strict_json_without_reordering_recipients() {
        assert_eq!(
            parse(" [3,-2,3,9223372036854775807] ", ArgumentKind::Recipients),
            Some(ScheduledArgument::Recipients(vec![3, -2, 3, i64::MAX]))
        );
        assert_eq!(
            parse("[]", ArgumentKind::Recipients),
            Some(ScheduledArgument::Recipients(Vec::new()))
        );
        assert_eq!(
            parse("0", ArgumentKind::Recipients),
            Some(ScheduledArgument::Integer(0))
        );
        assert_eq!(
            parse("-9223372036854775808", ArgumentKind::Integer),
            Some(ScheduledArgument::Integer(i64::MIN))
        );
        assert_eq!(
            parse("false", ArgumentKind::Boolean),
            Some(ScheduledArgument::Boolean(false))
        );
    }

    #[test]
    fn delegates_bad_json_wrong_types_and_arbitrary_size_integers() {
        for input in [
            "null",
            "\"42\"",
            "true",
            "42.0",
            "42e0",
            "9223372036854775808",
            "42,",
        ] {
            assert_eq!(parse(input, ArgumentKind::Integer), None, "{input}");
        }
        for input in ["[false]", "[1.0]", "[1,9223372036854775808]", "[1,]"] {
            assert_eq!(parse(input, ArgumentKind::Recipients), None, "{input}");
        }
        for input in ["0", "\"true\"", "null", "true false", "True"] {
            assert_eq!(parse(input, ArgumentKind::Boolean), None, "{input}");
        }
    }
}

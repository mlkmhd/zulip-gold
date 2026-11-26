from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import TypeAdapter, ValidationError

from zerver.lib import scheduled_messages


class PreparedScheduledInputRustTest(SimpleTestCase):
    def test_builtin_annotations_keep_original_values_and_validation_errors(self) -> None:
        examples = (
            (
                scheduled_messages.ScheduledJobInteger,
                (
                    "-0",
                    " 42\t",
                    "-9223372036854775808",
                    "9223372036854775808",
                    "1.0",
                    "1e0",
                    '"42"',
                    "true",
                    "null",
                    "12,",
                    b"23",
                    12,
                ),
            ),
            (
                scheduled_messages.ScheduledJobRecipients,
                (
                    "[3,-2,3]",
                    "[]",
                    "11",
                    "[1,9223372036854775808]",
                    '[true,2.0,"3"]',
                    "[1,]",
                    "[[]]",
                    "null",
                    "\ufeff[]",
                    [1, 2],
                ),
            ),
            (
                scheduled_messages.ScheduledJobBoolean,
                ("true", " false ", '"true"', "0", "null", "true false", "True", b"true", True),
            ),
        )
        backend = scheduled_messages._rust_prepared_scheduled_input
        witness = (
            patch.object(
                backend, "parse_scheduled_job_argument", wraps=backend.parse_scheduled_job_argument
            )
            if backend is not None
            else nullcontext()
        )

        def outcome(adapter: TypeAdapter[object], value: object) -> tuple[str, object]:
            try:
                return "value", adapter.validate_python(value)
            except ValidationError as error:
                return "error", error.errors(include_url=False, include_context=False)

        expected_calls = 0
        with patch.object(scheduled_messages, "_rust_scheduled_job_input", None), witness as native:
            for annotation, values in examples:
                adapter = TypeAdapter[object](annotation)
                for value in values:
                    expected_calls += int(type(value) is str)
                    with self.subTest(annotation=annotation, value=repr(value)):
                        with patch.object(
                            scheduled_messages, "_rust_prepared_scheduled_input", None
                        ):
                            expected = outcome(adapter, value)
                        actual = outcome(adapter, value)
                        self.assertEqual(actual, expected)
                        if actual[0] == "value":
                            self.assertIs(type(actual[1]), type(expected[1]))
            if native is not None:
                self.assertEqual(native.call_count, expected_calls)

from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import TypeAdapter, ValidationError

from zerver.lib import drafts


class DjangoDraftInputRustTest(SimpleTestCase):
    def test_default_draft_inputs_keep_omitted_timestamp_and_exact_values(self) -> None:
        backend = drafts._rust_django_draft_input
        if backend is None:
            self.skipTest("No prepared Rust Django draft input ABI")
        draft = r'{"type":"private","to":[3,1180591620717411303424,3],"topic":"فارسی","content":"\u2003keep\u2003"}'
        with_null = draft[:-1] + ', "timestamp": null}'
        with_float = draft[:-1] + ', "timestamp": 2.0}'
        batch = f"[{draft},{with_null},{with_float}]"
        single = TypeAdapter(drafts.DraftInput)
        multiple = TypeAdapter(drafts.DraftBatchInput)
        with patch.object(drafts, "_rust_django_draft_input", None):
            expected_single = single.validate_python(draft)
            expected_batch = multiple.validate_python(batch)
        with (
            patch.object(
                backend, "parse_prepared_django_draft", wraps=backend.parse_prepared_django_draft
            ) as parse,
            patch.object(
                backend,
                "parse_prepared_django_draft_batch",
                wraps=backend.parse_prepared_django_draft_batch,
            ) as parse_batch,
        ):
            actual_single = single.validate_python(draft)
            actual_batch = multiple.validate_python(batch)
        parse.assert_called_once_with(draft)
        parse_batch.assert_called_once_with(batch)
        self.assertEqual(actual_single.model_fields_set, expected_single.model_fields_set)
        self.assertEqual(
            [item.model_dump(exclude_unset=True) for item in actual_batch],
            [item.model_dump(exclude_unset=True) for item in expected_batch],
        )
        self.assertEqual(
            [type(item.timestamp) for item in actual_batch], [type(None), type(None), float]
        )

    def test_rejected_shape_uses_reference_pydantic_errors(self) -> None:
        adapter = TypeAdapter(drafts.DraftBatchInput)
        invalid = '[{"type":"wrong","to":[false],"topic":"","content":" "}]'
        with (
            patch.object(drafts, "_rust_django_draft_input", None),
            self.assertRaises(ValidationError) as reference,
        ):
            adapter.validate_python(invalid)
        with self.assertRaises(ValidationError) as actual:
            adapter.validate_python(invalid)
        self.assertEqual(actual.exception.errors(), reference.exception.errors())

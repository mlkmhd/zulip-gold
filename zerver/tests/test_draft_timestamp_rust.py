import math
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import drafts
from zerver.lib.exceptions import JsonableError


class DraftTimestampContractTest(SimpleTestCase):
    def test_timestamp_rounding_and_errors_remain_exact(self) -> None:
        for value in (
            -0.0000005,
            0.0,
            0.0000015,
            1.9999995,
            math.nextafter(2.0**33, 0.0),
            math.nextafter(253402300800.0, 0.0),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    drafts.normalize_draft_timestamp(value),
                    datetime.fromtimestamp(round(value, 6), tz=timezone.utc),
                )
        with self.assertRaisesRegex(JsonableError, "Timestamp must not be negative"):
            drafts.normalize_draft_timestamp(math.nextafter(-0.0000005, -math.inf))
        with self.assertRaises(ValueError):
            drafts.normalize_draft_timestamp(math.nan)

    def test_current_wheel_and_custom_converter_are_kept_separate(self) -> None:
        backend = drafts._rust_default_draft_timestamp
        if getattr(backend, "DRAFT_TIMESTAMP_API", None) != 1:
            self.skipTest("Matching Rust draft timestamp contract is not available")
        marker = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with (
            patch.object(drafts, "_rust_draft_timestamp", None),
            patch.object(
                backend, "normalize_draft_timestamp", wraps=backend.normalize_draft_timestamp
            ) as native,
        ):
            self.assertEqual(
                drafts.normalize_draft_timestamp(7.25),
                datetime.fromtimestamp(7.25, tz=timezone.utc),
            )
            with patch.object(drafts, "timestamp_to_datetime", return_value=marker) as custom:
                self.assertIs(drafts.normalize_draft_timestamp(7.25), marker)
            custom.assert_called_once_with(7.25)
        native.assert_called_once_with(7.25)

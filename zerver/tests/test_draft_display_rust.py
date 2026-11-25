import os
from datetime import datetime, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import call, patch

from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Draft
from zerver.models import drafts as display


class DraftDisplayRustTest(TestCase):
    def test_prepared_values_are_preserved_without_conversion(self) -> None:
        try:
            render = import_module("_zulip_backend").render_prepared_draft
        except (ImportError, AttributeError):
            self.skipTest("Wheel lacks prepared draft display")

        keys = ["id", "type", "to", "topic", "content", "timestamp"]
        content = "".join(chr(code) for code in range(0xD800, 0xE000))
        row = (2**90, "private", [4, 2, 4], "پیش‌نویس 😀", content, -1)
        result = render(row)
        self.assertEqual(list(result), keys)
        for rendered, original in zip(result.values(), row, strict=True):
            self.assertIs(rendered, original)
        self.assertIsNot(result, render(row))
        self.assertIsNone(render(list(row)))
        self.assertIsNone(render(row[:-1]))
        self.assertIsNone(render((*row, None)))
        self.assertIsNone(render((True, *row[1:])))

    def test_declining_extension_preserves_prepared_values_and_order(self) -> None:
        draft = Draft(
            id=2**90,
            user_profile_id=7,
            topic="نمونه\ud800",
            content="چند خط\n😀",
            last_edit_time=datetime(1969, 12, 31, 23, 59, 58, 500000, tzinfo=timezone.utc),
        )
        expected = draft.to_dict()
        with patch.object(display, "_render_prepared_draft", return_value=None) as render:
            result = draft.to_dict(rust_display_for_get=True)
            render.assert_called_once_with((draft.id, "", [], draft.topic, draft.content, -1))
        self.assertEqual(result, expected)
        self.assertEqual(list(result), list(expected))


class DraftNativeGetIsolationTest(ZulipTestCase):
    def test_no_flag_get_uses_native_for_only_the_current_users_sorted_drafts(self) -> None:
        if not supports_default_rust_backend() or any(
            os.environ.get(flag)
            for flag in ("ZULIP_RUST_DRAFT_GET_DISPLAY", "ZULIP_RUST_DRAFT_SERIALIZATION")
        ):
            self.skipTest("No-flag startup is tested on Ubuntu 24.04 x86-64")
        if getattr(display._rust_draft_get_display, "DRAFT_GET_DISPLAY_API", None) != 1:
            self.skipTest("Wheel predates the default draft GET display contract")

        reader = self.example_user("hamlet")
        other = self.example_user("cordelia")
        self.login_user(reader)
        first_time = datetime(1969, 12, 31, 23, 59, 58, 500000, tzinfo=timezone.utc)
        last_time = datetime(2020, 11, 25, 12, tzinfo=timezone.utc)
        last = Draft.objects.create(
            user_profile=reader, topic="", content="Second", last_edit_time=last_time
        )
        Draft.objects.create(user_profile=other, content="Not visible", last_edit_time=first_time)
        first = Draft.objects.create(
            user_profile=reader, topic="نمونه", content="First 😀", last_edit_time=first_time
        )
        native = display._render_prepared_draft
        assert native is not None
        with patch.object(display, "_render_prepared_draft", wraps=native) as render:
            expected = [first.to_dict(), last.to_dict()]
            render.assert_not_called()
            api = self.api_get(reader, "/api/v1/drafts")
            web = self.client_get("/json/drafts")
        for response in (api, web):
            parsed = self.assert_json_success(response)
            self.assertEqual(parsed["count"], 2)
            self.assertEqual(parsed["drafts"], expected)
        expected_calls = [
            call(
                (row["id"], row["type"], row["to"], row["topic"], row["content"], row["timestamp"])
            )
            for row in expected
        ]
        self.assertEqual(render.call_args_list, expected_calls * 2)

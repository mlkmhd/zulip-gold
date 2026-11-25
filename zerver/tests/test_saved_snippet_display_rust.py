import json
import os
from datetime import datetime, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from django.test import RequestFactory
from typing_extensions import override

from zerver.actions import saved_snippets as actions
from zerver.models import SavedSnippet, UserProfile
from zerver.views.saved_snippets import get_saved_snippets


class SavedSnippetDisplayRustTest(TestCase):
    def test_prepared_projection(self) -> None:
        try:
            mod = import_module("_zulip_backend")
            render_all = mod.render_saved_snippet_display_rows
        except (ImportError, AttributeError):
            self.skipTest("Rust saved snippet projection is not available")

        title = "synthetic"
        content = "متن\0😀\ud800"
        huge_id = 2**90
        parts = (huge_id, title, content, -1)
        row = render_all([parts])[0]
        keys = ["id", "title", "content", "date_created"]
        self.assertEqual(list(row), keys)
        self.assertEqual(list(row.values()), list(parts))
        for value, original in zip(row.values(), parts, strict=True):
            self.assertIs(value, original)
        batch = render_all([parts, parts])
        self.assertEqual(batch, [row, row])
        self.assertIsNot(batch[0], batch[1])
        self.assertEqual(render_all([]), [])

        class Derived(str):
            __slots__ = ()

        for invalid in (
            list(parts),
            (True, title, content, -1),
            (1, Derived(title), content, -1),
            (1, title, b"plain", -1),
            (1, title, content, 0.0),
            parts[:3],
            (*parts, 5),
        ):
            self.assertIsNone(render_all([invalid]))
            self.assertIsNone(render_all([parts, invalid]))
        self.assertIsNone(render_all((parts,)))

    @override
    def setUp(self) -> None:
        self.request = RequestFactory().get("/api/v1/saved_snippets")
        self.user = UserProfile()
        self.rows = [
            SavedSnippet(
                id=row_id,
                title=title,
                content="چند خط\nliteral 😀",
                date_created=datetime(1969, 12, 31, 23, 59, 58, 500000, tzinfo=timezone.utc),
            )
            for row_id, title in [(59, "مطلب"), (12, "")]
        ]
        self.expected = [row.to_api_dict() for row in self.rows]

    def test_real_get_display_and_untouched_non_get_callers(self) -> None:
        native = actions._render_saved_snippet_display_rows
        enabled = os.environ.get("ZULIP_RUST_SAVED_SNIPPET_DISPLAY") == "1"
        self.assertEqual(callable(native), enabled)
        with (
            patch.object(SavedSnippet.objects, "filter", return_value=self.rows) as query,
            patch.object(actions, "_render_saved_snippet_display_rows", wraps=native)
            if enabled
            else patch.object(actions, "_render_saved_snippet_display_rows", None) as dispatch,
        ):
            self.assertEqual(actions.do_get_saved_snippets(self.user), self.expected)
            response = get_saved_snippets(self.request, self.user)
            if dispatch is not None:
                dispatch.assert_called_once_with(
                    [(row.id, row.title, row.content, -1) for row in self.rows]
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["saved_snippets"], self.expected)
        self.assertEqual(query.call_count, 2)
        for args, kwargs in query.call_args_list:
            self.assertEqual(args, ())
            self.assertEqual(kwargs, {"user_profile": self.user})

        with (
            patch.object(SavedSnippet.objects, "filter", return_value=self.rows),
            patch.object(actions, "_render_saved_snippet_display_rows", wraps=native) as call,
        ):
            self.assertEqual(actions.do_get_saved_snippets(self.user), self.expected)
            call.assert_not_called()

    def test_real_get_exact_fallback_and_order_for_old_or_declining_wheel(self) -> None:
        for result in (None, (), [], [0, 0]):
            with (
                patch.object(SavedSnippet.objects, "filter", return_value=iter(self.rows)),
                patch.object(
                    actions, "_render_saved_snippet_display_rows", return_value=result
                ) as native,
            ):
                response = get_saved_snippets(self.request, self.user)
                native.assert_called_once_with(
                    [(row.id, row.title, row.content, -1) for row in self.rows]
                )
            actual = json.loads(response.content)["saved_snippets"]
            self.assertEqual(actual, self.expected)
            self.assertEqual([list(row) for row in actual], [list(row) for row in self.expected])

    def test_noncanonical_rows_keep_original_methods(self) -> None:
        class Noncanonical:
            def to_api_dict(self) -> dict[str, str]:
                return {"custom": "unchanged"}

        with (
            patch.object(SavedSnippet.objects, "filter", return_value=[Noncanonical()]),
            patch.object(actions, "_render_saved_snippet_display_rows") as native,
        ):
            response = get_saved_snippets(self.request, self.user)
        native.assert_not_called()
        self.assertEqual(json.loads(response.content)["saved_snippets"], [{"custom": "unchanged"}])

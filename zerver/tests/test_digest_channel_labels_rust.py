from types import SimpleNamespace
from typing import cast
from unittest import TestCase
from unittest.mock import call, patch

from markupsafe import Markup

from zerver.lib import digest
from zerver.models import Realm, Stream


class DigestChannelLabelsRustTest(TestCase):
    def test_public_filter_and_original_markup_escaping(self) -> None:
        realm = cast("Realm", SimpleNamespace(url="https://example.test"))
        visible = cast(
            "Stream", SimpleNamespace(id=8, name="A & B", invite_only=False, is_web_public=True)
        )
        hidden = cast(
            "Stream", SimpleNamespace(id=9, name="hidden", invite_only=True, is_web_public=False)
        )
        with patch.object(digest, "stream_narrow_url", return_value="x'&y") as url:
            count, values = digest.gather_new_streams(realm, [hidden, visible], False)
        self.assertEqual(count, 1)
        url.assert_called_once_with(realm, visible)
        self.assertEqual(values["plain"], ["A & B"])
        self.assertEqual(type(values["html"][0]), Markup)
        self.assertIn("x&#39;&amp;y", values["html"][0])
        self.assertIn("A &amp; B", values["html"][0])

    def test_current_native_called_after_access_selection(self) -> None:
        native = digest._rust_render_prepared_digest_channel_labels
        if native is None:
            self.skipTest("requires the current native prepared digest channel label builder")
        realm = cast("Realm", SimpleNamespace(url="https://example.test"))
        allowed = cast(
            "Stream", SimpleNamespace(id=8, name="A", invite_only=False, is_web_public=False)
        )
        hidden = cast(
            "Stream", SimpleNamespace(id=9, name="B", invite_only=True, is_web_public=False)
        )
        with (
            patch.object(digest, "stream_narrow_url", return_value="url") as url,
            patch.object(
                digest, "_rust_render_prepared_digest_channel_labels", wraps=native
            ) as render,
        ):
            self.assertEqual(digest.gather_new_streams(realm, [hidden, allowed], True)[0], 1)
        self.assertEqual(url.call_args_list, [call(realm, allowed)])
        self.assertEqual(render.call_args.args[0], [allowed])

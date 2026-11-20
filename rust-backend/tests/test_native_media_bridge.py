# ruff: noqa: INP001
import copy
import re
import unittest
from importlib import import_module
from typing import cast

from typing_extensions import override


class NativeMediaBridgeTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.extension = import_module("_zulip_backend")
        self.media = (
            "chat.example",
            {"1/voice", "1/both"},
            {"1/picture": ("10", "20", "image/png"), "1/both": ("30", "40", None)},
            (
                (
                    {".tgz": ".tar.gz"},
                    {".gz"},
                    {".mp3": "audio/mpeg"},
                    {"audio/mpeg"},
                    {"", "https", "http"},
                ),
                "/static/",
                "https://",
                None,
                "https://camo.example/",
                "secret",
            ),
        )
        self.policy = (
            ["http", "https", "file", "mailto"],
            ["http", "https", "file"],
            False,
            "https://chat.example",
        )

    def arguments(self, text: str, kinds: tuple[int, ...] = (12, 13)) -> list[object]:
        return [
            [("div", "div", None, None, [1]), ("p", "p", (text, False), None, [])],
            [(kind, []) for kind in kinds],
            ["outer"],
            [(None, None)] * len(kinds),
            [None] * len(kinds),
        ]

    def test_preview_priority_and_duplicates(self) -> None:
        arguments = self.arguments("![x](/user_uploads/1/both) ![x](/user_uploads/1/both)")
        before = copy.deepcopy((arguments, self.media))
        result, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *arguments,
            link_policy=self.policy,
            media_inputs=self.media,
        )
        self.assertEqual(paths, ["1/both", "1/both"])
        self.assertFalse(has_image)
        self.assertEqual([node[0] for node in result[0]].count("audio"), 2)
        self.assertEqual(result[5], ([], [], False, False))
        self.assertEqual((arguments, self.media), before)
        result, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *self.arguments("![x](/user_uploads/1/both)", (13, 12)),
            link_policy=self.policy,
            media_inputs=self.media,
        )
        self.assertTrue(has_image)
        self.assertEqual(paths, ["1/both"])
        self.assertIn("img", [node[0] for node in result[0]])

    def test_rejected_candidates_keep_attachment_order(self) -> None:
        _, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *self.arguments("![x](/user_uploads/1/missing) ![x](/user_uploads/1/missing)"),
            link_policy=self.policy,
            media_inputs=self.media,
        )
        self.assertEqual(paths, ["1/missing"] * 4)
        self.assertFalse(has_image)

    def test_absent_and_empty_preview_snapshots(self) -> None:
        host, _, _, policy = self.media
        preview_cases: list[tuple[set[str] | None, str | None]] = [(None, "audio"), (set(), None)]
        for previews, expected in preview_cases:
            with self.subTest(previews=previews):
                result, paths, has_image = self.extension.plan_markdown_native_inline_media(
                    *self.arguments("![x](/user_uploads/1/sound.mp3)", (12,)),
                    link_policy=self.policy,
                    media_inputs=(host, previews, None, policy),
                )
                tags = [node[0] for node in result[0]]
                self.assertEqual("audio" in tags, expected == "audio")
                self.assertEqual(paths, ["1/sound.mp3"])
                self.assertFalse(has_image)
        self.assertIsNone(
            self.extension.plan_markdown_native_inline_media(
                *self.arguments("![x](/user_uploads/1/picture)"),
                link_policy=self.policy,
            )
        )

    def test_image_strings_remain_lossless(self) -> None:
        host, audio, _, policy = self.media
        width = "9" * 5000
        result, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *self.arguments("![<alt>](/user_uploads/1/picture?x#y 'title')", (13,)),
            link_policy=self.policy,
            media_inputs=(host, audio, {"1/picture?x#y": (width, "-20", "image/<custom>")}, policy),
        )
        attributes = next(dict(node[5]) for node in result[0] if node[0] == "img")
        self.assertEqual(attributes["data-original-dimensions"], width + "x-20")
        self.assertEqual(attributes["data-original-content-type"], "image/<custom>")
        self.assertEqual(attributes["alt"], "<alt>")
        self.assertEqual(paths, ["1/picture"])
        self.assertTrue(has_image)

    def test_audio_policy_changes(self) -> None:
        host, audio, images, policy = self.media
        mime, static, scheme, _, _, _ = policy
        for root, camo, expected in [
            (None, "", "https://other.example/a.mp3"),
            ("https://other.example/", "https://camo.example/", "https://other.example/a.mp3"),
            (None, "https://camo.example/", None),
        ]:
            with self.subTest(root=root, camo=camo):
                result, paths, has_image = self.extension.plan_markdown_native_inline_media(
                    *self.arguments("![voice](https://other.example/a.mp3)", (12,)),
                    link_policy=self.policy,
                    media_inputs=(
                        host,
                        audio,
                        images,
                        (mime, static, scheme, root, camo, "secret"),
                    ),
                )
                attrs = next(dict(node[5]) for node in result[0] if node[0] == "audio")
                if expected is None:
                    self.assertTrue(attrs["src"].startswith(camo))
                    self.assertEqual(attrs["data-original-url"], "https://other.example/a.mp3")
                else:
                    self.assertEqual(attrs["src"], expected)
                    self.assertNotIn("data-original-url", attrs)
                self.assertEqual(paths, [])
                self.assertFalse(has_image)

    def test_late_delegation_discards_all_media_effects(self) -> None:
        args = self.arguments("![x](/user_uploads/1/picture) UNSUPPORTED", (13, 0))
        cast(list[tuple[object, object]], args[3])[-1] = (re.compile("UNSUPPORTED"), None)
        before = copy.deepcopy(args)
        self.assertIsNone(
            self.extension.plan_markdown_native_inline_media(
                *args,
                link_policy=self.policy,
                media_inputs=self.media,
            )
        )
        self.assertEqual(args, before)

    def test_excluded_ancestors(self) -> None:
        args = self.arguments("![x](/user_uploads/1/picture)", (13,))
        args[1] = [(13, ["p"])]
        _, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *args,
            link_policy=self.policy,
            media_inputs=self.media,
        )
        self.assertEqual(paths, [])
        self.assertFalse(has_image)

    def test_media_channels_and_notifications_share_one_plan(self) -> None:
        result, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *self.arguments(
                "![x](/user_uploads/1/picture) @**Ada** @*team* #**general**", (13, 17, 18, 14)
            ),
            link_policy=self.policy,
            media_inputs=self.media,
            channel_inputs=({"general": (4, False)}, {}, "no topic", []),
            mention_inputs=(
                ({"Ada": (2, "Ada", True)}, {2: (2, "Ada", True)}, {"team": (3, "team", False)}),
                {},
                4300,
            ),
        )
        self.assertEqual(paths, ["1/picture"])
        self.assertTrue(has_image)
        self.assertEqual(result[5], ([2], [3], False, False))
        self.assertIn("a", [node[0] for node in result[0]])

    def test_invalid_input_does_not_change_caller_data(self) -> None:
        args = self.arguments("![x](/user_uploads/1/picture)")
        before = copy.deepcopy(args)
        with self.assertRaises(TypeError):
            self.extension.plan_markdown_native_inline_media(
                *args,
                link_policy=self.policy,
                media_inputs=(object(), None, None, self.media[3]),
            )
        self.assertEqual(args, before)
        args[3] = []
        self.assertIsNone(
            self.extension.plan_markdown_native_inline_media(
                *args,
                link_policy=self.policy,
                media_inputs=self.media,
            )
        )

    def test_legacy_result_shape(self) -> None:
        args = self.arguments("`code` [link](https://example/a)", (1, 11))
        legacy = self.extension.plan_markdown_native_inline(*args, link_policy=self.policy)
        result, paths, has_image = self.extension.plan_markdown_native_inline_media(
            *args,
            link_policy=self.policy,
        )
        self.assertEqual(result, legacy)
        self.assertEqual(paths, [])
        self.assertFalse(has_image)


if __name__ == "__main__":
    unittest.main()

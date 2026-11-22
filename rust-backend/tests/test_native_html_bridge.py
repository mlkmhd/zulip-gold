# ruff: noqa: INP001
import copy
import re
import unittest
from importlib import import_module


class NativeHtmlBridgeTest(unittest.TestCase):
    def arguments(self, text: str, kinds: tuple[int, ...] = (20,)) -> list[object]:
        return [
            [("div", "div", None, None, [1]), ("p", "p", (text, False), None, [])],
            [(kind, []) for kind in kinds],
            ["outer"],
            [(re.compile("unsupported"), None)] * len(kinds),
            [None] * len(kinds),
        ]

    def test_pending_entities_do_not_mutate_arguments(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("&amp; &#000; &#xFF; &unknown;")
        before = copy.deepcopy(args)
        plan, paths, image, blocks = extension.plan_markdown_native_inline_html(
            *args, html_counter=9999
        )
        self.assertEqual(blocks, ["&amp;", "&#000;", "&#xFF;", "&unknown;"])
        self.assertEqual(paths, [])
        self.assertFalse(image)
        self.assertEqual(
            plan[0][1][2],
            (
                (
                    "\x02wzxhzdk:9999\x03 \x02wzxhzdk:10000\x03 "
                    "\x02wzxhzdk:10001\x03 \x02wzxhzdk:10002\x03"
                ),
                False,
            ),
        )
        self.assertEqual(args, before)

    def test_old_entrypoints_cannot_drop_html_stash_effects(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("&amp;")
        self.assertIsNone(extension.plan_markdown_native_inline(*args))
        self.assertIsNone(extension.plan_markdown_native_inline_media(*args))
        self.assertIsNone(extension.plan_markdown_native_inline_html(*args))
        for value in (-1, 1 << 100):
            with self.subTest(value=value), self.assertRaises(OverflowError):
                extension.plan_markdown_native_inline_html(*args, html_counter=value)

    def test_late_guard_failure_discards_entities_and_nodes(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("**before** &amp; unsupported", (3, 20, 0))
        before = copy.deepcopy(args)
        self.assertIsNone(extension.plan_markdown_native_inline_html(*args, html_counter=7))
        self.assertEqual(args, before)

    def test_old_result_shapes_stay_compatible(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("**bold** `code`", (1, 3))
        legacy = extension.plan_markdown_native_inline(*args)
        media = extension.plan_markdown_native_inline_media(*args)
        html = extension.plan_markdown_native_inline_html(*args)
        self.assertEqual(len(legacy), 6)
        self.assertEqual(media, (legacy, [], False))
        self.assertEqual(html, (*media, []))


if __name__ == "__main__":
    unittest.main()

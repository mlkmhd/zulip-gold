# ruff: noqa: INP001
import copy
import re
import unittest
from importlib import import_module


class NativeAutolinkBridgeTest(unittest.TestCase):
    def arguments(self, text: str, kinds: tuple[int, ...] = (21,)) -> list[object]:
        return [
            [("div", "div", None, None, [1]), ("p", "p", (text, False), None, [])],
            [(kind, []) for kind in kinds],
            ["outer"],
            [(re.compile("unsupported"), None)] * len(kinds),
            [None] * len(kinds),
        ]

    def options(self) -> dict[str, object]:
        return {
            "link_policy": (["http", "https", "mailto"], ["http", "https"], False, None),
            "autolink_spec": ("15.0.0", ["com"], ["https?"], False),
        }

    def test_atomic_links_and_pending_html_leave_arguments_unchanged(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("&amp; example.com https://x?.\n", (20, 21))
        before = copy.deepcopy(args)
        plan, paths, image, blocks = extension.plan_markdown_native_inline_autolinks(
            *args, html_counter=7, **self.options()
        )
        self.assertEqual(args, before)
        self.assertEqual(blocks, ["&amp;"])
        self.assertEqual(paths, [])
        self.assertFalse(image)
        links = [node for node in plan[0] if node[0] == "a"]
        self.assertEqual([node[2] for node in links], [("example.com", True), ("https://x?", True)])
        self.assertEqual(
            [node[5] for node in links],
            [[("href", "http://example.com")], [("href", "https://x")]],
        )

    def test_missing_and_unsupported_specs_delegate_without_effects(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("&amp; example.com", (20, 21))
        before = copy.deepcopy(args)
        for spec in [
            None,
            ("changed", ["com"], ["https?"], False),
            ("15.0.0", ["com|.*"], ["https?"], False),
            ("15.0.0", ["com"], [".*"], False),
        ]:
            options = self.options()
            options["autolink_spec"] = spec
            with self.subTest(spec=spec):
                self.assertIsNone(
                    extension.plan_markdown_native_inline_autolinks(
                        *args, html_counter=7, **options
                    )
                )
                self.assertEqual(args, before)
        options = self.options()
        options["link_policy"] = None
        self.assertIsNone(
            extension.plan_markdown_native_inline_autolinks(*args, html_counter=7, **options)
        )
        self.assertEqual(args, before)

    def test_late_failure_discards_autolinks_and_html(self) -> None:
        extension = import_module("_zulip_backend")
        for text in ["&amp; example.com https://例.com", "&amp; example.com unsupported"]:
            args = self.arguments(text, (20, 21, 0))
            before = copy.deepcopy(args)
            self.assertIsNone(
                extension.plan_markdown_native_inline_autolinks(
                    *args, html_counter=7, **self.options()
                )
            )
            self.assertEqual(args, before)

    def test_older_entrypoints_reject_new_kind_and_keep_result_shapes(self) -> None:
        extension = import_module("_zulip_backend")
        args = self.arguments("example.com")
        for entry in [
            extension.plan_markdown_native_inline,
            extension.plan_markdown_native_inline_media,
            extension.plan_markdown_native_inline_html,
        ]:
            self.assertIsNone(entry(*args, link_policy=self.options()["link_policy"]))
        args = self.arguments("**bold**", (3,))
        self.assertEqual(
            extension.plan_markdown_native_inline_autolinks(*args),
            extension.plan_markdown_native_inline_html(*args),
        )


if __name__ == "__main__":
    unittest.main()

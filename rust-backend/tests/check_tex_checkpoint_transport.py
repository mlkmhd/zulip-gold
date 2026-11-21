# ruff: noqa: INP001

"""Exercise the internal TeX continuation transport with actual Python objects."""

import unittest
from importlib import import_module
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown

EXTENSION = import_module("_zulip_backend")


class RendererError(Exception):
    pass


class TexCheckpointTransportTest(unittest.TestCase):
    def fixture(self, data: str) -> tuple:
        root = Element("div", {"kept": "yes"})
        paragraph = SubElement(root, "p")
        paragraph.text = data
        md = Markdown()
        processor = SimpleNamespace(
            md=md,
            inlinePatterns=SimpleNamespace(_priority=[], _is_sorted=False),
        )
        snapshots = [
            ("div", "div", None, None, [1], [("kept", "yes")]),
            ("p", "p", (data, False), None, [], []),
        ]
        state = EXTENSION.preflight_markdown_tex_checkpoint(
            snapshots, [(20, []), (22, [])], [], md.htmlStash.html_counter
        )
        self.assertIsNotNone(state)
        committer = EXTENSION.MarkdownInlineCheckpoint(processor, [root, paragraph], [])
        return root, paragraph, md, state, committer

    def test_single_use_and_real_html_stash(self) -> None:
        root, paragraph, md, state, committer = self.fixture("&amp; $$first$$ $$second$$")
        self.assertEqual(state.body, "first")
        with self.assertRaisesRegex(RuntimeError, "take the TeX checkpoint first"):
            state.complete("")
        (plan, paths, image, blocks), history = state.take_checkpoint()
        with self.assertRaisesRegex(RuntimeError, "already taken"):
            state.take_checkpoint()
        self.assertEqual(paragraph.text, "&amp; $$first$$ $$second$$")
        self.assertEqual(md.htmlStash.rawHtmlBlocks, [])
        committer.apply(plan, paths, image, blocks, history)
        self.assertEqual(md.htmlStash.rawHtmlBlocks, ["&amp;"])
        second = state.complete("")
        with self.assertRaisesRegex(RuntimeError, "no pending"):
            state.complete("")
        self.assertEqual(second.body, "second")
        (plan, paths, image, blocks), history = second.take_checkpoint()
        committer.apply(plan, paths, image, blocks, history)
        self.assertEqual(md.htmlStash.rawHtmlBlocks, ["&amp;", ""])
        final = second.complete(None)
        self.assertIsNone(final.body)
        (plan, paths, image, blocks), history = final.take_checkpoint()
        committer.apply(plan, paths, image, blocks, history)
        self.assertIs(root[0], paragraph)
        self.assertEqual(root.attrib, {"kept": "yes"})
        self.assertEqual(paragraph[0].attrib, {"class": "tex-error"})
        self.assertEqual(paragraph[0].text, "$$second$$")
        self.assertEqual(md.htmlStash.rawHtmlBlocks, ["&amp;", ""])

    def test_caller_can_drop_continuation_on_original_exception(self) -> None:
        _, paragraph, md, checkpoint, committer = self.fixture("$$first$$ $$later$$")
        error = RendererError("first")
        seen = []

        def render(body: str) -> None:
            seen.append(body)
            paragraph.set("during-render", body)
            raise error

        with self.assertRaises(RendererError) as caught:
            (plan, paths, image, blocks), history = checkpoint.take_checkpoint()
            committer.apply(plan, paths, image, blocks, history)
            render(checkpoint.body)
        del checkpoint
        self.assertIs(caught.exception, error)
        self.assertEqual(seen, ["first"])
        self.assertEqual(paragraph.attrib, {"during-render": "first"})
        self.assertEqual(md.htmlStash.rawHtmlBlocks, [])

    def test_unknown_registry_and_over_budget_defer(self) -> None:
        for patterns, data in [
            ([(21, [])], "$$first$$"),
            ([(22, [])], " ".join(f"$${i}$$" for i in range(7))),
        ]:
            with self.subTest(patterns=patterns, data=data):
                nodes = [
                    ("div", "div", None, None, [1], []),
                    ("p", "p", (data, False), None, [], []),
                ]
                self.assertIsNone(
                    EXTENSION.preflight_markdown_tex_checkpoint(nodes, patterns, [], 0)
                )


if __name__ == "__main__":
    unittest.main()

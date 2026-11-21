# ruff: noqa: INP001

"""Exercise the internal TeX continuation transport with actual Python objects."""

import unittest
from importlib import import_module
from re import Match
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement

from markdown import Markdown
from markdown.inlinepatterns import Pattern
from markdown.treeprocessors import InlineProcessor
from markdown.util import AtomicString, Registry

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

    def test_untouched_plain_and_atomic_slots_keep_original_identity(self) -> None:
        root = Element("div")
        original = b"not interned".decode()
        root.text = original
        atomic = AtomicString("original atomic")
        first = SubElement(root, "p")
        first.text = atomic
        second = SubElement(root, "p")
        second.text = "$$first$$"
        original_tail = b"$$later$$".decode()
        second.tail = original_tail
        md = Markdown()
        processor = SimpleNamespace(
            md=md, inlinePatterns=SimpleNamespace(_priority=[], _is_sorted=False)
        )
        nodes = [
            ("div", "div", (original, False), None, [1, 2], []),
            ("p", "p", (atomic, True), None, [], []),
            ("p", "p", ("$$first$$", False), (original_tail, False), [], []),
        ]
        checkpoint = EXTENSION.preflight_markdown_tex_checkpoint(nodes, [(22, [])], [], 0)
        committer = EXTENSION.MarkdownInlineCheckpoint(processor, [root, first, second], [])
        for expected_body in ["first", "later"]:
            self.assertEqual(checkpoint.body, expected_body)
            plan, history, protected = checkpoint.take_checkpoint_with_text()
            self.assertEqual(protected, [(True, False), (True, False), (False, True)])
            committer.apply(*plan, history, protected)
            self.assertIs(root.text, original)
            self.assertIs(first.text, atomic)
            self.assertIs(second.tail, original_tail)
            checkpoint = checkpoint.complete("<b>output</b>")
        plan, history, protected = checkpoint.take_checkpoint_with_text()
        self.assertEqual(protected, [(True, False), (True, False), (False, False)])
        committer.apply(*plan, history, protected)
        self.assertIs(root.text, original)
        self.assertIs(first.text, atomic)
        self.assertIsNot(second.tail, original_tail)

    def test_reference_observes_same_untouched_slots_at_callbacks(self) -> None:
        root = Element("div")
        original = b"original root".decode()
        root.text = original
        first = SubElement(root, "p")
        atomic = AtomicString("original atomic")
        first.text = atomic
        second = SubElement(root, "p")
        second.text = "$$first$$"
        original_tail = b"$$later$$".decode()
        second.tail = original_tail
        md = Markdown()
        md.inlinePatterns = Registry()
        reference = InlineProcessor(md)
        seen: list[tuple[str, bool, bool, bool]] = []

        class ReferenceTex(Pattern):
            def handleMatch(self, match: Match[str]) -> str:
                seen.append(
                    (
                        match.group("body"),
                        root.text is original,
                        first.text is atomic,
                        second.tail is original_tail,
                    )
                )
                return md.htmlStash.store("<b>output</b>")

        regex = r"\B(?<!\$)\$\$(?P<body>[^\n_$](\\\$|[^$\n])*)\$\$(?!\$)\B"
        md.inlinePatterns.register(ReferenceTex(regex, md), "tex", 20)
        reference.run(root)
        self.assertEqual(seen, [("first", True, True, True), ("later", True, True, True)])
        self.assertIs(root.text, original)
        self.assertIs(first.text, atomic)
        self.assertIsNot(second.tail, original_tail)

    def test_equal_replacement_after_callback_is_not_overwritten(self) -> None:
        root, paragraph, md, _, committer = self.fixture("$$first$$ $$later$$")
        original = b"not interned".decode()
        root.text = original
        snapshots = [
            ("div", "div", (original, False), None, [1], [("kept", "yes")]),
            ("p", "p", ("$$first$$ $$later$$", False), None, [], []),
        ]
        first = EXTENSION.preflight_markdown_tex_checkpoint(snapshots, [(22, [])], [], 0)
        plan, history, protected = first.take_checkpoint_with_text()
        committer.apply(*plan, history, protected)
        replacement = original.encode().decode()
        root.text = replacement
        self.assertEqual(replacement, original)
        self.assertIsNot(replacement, original)
        second = first.complete("<b>output</b>")
        plan, history, protected = second.take_checkpoint_with_text()
        before = (paragraph.text, list(md.htmlStash.rawHtmlBlocks))
        with self.assertRaisesRegex(ValueError, "inconsistent inline checkpoint"):
            committer.apply(*plan, history, protected)
        self.assertIs(root.text, replacement)
        self.assertEqual((paragraph.text, list(md.htmlStash.rawHtmlBlocks)), before)

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

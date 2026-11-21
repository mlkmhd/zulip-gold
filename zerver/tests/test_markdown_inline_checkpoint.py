import copy
import gc
import weakref
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol, cast, override
from unittest import TestCase
from xml.etree.ElementTree import Element, SubElement

from markdown import util

if TYPE_CHECKING:
    from zerver.lib.markdown.native_inline import Plan


class Checkpoint(Protocol):
    def apply(
        self, plan: "Plan", paths: list[str], has_image: bool, html_blocks: list[str]
    ) -> None: ...


class MarkdownInlineCheckpointTest(TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        try:
            constructor = import_module("_zulip_backend").MarkdownInlineCheckpoint
        except (ImportError, AttributeError):
            self.skipTest("Native checkpoint committer is unavailable")
        self.root = Element("div", {"original": "retained"})
        self.paragraph = SubElement(self.root, "p")
        self.paragraph.text = "before"
        self.priority = [("entity", 20), ("tex", 10)]
        self.registry = SimpleNamespace(_priority=[], _is_sorted=False)
        self.metadata = SimpleNamespace(
            mentions_user_ids={901},
            mentions_user_group_ids={902},
            mentions_stream_wildcard=True,
            mentions_topic_wildcard=False,
            potential_attachment_path_ids=["old", "old"],
            has_image=True,
            links_for_preview={"sentinel"},
        )
        self.html = SimpleNamespace(rawHtmlBlocks=["old"], html_counter=7)
        self.processor = SimpleNamespace(
            md=SimpleNamespace(htmlStash=self.html, zulip_rendering_result=self.metadata),
            inlinePatterns=self.registry,
            stashed_nodes={"old": "old"},
            parent_map={},
            ancestors=["old"],
        )
        self.committer = cast(
            Checkpoint, constructor(self.processor, [self.root, self.paragraph], self.priority)
        )
        self.plan: Plan = (
            [
                ("div", "div", None, None, [1], []),
                ("p", "p", None, ("tail", False), [2, 2], []),
                ("span", "span", ("atomic", True), None, [], [("class", "tex-error")]),
            ],
            [(1, 0), (2, 1)],
            [(2, None), (None, ("raw", True))],
            ["outer", "div", "p"],
            True,
            ([10], [20], False, False),
        )

    def test_constructor_has_no_effects(self) -> None:
        self.assertEqual(self.paragraph.text, "before")
        self.assertEqual(self.processor.stashed_nodes, {"old": "old"})
        self.assertEqual(self.html.rawHtmlBlocks, ["old"])
        self.assertEqual(self.registry._priority, [])

    def test_cumulative_effects_and_element_identity(self) -> None:
        attributes = self.root.attrib
        blocks = self.html.rawHtmlBlocks
        users = self.metadata.mentions_user_ids
        paths = self.metadata.potential_attachment_path_ids
        self.committer.apply(self.plan, ["a", "a"], False, ["&amp;", ""])
        created = self.paragraph[0]
        stash = self.processor.stashed_nodes
        parent_map = self.processor.parent_map
        self.assertIs(self.paragraph[1], created)
        self.assertIs(self.processor.stashed_nodes["0000"], created)
        self.assertIs(self.processor.parent_map[created], self.paragraph)
        self.assertIsInstance(created.text, util.AtomicString)
        self.assertIsInstance(self.processor.stashed_nodes["0001"], util.AtomicString)
        self.assertEqual(created.attrib, {"class": "tex-error"})
        self.assertIs(self.root.attrib, attributes)
        self.assertEqual(attributes, {"original": "retained"})
        self.assertEqual(self.registry._priority, self.priority)
        self.assertTrue(self.registry._is_sorted)
        self.priority.clear()
        next_plan = copy.deepcopy(self.plan)
        next_plan[0][2] = ("span", "span", ("changed", False), None, [], [("class", "tex-error")])
        next_plan[5][0].append(11)
        self.committer.apply(next_plan, ["a", "a", "b"], True, ["&amp;", "", "third"])
        self.committer.apply(next_plan, ["a", "a", "b"], True, ["&amp;", "", "third"])
        self.assertIs(self.paragraph[0], created)
        self.assertIs(self.processor.stashed_nodes, stash)
        self.assertIs(self.processor.parent_map, parent_map)
        self.assertEqual(created.text, "changed")
        self.assertIs(type(created.text), str)
        self.assertIs(self.html.rawHtmlBlocks, blocks)
        self.assertEqual(blocks, ["old", "&amp;", "", "third"])
        self.assertEqual(self.html.html_counter, 10)
        self.assertIs(self.metadata.mentions_user_ids, users)
        self.assertEqual(users, {901, 10, 11})
        self.assertEqual(self.metadata.mentions_user_group_ids, {902, 20})
        self.assertIs(self.metadata.potential_attachment_path_ids, paths)
        self.assertEqual(paths, ["old", "old", "a", "a", "b"])
        self.assertTrue(self.metadata.mentions_stream_wildcard)
        self.assertTrue(self.metadata.has_image)
        self.assertEqual(self.metadata.links_for_preview, {"sentinel"})
        self.assertEqual(self.registry._priority, [("entity", 20), ("tex", 10)])

    def test_invalid_graph_is_rejected_before_mutation(self) -> None:
        invalid = copy.deepcopy(self.plan)
        invalid[0][1][4].append(99)
        with self.assertRaisesRegex(ValueError, "inconsistent inline checkpoint"):
            self.committer.apply(invalid, [], False, [])
        self.assertEqual(self.paragraph.text, "before")
        self.assertEqual(list(self.paragraph), [])
        self.assertEqual(self.processor.stashed_nodes, {"old": "old"})
        self.committer.apply(self.plan, [], False, [])
        self.assertIsNone(self.paragraph.text)

    def test_rewritten_prefix_is_rejected(self) -> None:
        self.committer.apply(self.plan, ["first"], False, ["first"])
        for paths, blocks in [([], ["first"]), (["other"], ["first"]), (["first"], ["other"])]:
            with (
                self.subTest(paths=paths, blocks=blocks),
                self.assertRaisesRegex(ValueError, "inconsistent inline checkpoint"),
            ):
                self.committer.apply(self.plan, paths, False, blocks)
        self.assertEqual(self.html.rawHtmlBlocks, ["old", "first"])
        self.assertEqual(self.metadata.potential_attachment_path_ids, ["old", "old", "first"])

    def test_changed_html_counter_is_rejected_before_graph_mutation(self) -> None:
        self.html.html_counter += 1
        with self.assertRaisesRegex(ValueError, "inconsistent inline checkpoint"):
            self.committer.apply(self.plan, [], False, [])
        self.assertEqual(self.paragraph.text, "before")

    def test_invalid_stash_parents_and_new_node_prefix(self) -> None:
        for kind in ["parent", "missing-stash", "double-stash", "stash-index"]:
            invalid = copy.deepcopy(self.plan)
            if kind == "parent":
                invalid[1].append((99, 0))
            elif kind == "missing-stash":
                invalid[2].append((None, None))
            elif kind == "double-stash":
                invalid[2].append((0, ("text", False)))
            else:
                invalid[2].append((99, None))
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(ValueError, "inconsistent inline checkpoint"),
            ):
                self.committer.apply(invalid, [], False, [])
            self.assertEqual(self.paragraph.text, "before")
        self.committer.apply(self.plan, [], False, [])
        invalid = copy.deepcopy(self.plan)
        invalid[0][2][5].append(("extra", "attribute"))
        with self.assertRaisesRegex(ValueError, "inconsistent inline checkpoint"):
            self.committer.apply(invalid, [], False, [])
        self.assertEqual(self.paragraph[0].attrib, {"class": "tex-error"})

    def test_callback_metadata_additions_survive_later_checkpoint(self) -> None:
        self.committer.apply(self.plan, ["a"], False, [])
        self.metadata.potential_attachment_path_ids.append("from-renderer")
        self.metadata.mentions_user_ids.add(99)
        self.committer.apply(self.plan, ["a", "b"], False, [])
        self.assertEqual(
            self.metadata.potential_attachment_path_ids,
            ["old", "old", "a", "from-renderer", "b"],
        )
        self.assertEqual(self.metadata.mentions_user_ids, {901, 10, 99})

    def test_commit_exception_poisoning(self) -> None:
        class CommitError(Exception):
            pass

        class BrokenList(list[str]):
            @override
            def extend(self, values: object) -> None:
                raise error

        error = CommitError("sentinel")
        self.html.rawHtmlBlocks = BrokenList(["old"])
        with self.assertRaises(CommitError) as caught:
            self.committer.apply(self.plan, [], False, ["new"])
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.paragraph.text)
        self.assertIs(self.processor.stashed_nodes["0000"], self.paragraph[0])
        with self.assertRaisesRegex(RuntimeError, "commit already failed"):
            self.committer.apply(self.plan, [], False, ["new"])
        self.assertEqual(self.html.rawHtmlBlocks, ["old"])

    def test_renderer_exception_retains_applied_state(self) -> None:
        class RendererError(Exception):
            pass

        self.committer.apply(self.plan, [], False, ["&amp;"])
        created = self.paragraph[0]
        error = RendererError("sentinel")

        def render() -> None:
            created.set("callback", "retained")
            self.metadata.links_for_preview.add("from-renderer")
            raise error

        with self.assertRaises(RendererError) as caught:
            render()
        self.assertIs(caught.exception, error)
        self.assertIs(self.processor.stashed_nodes["0000"], created)
        self.assertEqual(created.get("callback"), "retained")
        self.assertEqual(self.metadata.links_for_preview, {"sentinel", "from-renderer"})
        self.assertEqual(self.html.rawHtmlBlocks, ["old", "&amp;"])

    def test_processor_cycle_is_collected(self) -> None:
        class Marker:
            pass

        marker = Marker()
        reference = weakref.ref(marker)
        self.processor.marker = marker
        self.processor.checkpoint = self.committer
        del marker, self.processor, self.committer
        gc.collect()
        self.assertIsNone(reference())

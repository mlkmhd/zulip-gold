from random import Random
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib.markdown import TopicLinkMatch, native_topic_links


def original_indices(matches: list[TopicLinkMatch]) -> list[int]:
    indices = sorted(
        range(len(matches)),
        key=lambda i: (
            matches[i].precedence is None,
            (matches[i].precedence, matches[i].index) if matches[i].precedence is not None else (),
        ),
    )

    def inside(a: TopicLinkMatch, b: TopicLinkMatch) -> bool:
        return b.index <= a.index < b.index + len(b.text)

    chosen: list[int] = []
    for i in indices:
        if all(
            not inside(matches[i], matches[j]) and not inside(matches[j], matches[i])
            for j in chosen
        ):
            chosen.append(i)
    return sorted(chosen, key=lambda index: matches[index].index)


class PreparedTopicLinkSelectionRustTest(SimpleTestCase):
    def test_selected_links_keep_original_values_and_order(self) -> None:
        backend = native_topic_links._selection_backend
        if backend is None:
            self.skipTest("Prepared topic link selection extension is unavailable")
        rng = Random(5189)
        with patch.object(native_topic_links, "_backend", None):
            for case in range(320):
                candidates = [
                    TopicLinkMatch(
                        f"https://example.test/{case}/{i}",
                        rng.choice(("", "🙂", "سلام", "a", "e\u0301")),
                        rng.randrange(16),
                        rng.choice((None, 0, 1, 1)),
                    )
                    for i in range(rng.randrange(1, 30))
                ]
                expected = [candidates[i] for i in original_indices(candidates)]
                selected = native_topic_links.select(candidates)
                assert selected is not None
                self.assertEqual(selected, [{"url": row.url, "text": row.text} for row in expected])
                for wire, prepared in zip(selected, expected, strict=True):
                    self.assertIs(wire["url"], prepared.url)
                    self.assertIs(wire["text"], prepared.text)

    def test_default_selection_declines_unusual_values_before_native_lookup(self) -> None:
        class Text(str):
            __slots__ = ()

        if native_topic_links._selection_backend is None:
            self.skipTest("Prepared topic link selection extension is unavailable")
        with patch.object(native_topic_links, "_backend", None):
            self.assertIsNone(
                native_topic_links.select(
                    [TopicLinkMatch("https://example.test/", Text("a"), 0, 0)]
                )
            )
            self.assertIsNone(
                native_topic_links.select([TopicLinkMatch("https://example.test/", "a", -1, 0)])
            )
            self.assertIsNone(
                native_topic_links.select([TopicLinkMatch("https://example.test/", "\ud800", 0, 0)])
            )

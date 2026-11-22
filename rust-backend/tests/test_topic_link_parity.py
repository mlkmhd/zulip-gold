# ruff: noqa: INP001
import itertools
import random
import re
import unicodedata
import unittest
from importlib import import_module
from pathlib import Path
from typing import TypeAlias
from urllib.parse import urlsplit

from check_autolink_parity import cases, patterns

Candidate: TypeAlias = tuple[str, str, int, int | None]
SPLITTER = re.compile(r"""[ !;),'"]""")


def raw_matches(regex: re.Pattern[str], topic: str) -> list[Candidate]:
    matches: list[Candidate] = []
    pos = 0
    while pos < len(topic):
        stop = SPLITTER.search(topic, pos)
        end = stop.start() if stop is not None else len(topic)
        match = regex.match(topic[pos:end])
        if match:
            displayed = match["url"]
            parts = urlsplit(displayed)
            if not parts.scheme:
                if not parts.netloc:
                    slash = (parts.path + "/").index("/")
                    parts = parts._replace(netloc=parts.path[:slash], path=parts.path[slash:])
                url = parts._replace(scheme="https").geturl()
            else:
                url = displayed
            matches.append((url, displayed, pos, None))
        pos = end + 1
    return matches


def select_matches(matches: list[Candidate]) -> list[tuple[str, str]]:
    linkifier_matches = [match for match in matches if match[3] is not None]
    linkifier_matches.sort(key=lambda match: (match[3], match[2]))
    raw = [match for match in matches if match[3] is None]
    kept: list[Candidate] = []

    def overlap(left: Candidate, right: Candidate) -> bool:
        _, a, p, _ = left
        _, b, q, _ = right
        return q <= p < q + len(b) or p <= q < p + len(a)

    for candidate in linkifier_matches + raw:
        if all(not overlap(candidate, previous) for previous in kept):
            kept.append(candidate)
    kept.sort(key=lambda match: match[2])
    return [(url, label) for url, label, _, _ in kept]


class TopicLinkParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = import_module("_zulip_backend")
        source = Path(__file__).resolve().parents[2] / "zerver/lib/markdown/__init__.py"
        cls.tlds, cls.schemes, cls.regexes = patterns(source)

    def compare_plan(self, topic: str, files: bool, linkifiers: list[Candidate]) -> bool:
        result = self.native.plan_markdown_topic_links(
            topic, linkifiers, (unicodedata.unidata_version, self.tlds, self.schemes, files)
        )
        if result is None:
            return False
        reference = select_matches(linkifiers + raw_matches(self.regexes[files], topic))
        self.assertEqual(result, reference, (topic, files, linkifiers))
        return True

    def test_scan_delimiters_preserve_legacy_start_index(self) -> None:
        topic = "🙂(example.com/ref,  example.org; x@example.com"
        # The 'example.com/ref' link begins inside the segment, but loses
        # to a preceding one-codepoint linkifier according to the reference.
        linkifiers = [("https://example.test/emoji", "🙂", 0, 0)]
        self.assertTrue(self.compare_plan(topic, False, linkifiers))
        self.assertEqual(
            self.native.plan_markdown_topic_links(
                topic, linkifiers, (unicodedata.unidata_version, self.tlds, self.schemes, False)
            ),
            [
                ("https://example.test/emoji", "🙂"),
                ("https://example.org", "example.org"),
                ("https://x@example.com", "x@example.com"),
            ],
        )

    def test_seeded_topic_scan_and_overlap(self) -> None:
        rng = random.Random(387_947)
        native_count = 0
        delegated = 0
        for text, files in cases(443_811, 60_000, unicode_scalars=False):
            candidate_count = rng.randrange(4)
            linkifiers = []
            for idx in range(candidate_count):
                start = rng.randrange(len(text) + 1)
                label = text[start : start + rng.randrange(14)]
                linkifiers.append((f"https://example.test/linkifier/{idx}", label, start, idx % 2))
            if self.compare_plan(text, files, linkifiers):
                native_count += 1
            else:
                delegated += 1
        self.assertGreater(native_count, 59_000)
        print(f"Topic scan differential: {native_count} native, {delegated} delegated")

    def test_seeded_selection(self) -> None:
        rng = random.Random(53_609)
        for row in range(12_000):
            candidates = [
                (
                    f"https://example.test/{row}/{idx}",
                    rng.choice(("", "س", "AB", "🧪🙂", "A\u0301ب", "猫かなZ", "space label")),
                    rng.randrange(32),
                    rng.choice((None, None, 0, 0, 1, 2)),
                )
                for idx in range(rng.randrange(24))
            ]
            result = self.native.select_markdown_topic_links(candidates)
            self.assertEqual(result, select_matches(candidates))

    def test_file_flag_and_legacy_punctuation(self) -> None:
        for files, prefix, suffix in itertools.product(
            (False, True),
            ("", "before ", "\t", "🙂(", "<", "a:"),
            ("", ".", "?", "!!", "\ttext", " tail", "/"),
        ):
            for subject in (
                "file:///tmp/topic.txt",
                "example.com",
                "https://x?",
                "http://例.com/path",
            ):
                self.compare_plan(prefix + subject + suffix, files, [])

    def test_unsupported_type_and_budget(self) -> None:
        spec = (unicodedata.unidata_version, self.tlds, self.schemes, False)
        self.assertIsNone(self.native.plan_markdown_topic_links("example.com\ud800", [], spec))
        self.assertIsNone(self.native.plan_markdown_topic_links("a" * 16_385, [], spec))
        self.assertIsNone(
            self.native.select_markdown_topic_links([("https://example.test", "\ud800", 0, 0)])
        )
        self.assertIsNone(self.native.select_markdown_topic_links([("url", "A", -1, 0)]))


if __name__ == "__main__":
    unittest.main()

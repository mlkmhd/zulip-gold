import random
from unittest import TestCase, mock

from markdown import Markdown, inlinepatterns

from zerver.lib.markdown import ZulipMarkdown, emphasis

PATTERNS = [
    (r"(\*\*\*)(?!\s+)([^\*^\n]+)(?<!\s)\*\*\*", "strong,em", "strong_em", 100),
    (r"(\*\*)([^\n]+?)\2", "strong", "strong", 35),
    (r"(\*)(?!\s+)([^\*^\n]+)(?<!\s)\*", "em", "emphasis", 30),
    (r"(?<!~)(\~\~)([^~\n]+?)(\~\~)(?!~)", "del", "del", 25),
]


class MarkdownEmphasisTest(TestCase):
    def engine(self, *, use_python: bool = False) -> Markdown:
        engine = Markdown()
        for pattern, tag, name, priority in PATTERNS:
            if use_python:
                processor = (
                    inlinepatterns.DoubleTagPattern(pattern, tag)
                    if tag == "strong,em"
                    else inlinepatterns.SimpleTagPattern(pattern, tag)
                )
            else:
                processor = emphasis.tag_pattern(pattern, tag)
            engine.inlinePatterns.register(processor, name, priority)
        return engine

    def test_registered_patterns(self) -> None:
        engine = ZulipMarkdown(linkifiers=[], linkifiers_key=-1, email_gateway=False)
        for pattern, _, name, _ in PATTERNS:
            self.assertEqual(engine.inlinePatterns[name].pattern, pattern)

    def test_nested_formatting_and_boundaries(self) -> None:
        engine = self.engine()
        self.assertEqual(
            engine.convert("***عنوان*** **پررنگ** *کج* ~~خط‌خورده~~"),
            "<p><strong><em>عنوان</em></strong> <strong>پررنگ</strong> "
            "<em>کج</em> <del>خط‌خورده</del></p>",
        )
        for content in [
            "**a**b**",
            "*****",
            "****x****",
            "***x**y*",
            "~~a~~~ ~~b~~",
            "~~~a~~",
            "*a^b*",
            "*a\nb*",
            "**a\nb**",
            "*\u001cx* *x\u0085* *\u200bx*",
            "**~~a~~ *b***",
            "<script>**x**</script> & **<>&**",
            "`**x**` and **`a*b`**",
            "**[link](https://zulip.com)**",
            "**\ud800** *\udfff* ~~\ud800~~",
        ]:
            with self.subTest(content=content):
                self.assertEqual(
                    engine.convert(content), self.engine(use_python=True).convert(content)
                )

    def test_generated_formatting(self) -> None:
        rng = random.Random(0)
        alphabet = [
            "*",
            "**",
            "***",
            "~",
            "~~",
            "a",
            "عنوان📝",
            " ",
            "\n",
            "\u001c",
            "^",
            "`",
            "\\",
            "<>&",
        ]
        engine = self.engine()
        reference = self.engine(use_python=True)
        for _ in range(2000):
            content = "".join(rng.choices(alphabet, k=rng.randrange(50)))
            with self.subTest(content=content):
                self.assertEqual(engine.convert(content), reference.convert(content))

    def test_match_captures_and_surrogate_fallback(self) -> None:
        for pattern, tag, _, _ in PATTERNS:
            reference = (
                inlinepatterns.DoubleTagPattern(pattern, tag)
                if tag == "strong,em"
                else inlinepatterns.SimpleTagPattern(pattern, tag)
            )
            actual = emphasis.tag_pattern(pattern, tag)
            for content in [
                "plain text",
                "before **a** *b* ***c*** ~~d~~ after",
                "**\ud800** *\ud800* ***\ud800*** ~~\ud800~~",
            ]:
                expected_match = reference.getCompiledRegExp().match(content)
                actual_match = actual.getCompiledRegExp().match(content)
                with self.subTest(content=content, tag=tag):
                    if expected_match is None:
                        self.assertIsNone(actual_match)
                    else:
                        assert actual_match is not None
                        self.assertEqual(actual_match.groups(), expected_match.groups())
                        self.assertEqual(actual_match.group(), expected_match.group())
                        self.assertEqual(actual_match.group(3), expected_match.group(3))
                        self.assertEqual(actual_match.start(), expected_match.start())
                        self.assertEqual(actual_match.end(), expected_match.end())

    def test_rust_path(self) -> None:
        if emphasis._rust_emphasis is None:
            self.skipTest("Rust emphasis parser is disabled")
        engine = self.engine()
        for _, _, name, _ in PATTERNS:
            pattern = engine.inlinePatterns[name].getCompiledRegExp()
            assert isinstance(pattern, emphasis.EmphasisPattern)
            fallback = mock.Mock()
            fallback.match.side_effect = AssertionError
            pattern.fallback = fallback
        self.assertEqual(
            engine.convert("***a*** **b** *c* ~~d~~"),
            "<p><strong><em>a</em></strong> <strong>b</strong> <em>c</em> <del>d</del></p>",
        )

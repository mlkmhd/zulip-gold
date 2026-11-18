import random
from unittest import TestCase, mock

from markdown import Markdown
from markdown.blockprocessors import HashHeaderProcessor as MarkdownHashHeaderProcessor

from zerver.lib.markdown import HashHeaderProcessor


class PythonHashHeaderProcessor(MarkdownHashHeaderProcessor):
    RE = HashHeaderProcessor.RE


class MarkdownHeadingTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown()
        processor: MarkdownHashHeaderProcessor
        if use_python:
            processor = PythonHashHeaderProcessor(engine.parser)
        else:
            processor = HashHeaderProcessor(engine.parser)
        engine.parser.blockprocessors.register(processor, "hashheader", 70)
        return engine.convert(content)

    def test_headings_and_surrounding_blocks(self) -> None:
        self.assertEqual(
            self.render("before\n### عنوان 📝 ###\nafter"),
            "<p>before</p>\n<h3>عنوان 📝</h3>\n<p>after</p>",
        )
        for content in [
            "#title",
            "####### title",
            "# title\\#",
            "#\nnext",
            "# broken\\\n## valid",
            "#\u001c\u001ftitle\u001f###",
            "## عنوان\n# second\ntext",
            "* first\n\n    # heading\n    continuation\n\n* second",
            "* first\n\n* # heading\n    continuation",
            "# <script>alert('x')</script>",
            "# **bold** & [link](https://zulip.com)",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_generated_blocks(self) -> None:
        rng = random.Random(0)
        alphabet = "# \n\\\t\rعنوان📝<>*&\u001c\u0085\u2028"
        for _ in range(3000):
            content = "".join(rng.choices(alphabet, k=rng.randrange(50)))
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_surrogate_delegation(self) -> None:
        content = "before\n# \ud800 title\nafter"
        self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_heading_levels_and_unicode_separators(self) -> None:
        for level in range(1, 8):
            for separator in [
                " ",
                "\t",
                "\n",
                "\r",
                "\u001c",
                "\u0085",
                "\u2028",
                "\u3000",
                "\u200b",
            ]:
                content = f"{'#' * level}{separator}عنوان ###\nafter"
                with self.subTest(level=level, separator=separator):
                    self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_rust_path(self) -> None:
        from zerver.lib import markdown

        if markdown._rust_markdown_headings is None:
            self.skipTest("Rust heading parser is disabled")
        with mock.patch.object(HashHeaderProcessor, "RE", None):
            self.assertEqual(self.render("# title"), "<h1>title</h1>")

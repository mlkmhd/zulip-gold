import random
from unittest import TestCase, mock

from markdown import Markdown
from markdown.blockprocessors import HRProcessor as PythonHRProcessor

from zerver.lib.markdown import HRProcessor


class MarkdownHorizontalRuleTest(TestCase):
    def render(self, content: str, use_python: bool = False) -> str:
        engine = Markdown()
        processor = PythonHRProcessor if use_python else HRProcessor
        engine.parser.blockprocessors.register(processor(engine.parser), "hr", 70)
        return engine.convert(content)

    def test_rules_and_surrounding_blocks(self) -> None:
        self.assertEqual(
            self.render("عنوان\n\n---\n\nnext\n***"),
            "<p>عنوان</p>\n<hr />\n<p>next</p>\n<hr />",
        )
        for content in [
            "* * *",
            "_  _  _",
            "   -  --   ",
            "    ---",
            "-   - -",
            "\t---",
            "---\r",
            "**",
            "-_*",
            "*\u00a0**",
            "---\n___\n***",
            "* first\n\n    ---\n    continuation\n\n* second",
            "> before\n> ---\n> after",
            "before\n---\nafter",
            "<script>alert('x')</script>\n***\n**bold** & [link](https://zulip.com)",
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_generated_blocks(self) -> None:
        rng = random.Random(0)
        alphabet = "-_* \n\t\rعنوان📝<>\\\u001c\u0085\u2028"
        for _ in range(3000):
            content = "".join(rng.choices(alphabet, k=rng.randrange(50)))
            with self.subTest(content=content):
                self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_surrogate_delegation(self) -> None:
        content = "before\ud800\n---\nafter"
        self.assertEqual(self.render(content), self.render(content, use_python=True))

    def test_rust_path(self) -> None:
        from zerver.lib import markdown

        if markdown._rust_markdown_horizontal_rules is None:
            self.skipTest("Rust horizontal rule parser is disabled")
        with mock.patch.object(HRProcessor, "SEARCH_RE", None):
            self.assertEqual(self.render("---"), "<hr />")

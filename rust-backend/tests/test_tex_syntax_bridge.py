# ruff: noqa: INP001
import re
import unittest
from importlib import import_module


class TexSyntaxBridgeTest(unittest.TestCase):
    def test_captures_match_the_legacy_pattern_wrapper(self) -> None:
        extension = import_module("_zulip_backend")
        reference = re.compile(
            r"^(.*?)\B(?<!\$)\$\$(?P<body>[^\n_$](\\\$|[^$\n])*)\$\$(?!\$)\B(.*)$",
            re.DOTALL | re.UNICODE,
        )
        for data in [
            "",
            "$$x$$",
            "📝 $$α_1$$ : $$later$$\n",
            r"$$x\$$",
            r"$$x\$$$",
            r"$$x\$y$$",
            r"$$\$$",
            r"$$x\$$$$ : $$y$$",
            "a$$x$$",
            "²$$x$$",
            "$$x$$سلام",
            "$$x$$²",
            "\u0301$$x$$\u0301",
            "$$bad$ : $$good$$",
            "$$x\n$$y$$",
            "$$\r$$",
            "$$x\0y$$\n",
        ]:
            with self.subTest(data=data):
                matched = reference.match(data)
                self.assertEqual(
                    extension.find_markdown_tex(data, "15.0.0"),
                    (None if matched is None else matched.groups(),),
                )

    def test_delegation_is_distinct_from_a_missing_match(self) -> None:
        extension = import_module("_zulip_backend")
        self.assertEqual(extension.find_markdown_tex("plain text", "15.0.0"), (None,))
        self.assertIsNone(extension.find_markdown_tex("$$x$$", "changed"))
        self.assertIsNone(extension.find_markdown_tex("$$\ud800$$", "15.0.0"))
        self.assertIsNone(extension.find_markdown_tex("x" * (8 * 1024 * 1024 + 1), "15.0.0"))
        self.assertEqual(extension.find_markdown_tex("x" * (8 * 1024 * 1024), "15.0.0"), (None,))


if __name__ == "__main__":
    unittest.main()

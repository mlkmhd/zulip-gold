from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import url_decoding
from zerver.lib.narrow_helpers import NarrowTerm
from zerver.lib.url_decoding import parse_narrow_url


class NarrowTermGrammarRustTest(SimpleTestCase):
    def test_actual_link_keeps_order_aliases_negation_and_scope(self) -> None:
        link = "#narrow/-StReAm/9-مثال/.20Subject.20/.20آرش.20/-pm-with/7,2,7-group/near/008"
        expected = [
            NarrowTerm("channel", 9, True),
            NarrowTerm("topic", "آرش", False),
            NarrowTerm("dm", [7, 2, 7], True),
            NarrowTerm("near", 8, False),
        ]
        self.assertEqual(parse_narrow_url(link), expected)
        with patch.object(url_decoding, "_rust_narrow_term_grammar", ModuleType("old")):
            self.assertEqual(parse_narrow_url(link), expected)

    def test_known_empty_topic_and_legacy_invalid_case_distinctions(self) -> None:
        self.assertEqual(parse_narrow_url("#narrow/topic"), [NarrowTerm("topic", "", False)])
        self.assertEqual(parse_narrow_url("#narrow/-topic/"), [NarrowTerm("topic", "", True)])
        self.assertEqual(parse_narrow_url("#anything"), [])
        for link in (
            "#narrow/topic/a/",
            "#narrow/channel/legacy-name",
            "#narrow//abc",
            "#narrow/is/",
        ):
            with self.subTest(link=link):
                self.assertIsNone(parse_narrow_url(link))

    def test_noncanonical_unicode_and_large_near_fall_back(self) -> None:
        self.assertEqual(parse_narrow_url("#narrow/ÄBC/Námé"), [NarrowTerm("äbc", "Námé", False)])
        self.assertEqual(parse_narrow_url("#narrow/near/١٢"), [NarrowTerm("near", 12, False)])
        self.assertEqual(
            parse_narrow_url("#narrow/near/9223372036854775808"),
            [NarrowTerm("near", 2**63, False)],
        )
        with self.assertRaises(ValueError):
            parse_narrow_url("#narrow/near/²")

    def test_native_gets_extracted_text_not_url_or_hostname(self) -> None:
        native = url_decoding._rust_narrow_term_grammar
        if native is None:
            self.skipTest("requires current native prepared narrow terms")
        with patch.object(
            native, "parse_prepared_narrow_terms", wraps=native.parse_prepared_narrow_terms
        ) as parser:
            actual = parse_narrow_url("https://synthetic.invalid/view#narrow/topic/بحث")
            parser.assert_called_once_with("narrow/topic/بحث", url_decoding.OPERATOR_SYNONYMS)
        self.assertEqual(actual, [NarrowTerm("topic", "بحث", False)])

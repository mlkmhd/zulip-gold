from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils.translation import override as override_language

from zerver.lib import onboarding
from zerver.lib.onboarding import select_welcome_bot_response


class WelcomeBotReplyRustTest(SimpleTestCase):
    def test_every_alias_has_the_same_localized_response(self) -> None:
        cases = [
            ("app", "apps"),
            ("profile",),
            ("theme",),
            ("stream", "streams", "channel", "channels"),
            ("topic", "topics"),
            ("keyboard", "shortcuts", "keyboard shortcuts"),
            ("formatting", "message formatting"),
            ("help", "?"),
            ("", " help ", "Apps", "👋", "سلام", "\udfff", "help\n"),
        ]
        for language in ("en", "fr"):
            with override_language(language):
                with patch.object(onboarding, "_rust_welcome_bot_reply", ModuleType("old")):
                    reference = [select_welcome_bot_response(aliases[0]) for aliases in cases]
                self.assertEqual(len(set(reference)), len(cases))
                for aliases, expected in zip(cases, reference, strict=True):
                    for alias in aliases:
                        with self.subTest(language=language, alias=alias):
                            self.assertEqual(select_welcome_bot_response(alias), expected)

    def test_reply_retains_formatted_links_and_help_options(self) -> None:
        with override_language("en"):
            self.assertIn(
                "[Browse and subscribe to channels](#channels/all)",
                select_welcome_bot_response("channels"),
            )
            self.assertIn(
                "[Getting started guide](/help/getting-started-with-zulip)",
                select_welcome_bot_response("?"),
            )
            self.assertNotIn(", 'help'", select_welcome_bot_response("?"))
            self.assertNotIn(", \x60help\x60", select_welcome_bot_response("?"))
            self.assertIn(", \x60help\x60", select_welcome_bot_response("other"))

    def test_current_native_dispatch_without_normalizing_again(self) -> None:
        native = onboarding._rust_welcome_bot_reply
        if native is None:
            self.skipTest("requires current native welcome bot contract")
        with patch.object(
            native,
            "classify_prepared_welcome_bot_reply",
            wraps=native.classify_prepared_welcome_bot_reply,
        ) as classifier:
            response = select_welcome_bot_response(" Help ")
        classifier.assert_called_once_with(" Help ")
        self.assertEqual(response, select_welcome_bot_response(""))

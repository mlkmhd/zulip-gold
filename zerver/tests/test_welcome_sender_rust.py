from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from zerver.lib import email_notifications as emails


class WelcomeSenderRustTest(TestCase):
    def test_reads_configured_fields_in_original_order(self) -> None:
        options = Mock()
        property_ = PropertyMock(side_effect=[{}, {"name": "Demo"}, {"email": "demo@example.test"}])
        type(options).WELCOME_EMAIL_SENDER = property_
        with (
            patch.object(emails, "settings", options),
            patch.object(emails, "FromAddress", object()),
        ):
            self.assertEqual(emails.welcome_sender_information(), ("Demo", "demo@example.test"))
        self.assertEqual(property_.call_count, 3)

    def test_uses_default_only_if_configuration_is_none(self) -> None:
        placeholder = "fallback@example.test"
        with (
            patch.object(emails, "settings", SimpleNamespace(WELCOME_EMAIL_SENDER=None)),
            patch.object(emails, "FromAddress", SimpleNamespace(support_placeholder=placeholder)),
        ):
            selected = emails.welcome_sender_information()
        self.assertIs(type(selected), tuple)
        self.assertEqual(selected, (None, placeholder))
        with (
            patch.object(emails, "settings", SimpleNamespace(WELCOME_EMAIL_SENDER={})),
            self.assertRaises(KeyError) as error,
        ):
            emails.welcome_sender_information()
        self.assertEqual(error.exception.args, ("name",))

    def test_actual_adapter_uses_the_current_native_module(self) -> None:
        native = emails._rust_welcome_sender
        if native is None:
            self.skipTest("requires the current native welcome sender selection contract")
        options = SimpleNamespace(WELCOME_EMAIL_SENDER={"name": None, "email": ""})
        default = object()
        with (
            patch.object(emails, "settings", options),
            patch.object(emails, "FromAddress", default),
            patch.object(
                native,
                "select_prepared_welcome_sender",
                wraps=native.select_prepared_welcome_sender,
            ) as select,
        ):
            self.assertEqual(emails.welcome_sender_information(), (None, ""))
        select.assert_called_once_with(options, default)

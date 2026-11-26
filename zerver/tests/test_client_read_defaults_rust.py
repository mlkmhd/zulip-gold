from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.models import clients
from zerver.models.clients import Client


class ClientReadDefaultsRustTest(SimpleTestCase):
    def test_scoped_sender_default_matches_original_for_interactive_names(self) -> None:
        backend = clients._native_client_read_defaults
        witness = (
            patch.object(backend, "default_read_by_sender", wraps=backend.default_read_by_sender)
            if backend is not None
            else nullcontext()
        )
        names = (
            "ZulipAndroid",
            "ZulipIOS",
            "ZulipDesktop",
            "ZulipMobile",
            "ZulipElectron",
            "ZulipTerminal",
            "Snipe",
            "WEBSITE",
            "iOS",
            "Android",
            "test suite",
            "curl",
            "Website ",
            "نمونه",
        )
        with patch.object(clients, "_rust_client_defaults", None), witness as native:
            for enabled in (False, True, 17):
                with self.settings(TEST_SUITE=enabled):
                    for name in names:
                        client = Client(name=name)
                        with self.subTest(name=name, test_suite=enabled):
                            with patch.object(clients, "_native_client_read_defaults", None):
                                expected = client.default_read_by_sender()
                            self.assertIs(client.default_read_by_sender(), expected)
            if native is not None:
                self.assertEqual(native.call_count, 3 * (len(names) - 1) - 1)
                self.assertIn((("test suite", True), {}), native.call_args_list)

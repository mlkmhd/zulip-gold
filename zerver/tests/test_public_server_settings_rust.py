from unittest.mock import patch

from zerver.lib.test_classes import ZulipTestCase
from zerver.views import auth


class PublicServerSettingsRustTest(ZulipTestCase):
    def test_bytes_match_real_server_settings_response(self) -> None:
        backend = auth._public_server_settings_backend
        if backend is None or not hasattr(backend, "append_prepared_server_settings"):
            self.skipTest("No public server settings response ABI")
        route = "/api/v1/server_settings"
        for subdomain, user_agent in (("", ""), ("zulip", ""), ("zulip", "ZulipInvalid")):
            with (
                self.subTest(subdomain=subdomain, user_agent=user_agent),
                self.settings(ROOT_DOMAIN_LANDING_PAGE=False),
            ):
                with patch.object(auth, "_public_server_settings_backend", None):
                    expected = self.client_get(
                        route, subdomain=subdomain, HTTP_USER_AGENT=user_agent
                    )
                with patch.object(
                    backend,
                    "append_prepared_server_settings",
                    wraps=backend.append_prepared_server_settings,
                ) as witness:
                    actual = self.client_get(route, subdomain=subdomain, HTTP_USER_AGENT=user_agent)
                self.assert_json_success(expected)
                self.assert_json_success(actual)
                self.assertEqual(actual.content, expected.content)
                witness.assert_called_once()

    def test_native_only_uses_prepared_public_fields_in_order(self) -> None:
        backend = auth._public_server_settings_backend
        if backend is None or not hasattr(backend, "append_prepared_server_settings"):
            self.skipTest("No public server settings response ABI")
        result = {"zulip_version": "synthetic"}
        context = dict.fromkeys(auth._PUBLIC_SERVER_CONTEXT_FIELDS)
        context.update(
            realm_url="https://zulip.example.test",
            email_auth_enabled=False,
            realm_name="",
            external_authentication_methods=[],
            unselected="not a public response field",
        )
        with patch.object(auth, "_public_server_settings_backend", None):
            expected = auth._append_public_server_settings(dict(result), context)
        prepare = backend.append_prepared_server_settings
        actual = prepare(result, context, auth._PUBLIC_SERVER_CONTEXT_FIELDS)
        assert actual is not None
        self.assertEqual(list(actual), list(expected))
        self.assertEqual(actual, expected)
        self.assertIs(
            actual["external_authentication_methods"], context["external_authentication_methods"]
        )
        self.assertNotIn("unselected", actual)
        self.assertEqual(result, {"zulip_version": "synthetic"})
        del context["realm_url"]
        self.assertIsNone(prepare(result, context, auth._PUBLIC_SERVER_CONTEXT_FIELDS))
        with self.assertRaisesRegex(KeyError, "realm_url"):
            auth._append_public_server_settings(result, context)

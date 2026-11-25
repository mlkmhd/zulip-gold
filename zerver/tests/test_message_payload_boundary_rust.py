from collections.abc import Callable
from importlib import import_module
from itertools import product
from os import environ
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest import SkipTest
from unittest.mock import Mock, patch

from zerver.actions.realm_settings import do_set_realm_property
from zerver.lib import native_message_payload
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.test_classes import ZulipTestCase
from zerver.models.realms import MessageEditHistoryVisibilityPolicyEnum

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class MessagePayloadBoundaryRustTest(ZulipTestCase):
    def test_matching_wheel_only_enables_the_content_finalizer(self) -> None:
        if not supports_default_rust_backend() or environ.get("ZULIP_RUST_MESSAGE_PAYLOAD"):
            self.skipTest("No-flag startup is tested on Ubuntu 24.04 x86-64")
        try:
            backend = import_module("_zulip_backend")
        except ModuleNotFoundError:
            raise SkipTest("Rust extension is not installed") from None
        if getattr(backend, "MESSAGE_CONTENT_FINALIZER_API", None) != 1:
            self.skipTest("Wheel predates the default content finalizer contract")
        self.assertTrue(native_message_payload.has_content_finalizer())
        self.assertFalse(native_message_payload.is_enabled())

        author = self.example_user("cordelia")
        channel = self.make_stream("Native content fixture", is_web_public=True)
        self.subscribe(author, channel.name)
        message_id = self.send_stream_message(author, channel.name, "**Visible body**")
        with patch.object(
            backend,
            "finalize_message_payload_content",
            wraps=backend.finalize_message_payload_content,
        ) as finalizer:
            response = self.api_get(
                author, f"/api/v1/messages/{message_id}", {"apply_markdown": "true"}
            )
        self.assertIn(
            "<strong>Visible body</strong>",
            self.assert_json_success(response)["message"]["content"],
        )
        finalizer.assert_called_once()

    def test_single_message_bytes_and_native_input_allowlist(self) -> None:
        try:
            backend = import_module("_zulip_backend")
        except ModuleNotFoundError:
            raise SkipTest("Rust extension is not installed") from None
        real_native = getattr(backend, "finalize_message_payload_content", None)
        if real_native is None:
            raise SkipTest("Rust extension does not support content finalization")

        author = self.example_user("cordelia")
        reader = self.example_user("hamlet")
        channel = self.make_stream("Card boundary fixture", is_web_public=True)
        self.subscribe(author, channel.name)
        self.subscribe(reader, channel.name)
        first = self.send_stream_message(author, channel.name, "Reference", topic_name="")
        message_id = self.send_stream_message(author, channel.name, "**Older body**", topic_name="")
        target = f"#narrow/channel/{channel.id}-card-boundary-fixture/topic/near/{first}"
        current = (
            f'[Reference | safe reply]({author.realm.url}/{target} "zulip-message-reply")'
            "\n\n**Visible body**"
        )
        self.login_user(author)
        self.assert_json_success(
            self.client_patch(
                f"/json/messages/{message_id}",
                {"content": current, "topic": "Focused edit", "propagate_mode": "change_one"},
            )
        )

        legacy_renderings = set()

        def observed(
            payload: dict[str, object],
            apply_markdown: bool,
            allow_empty_topic: bool,
            fallback: str,
            reply_fields: Callable[..., dict[str, str]] | None,
        ) -> bool:
            allowed = {"edit_history"}
            edit_fields = {"prev_rendered_content_version"}
            if apply_markdown:
                allowed.add("rendered_content")
                self.assertIn(
                    "<strong>Visible body</strong>", cast(str, payload["rendered_content"])
                )
            if reply_fields is not None:
                allowed.add("match_content")
                edit_fields.add("prev_rendered_content")
                legacy_renderings.add(apply_markdown)
            if not allow_empty_topic:
                edit_fields.update(("prev_topic", "topic"))
            self.assertLessEqual(payload.keys(), allowed)
            history = cast(list[dict[str, object]], payload.get("edit_history", []))
            for row in history:
                self.assertLessEqual(row.keys(), edit_fields)
                self.assertIsNone(row.get("prev_rendered_content_version"))
                for topic_field in ("prev_topic", "topic"):
                    if topic_field in row:
                        self.assertEqual(row[topic_field], "")
            result = real_native(payload, apply_markdown, allow_empty_topic, fallback, reply_fields)
            self.assertIs(result, True)
            return True

        native = Mock(side_effect=observed)
        decline = Mock(return_value=False)

        def fetch(route: str, params: dict[str, str]) -> "TestHttpResponse":
            if route == "basic":
                return self.api_get(reader, f"/api/v1/messages/{message_id}", params)
            return self.client_get(f"/json/messages/{message_id}", params)

        cases = 0
        for policy, route in product(
            MessageEditHistoryVisibilityPolicyEnum,
            ("basic", "session", "spectator"),
        ):
            self.logout()
            do_set_realm_property(
                author.realm, "message_edit_history_visibility_policy", policy, acting_user=None
            )
            if route == "session":
                self.login_user(reader)
            for html, mobile in product((False, True), repeat=2):
                with self.subTest(policy=policy, route=route, html=html, mobile=mobile):
                    params = {
                        "apply_markdown": "true" if html else "false",
                        "allow_empty_topic_name": "true" if mobile else "false",
                    }
                    if mobile:
                        params["client"] = "ZulipMobile"
                    with (
                        patch.object(native_message_payload, "_backend", None),
                        patch.object(native_message_payload, "_default_content_backend", None),
                    ):
                        reference = fetch(route, params)
                    envelope = self.assert_json_success(reference)
                    card = envelope["message"]
                    self.assertEqual(envelope["raw_content"], current)
                    self.assertEqual(
                        card["content_type"], "text/html" if html else "text/x-markdown"
                    )
                    self.assertNotIn("sender_delivery_email", card)
                    self.assertNotIn("sender_email_address_visibility", card)
                    if policy is MessageEditHistoryVisibilityPolicyEnum.all:
                        self.assertEqual(card["edit_history"][0]["prev_content"], "**Older body**")
                    else:
                        self.assertNotIn("Older body", repr(card))
                    if policy is MessageEditHistoryVisibilityPolicyEnum.none:
                        self.assertNotIn("edit_history", card)
                    else:
                        self.assertEqual(
                            card["edit_history"][0]["prev_topic"], "" if mobile else "general chat"
                        )
                    for extension in (
                        SimpleNamespace(finalize_message_payload_content=native),
                        SimpleNamespace(),
                        SimpleNamespace(finalize_message_payload_content=decline),
                    ):
                        with patch.object(native_message_payload, "_backend", extension):
                            response = fetch(route, params)
                        self.assertEqual(response.status_code, reference.status_code)
                        self.assertEqual(response.content, reference.content)
                    cases += 1
        self.assertEqual(cases, 36)
        self.assertEqual(native.call_count, cases)
        self.assertEqual(decline.call_count, cases)
        self.assertEqual(legacy_renderings, {False, True})

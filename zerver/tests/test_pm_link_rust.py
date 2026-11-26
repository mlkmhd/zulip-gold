from types import SimpleNamespace
from typing import cast
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from zerver.lib import url_encoding
from zerver.models import Realm


class PreparedPMLinkRustTest(TestCase):
    def test_relative_absolute_and_group_link_only_use_prepared_recipient_ids(self) -> None:
        realm = cast("Realm", SimpleNamespace(url="https://example.test/مسیر"))
        message = {"id": -17, "display_recipient": [{"id": 5}, {"id": 1}, {"id": 5}]}
        self.assertEqual(
            url_encoding.pm_message_url(None, message), "#narrow/dm/1,5,5-group/near/-17"
        )
        self.assertEqual(
            url_encoding.pm_message_url(realm, message, conversation_link=True),
            "https://example.test/مسیر/#narrow/dm/1,5,5-group/with/-17",
        )

    def test_encoder_failure_precedes_reading_the_realm_url(self) -> None:
        realm = Mock()
        read_url = PropertyMock(side_effect=AssertionError("realm lookup out of order"))
        type(realm).url = read_url
        with self.assertRaises(AssertionError):
            url_encoding.pm_message_url(realm, {"id": 8, "display_recipient": []})
        read_url.assert_not_called()

    def test_native_receives_the_real_encoder_and_does_not_change_id_type(self) -> None:
        render = url_encoding._build_prepared_pm_message_url
        if render is None:
            self.skipTest("requires the current native PM permalink implementation")
        with (
            patch.object(url_encoding, "_build_prepared_pm_message_url", wraps=render) as native,
            patch.object(url_encoding, "encode_user_ids", return_value="slug") as encode,
        ):
            message = {"id": 1, "display_recipient": [{"id": 2**80}]}
            self.assertEqual(url_encoding.pm_message_url(None, message), "#narrow/dm/slug/near/1")
        native.assert_called_once()
        encode.assert_called_once_with([2**80])

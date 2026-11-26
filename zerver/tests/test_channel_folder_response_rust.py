from dataclasses import asdict
from unittest.mock import patch

from zerver.lib import channel_folders
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import ChannelFolder


class ChannelFolderResponseRustTest(ZulipTestCase):
    def test_real_member_folder_response_is_byte_identical(self) -> None:
        backend = channel_folders._channel_folder_response_backend
        if backend is None or not hasattr(backend, "assemble_channel_folder_payloads"):
            self.skipTest("No folder response ABI")
        user = self.example_user("hamlet")
        ChannelFolder.objects.create(realm=user.realm, name="فارسی", order=2)
        ChannelFolder.objects.create(realm=user.realm, name="Archived", is_archived=True)
        ChannelFolder.objects.create(realm=self.mit_user("sipbtest").realm, name="Another realm")
        self.login_user(user)
        for url in ("/json/channel_folders", "/json/channel_folders?include_archived=true"):
            with patch.object(channel_folders, "_channel_folder_response_backend", None):
                expected = self.client_get(url)
            with patch.object(
                backend,
                "assemble_channel_folder_payloads",
                wraps=backend.assemble_channel_folder_payloads,
            ) as witness:
                actual = self.client_get(url)
            self.assert_json_success(actual)
            self.assertEqual(actual.content, expected.content)
            witness.assert_called_once()
            self.assertNotContains(actual, "Another realm")

    def test_payloads_preserve_order_and_decline_non_plain_fields(self) -> None:
        backend = channel_folders._channel_folder_response_backend
        if backend is None or not hasattr(backend, "assemble_channel_folder_payloads"):
            self.skipTest("No folder response ABI")
        rows = [
            channel_folders.ChannelFolderData(
                id=2,
                name="🌿",
                description="فارسی",
                rendered_description="<p>فارسی</p>",
                order=1,
                creator_id=None,
                date_created=99,
                is_archived=False,
            ),
            channel_folders.ChannelFolderData(
                id=1,
                name="Earlier ID",
                description="",
                rendered_description="",
                order=1,
                creator_id=4,
                date_created=3,
                is_archived=True,
            ),
        ]
        expected = [asdict(row) for row in rows]
        actual = channel_folders.format_channel_folder_payloads(rows)
        self.assertEqual(actual, expected)
        self.assertEqual([list(row) for row in actual], [list(row) for row in expected])

        class DifferentName(str):
            __slots__ = ()

        rows[0].name = DifferentName("custom")
        self.assertIsNone(
            backend.assemble_channel_folder_payloads(rows, channel_folders.ChannelFolderData)
        )
        self.assertEqual(
            channel_folders.format_channel_folder_payloads(rows), [asdict(row) for row in rows]
        )

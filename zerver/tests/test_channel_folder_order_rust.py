from importlib import import_module
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
from django.db import DatabaseError
from django.db.models.signals import post_save
from typing_extensions import override

from zerver.actions.channel_folders import try_reorder_realm_channel_folders
from zerver.lib import native_channel_folder_order
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import queries_captured
from zerver.models import ChannelFolder
from zerver.models.users import active_user_ids

if TYPE_CHECKING:
    from django.db.backends.utils import CursorWrapper
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


def extension() -> ModuleType:
    try:
        module = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(module, "update_channel_folder_order", None)):
        raise SkipTest("Rust extension does not support batch channel folder order")
    return module


class ChannelFolderOrderRustTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.admin = self.example_user("iago")
        self.reader = self.example_user("hamlet")
        self.other_realm = self.mit_user("sipbtest").realm
        self.other = ChannelFolder.objects.create(realm=self.other_realm, name="Outside", order=-42)
        self.folders = [
            ChannelFolder.objects.create(
                realm=self.admin.realm,
                name=f"Folder {index} آزمایش",
                order=index + 25,
                is_archived=(index == 2),
            )
            for index in range(8)
        ]
        # A duplicate keeps its last position. An obsolete extra ID is
        # retained in the event, but is not a selected row to write.
        self.order = [
            self.folders[1].id,
            self.folders[-1].id,
            *(row.id for row in self.folders[:7]),
            0,
        ]
        assert {row.id for row in self.folders} == set(self.order) - {0}
        self.rows = ChannelFolder.objects.filter(realm=self.admin.realm)

    def positions(self) -> dict[int, int]:
        return dict(self.rows.values_list("id", "order"))

    def test_exact_basic_and_session_results_with_statement_count(self) -> None:
        backend = extension()
        observed = Mock(wraps=backend.update_channel_folder_order)
        native = SimpleNamespace(update_channel_folder_order=observed)
        self.login_user(self.admin)
        baseline_rows = self.positions()

        for session in (False, True):

            def run(
                mode: SimpleNamespace | None, active_session: bool
            ) -> tuple[bytes, bytes, bytes, list[dict[str, object]], int]:
                for id, position in baseline_rows.items():
                    self.rows.filter(id=id).update(order=position)
                form = {"order": orjson.dumps(self.order).decode()}
                with (
                    patch.object(native_channel_folder_order, "_backend", mode),
                    queries_captured(keep_cache_warm=True) as queries,
                    self.capture_send_event_calls(expected_num_events=1) as notifications,
                ):
                    response = (
                        self.client_patch("/json/channel_folders", form)
                        if active_session
                        else self.api_patch(self.admin, "/api/v1/channel_folders", form)
                    )
                self.assert_json_success(response)
                current_order = {id: index for index, id in enumerate(self.order) if id != 0}
                self.assertEqual(self.positions(), current_order)
                self.other.refresh_from_db()
                self.assertEqual(self.other.order, -42)
                ordinary = self.api_get(self.reader, "/api/v1/channel_folders")
                complete = self.client_get("/json/channel_folders", {"include_archived": "true"})
                active_ids = sorted(current_order, key=current_order.__getitem__)
                self.assertEqual(
                    [
                        folder["id"]
                        for folder in self.assert_json_success(complete)["channel_folders"]
                    ],
                    active_ids,
                )
                self.assertEqual(
                    [
                        folder["id"]
                        for folder in self.assert_json_success(ordinary)["channel_folders"]
                    ],
                    [id for id in active_ids if id != self.folders[2].id],
                )
                self.assertEqual(
                    notifications,
                    [
                        {
                            "event": {
                                "type": "channel_folder",
                                "op": "reorder",
                                "order": self.order,
                            },
                            "users": active_user_ids(self.admin.realm_id),
                        },
                    ],
                )
                writes = sum(
                    "UPDATE " in query.sql and "zerver_channelfolder" in query.sql
                    for query in queries
                )
                return (
                    response.content,
                    ordinary.content,
                    complete.content,
                    [dict(event) for event in notifications],
                    writes,
                )

            baseline = run(None, session)
            migrated = run(native, session)
            self.assertEqual(baseline[:-1], migrated[:-1])
            self.assertEqual((baseline[-1], migrated[-1]), (8, 1))
            self.assertEqual(run(SimpleNamespace(), session), baseline)
        self.assertEqual(observed.call_count, 2)
        for call in observed.call_args_list:
            self.assertEqual(call.args[1], self.admin.realm_id)
            self.assertEqual(dict(call.args[2]), self.positions())

    def test_django_rejects_before_the_native_write(self) -> None:
        backend = extension()
        witness = Mock(wraps=backend.update_channel_folder_order)
        native = SimpleNamespace(update_channel_folder_order=witness)
        baseline = self.positions()
        ordinary: TestHttpResponse
        invalid: TestHttpResponse
        with (
            patch.object(native_channel_folder_order, "_backend", native),
            self.capture_send_event_calls(expected_num_events=0),
        ):
            ordinary = self.api_patch(
                self.reader, "/api/v1/channel_folders", {"order": orjson.dumps(self.order).decode()}
            )
            invalid = self.api_patch(self.admin, "/api/v1/channel_folders", {"order": "[0]"})
        self.assert_json_error(ordinary, "Must be an organization administrator")
        self.assert_json_error(invalid, "Invalid order mapping.")
        witness.assert_not_called()
        self.assertEqual(self.positions(), baseline)

    def test_model_hooks_fall_back_and_failed_batch_does_not_replay(self) -> None:
        backend = extension()
        witness = Mock(wraps=backend.update_channel_folder_order)
        native = SimpleNamespace(update_channel_folder_order=witness)
        hook = Mock()
        post_save.connect(hook, sender=ChannelFolder, weak=False)
        try:
            with patch.object(native_channel_folder_order, "_backend", native):
                try_reorder_realm_channel_folders(self.admin.realm, self.order)
            self.assertEqual(hook.call_count, len(self.folders))
            witness.assert_not_called()
        finally:
            post_save.disconnect(hook, sender=ChannelFolder)

        baseline = self.positions()
        new_order = list(reversed(self.order))

        def incomplete(
            cursor: "CursorWrapper", realm_id: int, changes: list[tuple[int, int]]
        ) -> int:
            # Exercise a real partial UPDATE and the caller's transaction
            # rollback without a timing-dependent concurrent deletion.
            result: int = backend.update_channel_folder_order(cursor, realm_id, changes[:-1])
            self.assertEqual(result, len(changes) - 1)
            return result

        incomplete_update = Mock(wraps=incomplete)
        with (
            patch.object(
                native_channel_folder_order,
                "_backend",
                SimpleNamespace(update_channel_folder_order=incomplete_update),
            ),
            self.capture_send_event_calls(expected_num_events=0),
            self.assertRaisesMessage(
                DatabaseError, "Save with update_fields did not affect any rows."
            ),
        ):
            try_reorder_realm_channel_folders(self.admin.realm, new_order)
        incomplete_update.assert_called_once()
        self.assertEqual(self.positions(), baseline)

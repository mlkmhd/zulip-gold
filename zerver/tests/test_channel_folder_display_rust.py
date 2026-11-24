import os
from datetime import datetime, timezone
from importlib import import_module
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from zerver.lib import channel_folders as display
from zerver.models import ChannelFolder, Realm


def _row(row_id: int, key: int, *, archived: bool = False) -> ChannelFolder:
    return ChannelFolder(
        id=row_id,
        name=f"نمایش {row_id}",
        description="prepared",
        rendered_description="<p>prepared</p>",
        order=key,
        date_created=datetime(2025, 1, 2, tzinfo=timezone.utc),
        is_archived=archived,
    )


class ChannelFolderDisplayRustTest(TestCase):
    def test_stable_display_order(self) -> None:
        try:
            plan = import_module("_zulip_backend").plan_channel_folder_display_order
        except (ImportError, AttributeError):
            self.skipTest("Rust channel folder display ordering is not available")

        keys = [5, -1, 5, 1, -1, 0, 5]
        self.assertEqual(plan(keys), [1, 4, 5, 3, 0, 2, 6])
        self.assertEqual(keys, [5, -1, 5, 1, -1, 0, 5])
        self.assertEqual(plan([]), [])
        self.assertEqual(plan([-(2**63), 2**63 - 1]), [0, 1])
        self.assertEqual(plan([3, 3, 3]), [0, 1, 2])

        class Derived(int):
            pass

        for invalid in ((1,), [True], [2**63], [-(2**63) - 1], [Derived(2)], [3.0], [None]):
            self.assertIsNone(plan(invalid))

    def test_realm_read_preserves_archive_and_original_display_objects(self) -> None:
        realm = Realm()
        rows = [_row(30, -1), _row(10, 5), _row(20, -1), _row(11, 5, archived=True)]
        dtos = {row.id: display.get_channel_folder_data(row) for row in rows}
        queryset = MagicMock()
        queryset.exclude.return_value = rows[:3]
        queryset.__iter__.return_value = rows
        native = display._rust_plan_channel_folder_display_order
        enabled = os.environ.get("ZULIP_RUST_CHANNEL_FOLDER_DISPLAY") == "1"
        self.assertEqual(callable(native), enabled)

        with (
            patch.object(ChannelFolder.objects, "filter", return_value=queryset) as query,
            patch.object(display, "get_channel_folder_data", side_effect=lambda row: dtos[row.id]),
            patch.object(display, "_rust_plan_channel_folder_display_order", wraps=native)
            if enabled
            else patch.object(display, "_rust_plan_channel_folder_display_order", None) as dispatch,
        ):
            active = display.get_channel_folders_in_realm(realm)
            all_rows = display.get_channel_folders_in_realm(realm, include_archived=True)
            if dispatch is not None:
                self.assertEqual(dispatch.call_args_list, [call([-1, 5, -1]), call([-1, 5, -1, 5])])
        self.assertEqual(query.call_args_list, [call(realm=realm), call(realm=realm)])
        queryset.exclude.assert_called_once_with(is_archived=True)
        for actual, row_id in zip(active, [30, 20, 10], strict=True):
            self.assertIs(actual, dtos[row_id])
        for actual, row_id in zip(all_rows, [30, 20, 10, 11], strict=True):
            self.assertIs(actual, dtos[row_id])

    def test_spectator_read_preserves_selected_ids_and_sorts_by_id(self) -> None:
        realm = Realm()
        rows = [_row(30, -1), _row(10, 5), _row(20, -1), _row(11, 5)]
        eligible = MagicMock()
        eligible.values_list.return_value = [30, 20, 10, 11, 30]
        native = display._rust_plan_channel_folder_display_order
        enabled = os.environ.get("ZULIP_RUST_CHANNEL_FOLDER_DISPLAY") == "1"
        with (
            patch.object(
                display, "get_web_public_streams_queryset", return_value=eligible
            ) as select,
            patch.object(ChannelFolder.objects, "filter", return_value=rows) as query,
            patch.object(display, "_rust_plan_channel_folder_display_order", wraps=native)
            if enabled
            else patch.object(display, "_rust_plan_channel_folder_display_order", None) as dispatch,
        ):
            folders = display.get_channel_folders_for_spectators(realm)
            if dispatch is not None:
                dispatch.assert_called_once_with([30, 10, 20, 11])
        select.assert_called_once_with(realm)
        eligible.values_list.assert_called_once_with("folder_id", flat=True)
        query.assert_called_once_with(id__in={10, 11, 20, 30})
        self.assertEqual([folder.id for folder in folders], [10, 11, 20, 30])
        self.assertTrue(all(folder.rendered_description == "<p>prepared</p>" for folder in folders))

    def test_malformed_decline_and_large_keys_take_original_sort(self) -> None:
        realm = Realm()
        huge = 2**120
        rows = [_row(30, 1), _row(10, huge), _row(20, 1)]
        for invalid in (
            None,
            (0, 2, 1),
            [],
            [0, 0, 1],
            [2, 0, 1],
            [0, 1, 2],
            [False, 2, 1],
            [-1, 2, 1],
        ):
            with (
                patch.object(ChannelFolder.objects, "filter", return_value=rows),
                patch.object(
                    display, "_rust_plan_channel_folder_display_order", return_value=invalid
                ) as plan,
            ):
                self.assertEqual(
                    [folder.id for folder in display.get_channel_folders_in_realm(realm, True)],
                    [30, 20, 10],
                )
                plan.assert_called_once_with([1, huge, 1])

        with patch.object(ChannelFolder.objects, "filter", return_value=rows):
            self.assertEqual(
                [folder.id for folder in display.get_channel_folders_in_realm(realm, True)],
                [30, 20, 10],
            )

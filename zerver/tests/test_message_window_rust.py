from importlib import import_module
from random import Random
from types import ModuleType, SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson

from zerver.lib import native_message_window
from zerver.lib.narrow import post_process_limited_query
from zerver.lib.test_classes import ZulipTestCase


def extension() -> ModuleType:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not callable(getattr(backend, "plan_sorted_message_window", None)):
        raise SkipTest("Rust extension does not support message windows")
    return backend


class MessageWindowRustTest(ZulipTestCase):
    def test_exact_windows_on_sorted_visible_rows(self) -> None:
        backend = extension()
        generator = Random(7618)
        witness = Mock(wraps=backend.plan_sorted_message_window)
        with patch.object(
            native_message_window, "_backend", SimpleNamespace(plan_sorted_message_window=witness)
        ):
            for _case in range(900):
                message_ids = sorted(generator.choices(range(80), k=generator.randrange(100)))
                rows = [(message_id, index) for index, message_id in enumerate(message_ids)]
                args = (
                    rows,
                    generator.randrange(8),
                    generator.randrange(8),
                    generator.randrange(-3, 87),
                    bool(generator.randrange(2)),
                    bool(generator.randrange(2)),
                    generator.randrange(-3, 87),
                )
                reference = post_process_limited_query(*args)
                optimized = post_process_limited_query(*args, rows_sorted_by_id=True)
                self.assertEqual(optimized, reference)
                self.assertTrue(
                    all(new is old for new, old in zip(optimized.rows, reference.rows, strict=True))
                )
                planned = witness.call_args.args[0]
                cutoff = args[-1]
                if cutoff > 0:
                    self.assertTrue(all(row[0] >= cutoff for row in planned))
        self.assertEqual(witness.call_count, 900)

    def test_legacy_sequences_and_old_extensions_fall_back(self) -> None:
        backend = extension()
        rows = [[1], [2], [3]]
        baseline = post_process_limited_query(rows, 1, 1, 2, False, False, 2)
        decline = Mock(return_value=None)
        for candidate in (
            backend,
            SimpleNamespace(),
            SimpleNamespace(plan_sorted_message_window=decline),
        ):
            with patch.object(native_message_window, "_backend", candidate):
                self.assertEqual(
                    post_process_limited_query(
                        rows, 1, 1, 2, False, False, 2, rows_sorted_by_id=True
                    ),
                    baseline,
                )
        decline.assert_called_once()
        self.assertIsNone(backend.plan_sorted_message_window([(1,)], 2**100, 1, 1, False))

    def test_real_authenticated_window_preserves_cutoff_and_request_bytes(self) -> None:
        backend = extension()
        reader = self.example_user("hamlet")
        sender = self.example_user("cordelia")
        channel = "Sorted receipt-focused window"
        self.subscribe(reader, channel)
        self.subscribe(sender, channel)
        message_ids = [
            self.send_stream_message(sender, channel, f"Message {n}", topic_name="Window")
            for n in range(7)
        ]
        reader.realm.first_visible_message_id = message_ids[2]
        reader.realm.save(update_fields=["first_visible_message_id"])
        self.login_user(reader)
        native = Mock(wraps=backend.plan_sorted_message_window)
        for path in ("/json/messages", "/api/v1/messages"):
            for anchor in ("oldest", "newest", "first_unread", str(message_ids[4])):
                with self.subTest(path=path, anchor=anchor):
                    params = {
                        "anchor": anchor,
                        "num_before": 2,
                        "num_after": 2,
                        "narrow": orjson.dumps(
                            [{"operator": "channel", "operand": channel}]
                        ).decode(),
                    }
                    with (
                        patch.object(native_message_window, "_backend", None),
                        patch(
                            "zerver.lib.narrow.capture_message_fetch_query_for_testing"
                        ) as ref_query,
                    ):
                        if path == "/json/messages":
                            reference = self.client_get(path, params)
                        else:
                            reference = self.api_get(reader, path, params)
                    ref_query.assert_called_once()
                    reference_sql = ref_query.call_args.args[0].query.sql_with_params()
                    reference_payload = self.assert_json_success(reference)
                    self.assertTrue(
                        all(row["id"] >= message_ids[2] for row in reference_payload["messages"])
                    )
                    self.assertTrue(reference_payload["messages"])
                    override = (
                        patch.object(backend, "plan_sorted_message_window", native)
                        if native_message_window._backend is backend
                        else patch.object(
                            native_message_window,
                            "_backend",
                            SimpleNamespace(plan_sorted_message_window=native),
                        )
                    )
                    with (
                        override,
                        patch(
                            "zerver.lib.narrow.capture_message_fetch_query_for_testing"
                        ) as opt_query,
                    ):
                        if path == "/json/messages":
                            optimized = self.client_get(path, params)
                        else:
                            optimized = self.api_get(reader, path, params)
                    opt_query.assert_called_once()
                    self.assertEqual(
                        opt_query.call_args.args[0].query.sql_with_params(), reference_sql
                    )
                    self.assertEqual(optimized.content, reference.content)
                    native.assert_called()
                    visible = native.call_args.args[0]
                    self.assertTrue(all(row[0] >= message_ids[2] for row in visible))
        self.assertEqual(native.call_count, 8)

from importlib import import_module
from random import Random
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, override_settings

from zerver.lib import native_search_highlights
from zerver.lib.native_search_highlights import SearchHighlightRow
from zerver.lib.test_classes import ZulipTestCase
from zerver.views.message_fetch import get_search_fields_batch
from zerver.views.message_fetch import highlight_string as python_highlight


def extension() -> object:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    if not hasattr(backend, "assemble_search_highlights"):
        raise SkipTest("Rust extension does not support search highlights")
    return backend


class SearchHighlightProjectionTest(SimpleTestCase):
    def test_batch_matches_legacy_unicode_html_projection(self) -> None:
        generator = Random(28473)
        rows: list[SearchHighlightRow] = []
        for index in range(125):
            text = generator.choice(
                ["", "🙂 á and فارسی", '<a title="hello">hello</a>&amp;日本語', "<p></p>"]
            )
            positions = sorted(generator.randrange(len(text) + 1) for _ in range(8))
            locations = list(zip(positions[::2], positions[1::2], strict=True))
            spans = [(start, end - start) for start, end in locations]
            rows.append((index, text, text, spans, tuple(spans)))
        # Duplicate IDs retain their position but use the last result.
        rows.append((1, "last", "match", [(0, 4)], [(0, 5)]))
        with patch.object(native_search_highlights, "_backend", None):
            expected = get_search_fields_batch(iter(rows))
        native = extension().assemble_search_highlights
        witness = Mock(wraps=native)
        with (
            patch.object(
                native_search_highlights,
                "_backend",
                SimpleNamespace(assemble_search_highlights=witness),
            ),
            patch("zerver.views.message_fetch.highlight_string", side_effect=AssertionError),
        ):
            self.assertEqual(get_search_fields_batch(iter(rows)), expected)
            self.assertEqual(native([]), {})
        self.assertEqual(witness.call_count, 1)
        self.assertEqual(witness.call_args.args[0], rows)

    def test_declines_keep_legacy_slices_and_old_wheels(self) -> None:
        rows: list[SearchHighlightRow] = [
            (3, "café", "tea", [(1, 2)], ()),
            (5, "café", "茶", [(-2, 1)], [(0, 1)]),
        ]
        with patch.object(native_search_highlights, "_backend", None):
            expected = get_search_fields_batch(iter(rows))
        backend = extension()
        with patch.object(native_search_highlights, "_backend", backend):
            self.assertIsNone(native_search_highlights.assemble(rows))
            self.assertIsNone(native_search_highlights.assemble([(1, "x", "y", [(True, 1)], ())]))
            self.assertIsNone(native_search_highlights.assemble([(1, "x", "y", [(2**100, 1)], ())]))
            self.assertIsNone(native_search_highlights.assemble([(1, "\ud800", "y", [], ())]))
            with patch(
                "zerver.views.message_fetch.highlight_string", wraps=python_highlight
            ) as old:
                self.assertEqual(get_search_fields_batch(iter(rows)), expected)
            self.assertEqual(old.call_count, 4)
        with patch.object(native_search_highlights, "_backend", SimpleNamespace()):
            self.assertFalse(native_search_highlights.available())
            self.assertEqual(get_search_fields_batch(iter(rows)), expected)


class SearchHighlightEndpointTest(ZulipTestCase):
    def test_both_routes_send_only_selected_rows_in_one_native_batch(self) -> None:
        backend = extension()
        native = backend.assemble_search_highlights
        reader = self.example_user("cordelia")
        self.login_user(reader)
        message_id = self.send_stream_message(
            reader, "Verona", content="🙂 فارسی local lunch", topic_name="café lunch"
        )
        reply_url = f"{reader.realm.url}/#narrow/channel/999-verona/topic/near/{message_id}"
        second_id = self.send_stream_message(
            reader,
            "Verona",
            content=f'An earlier lunch\n\n[Othello | regular preview]({reply_url} "zulip-message-reply")',
            topic_name="lunch 🍱",
        )
        nonmatching_id = self.send_stream_message(reader, "Verona", content="No search term")
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE zerver_message SET search_tsvector = "
                "to_tsvector('zulip.english_us_search', subject || rendered_content)"
            )
            if settings.USING_PGROONGA:
                cursor.execute(
                    "UPDATE zerver_message SET search_pgroonga = "
                    "escape_html(subject) || ' ' || rendered_content"
                )

        encoded_narrow = orjson.dumps([{"operator": "search", "operand": "lunch"}]).decode()
        fetch_args = {
            "anchor": message_id,
            "num_before": 0,
            "num_after": 5,
            "narrow": encoded_narrow,
        }
        match_args: dict[str, str | int] = {
            "msg_ids": orjson.dumps([message_id, second_id, nonmatching_id]).decode(),
            "narrow": encoded_narrow,
        }

        def request(url: str, args: dict[str, str | int]) -> tuple[int, bytes]:
            response = (
                self.api_get(reader, url, args)
                if url.startswith("/api/v1")
                else self.client_get(url, args)
            )
            self.assert_json_success(response)
            return response.status_code, response.content

        for use_pgroonga in [False, True] if settings.USING_PGROONGA else [False]:
            with override_settings(USING_PGROONGA=use_pgroonga):
                for url, args in [
                    ("/messages", fetch_args),
                    ("/messages/matches_narrow", match_args),
                ]:
                    for prefix in ("/api/v1", "/json"):
                        full_url = prefix + url

                        with self.subTest(pgroonga=use_pgroonga, path=full_url):
                            with patch.object(native_search_highlights, "_backend", None):
                                expected = request(full_url, args)
                            messages = orjson.loads(expected[1])["messages"]
                            if url == "/messages":
                                match_content = next(
                                    row["match_content"]
                                    for row in messages
                                    if row["id"] == second_id
                                )
                            else:
                                match_content = messages[str(second_id)]["match_content"]
                            self.assertIn('<span class="highlight">lunch</span>', match_content)
                            if prefix == "/api/v1":
                                self.assertNotIn('class="message-reply-link"', match_content)
                                self.assertIn("Othello · regular preview", match_content)
                            else:
                                self.assertIn('class="message-reply-link"', match_content)
                            for mode, native_for_mode in (
                                ("native", Mock(wraps=native)),
                                ("declined", Mock(return_value=None)),
                                ("older", None),
                            ):
                                expected_ids = [message_id, second_id]
                                optional_backend = (
                                    SimpleNamespace(assemble_search_highlights=native_for_mode)
                                    if native_for_mode is not None
                                    else SimpleNamespace()
                                )
                                with (
                                    patch.object(
                                        native_search_highlights, "_backend", optional_backend
                                    ),
                                    patch(
                                        "zerver.views.message_fetch.highlight_string",
                                        wraps=python_highlight,
                                    ) as old,
                                ):
                                    self.assertEqual(request(full_url, args), expected)
                                if native_for_mode is not None:
                                    self.assertEqual(native_for_mode.call_count, 1)
                                    batch = native_for_mode.call_args.args[0]
                                    self.assertCountEqual([row[0] for row in batch], expected_ids)
                                    if mode == "native":
                                        self.assertIsNotNone(native(batch))
                                self.assertEqual(old.call_count, 0 if mode == "native" else 4)

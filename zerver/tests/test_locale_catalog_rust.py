import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.lib import i18n


class LocaleCatalogRustTest(TestCase):
    def test_ordered_display_projection(self) -> None:
        try:
            module = import_module("_zulip_backend")
            lookup = module.lookup_catalog_display_name
            project = module.project_catalog_language_codes
        except (ImportError, AttributeError):
            self.skipTest("Rust locale catalog projection is not available")

        first = "value:Language".removeprefix("value:")
        code = "value:fa-fake".removeprefix("value:")
        rows: list[dict[str, object]] = [
            {"code": "en", "locale": "en-US", "name": "English"},
            {"code": code, "locale": "Fa_IR 😀", "name": first, "percent_translated": 5},
            {"code": "different", "locale": code, "name": "Second", "percent_translated": 4},
            {"code": code, "locale": "fa", "name": "Third", "percent_translated": 50},
        ]
        self.assertIs(lookup(rows, code)[0], first)
        self.assertEqual(lookup(rows, "Fa_IR 😀"), (first,))
        self.assertEqual(lookup(rows, "FA_IR 😀"), (None,))
        self.assertEqual(project(rows, "en", 5), ["en", code, code])
        self.assertIs(project(rows, "en", 5)[1], code)
        self.assertEqual(project([], "en", 5), [])
        self.assertEqual(lookup([], "missing"), (None,))
        self.assertIsNone(lookup([{"code": "en", "name": "Missing locale"}], "en"))
        self.assertIsNone(lookup(rows, "\ud800"))
        self.assertIsNone(project(rows, "en", True))
        for percentage in (True, 5.0, 2**90, None):
            self.assertIsNone(
                project([{"code": "different", "percent_translated": percentage}], "en", 5)
            )

        class CustomKey(str):
            __slots__ = ()

        row_with_custom_key = dict(rows[0])
        row_with_custom_key[CustomKey("synthetic")] = 1
        self.assertIsNone(lookup([row_with_custom_key], "en"))
        self.assertIsNone(project([row_with_custom_key], "en", 5))


class LocaleCatalogIntegrationTest(SimpleTestCase):
    def test_real_wrappers_follow_flag_and_reuse_single_catalog(self) -> None:
        enabled = os.environ.get("ZULIP_RUST_LOCALE_CATALOG") == "1"
        lookup = i18n._lookup_catalog_display_name
        project = i18n._project_catalog_language_codes
        if enabled:
            self.assertIsNotNone(lookup)
            self.assertIsNotNone(project)
        else:
            self.assertIsNone(lookup)
            self.assertIsNone(project)
        lookup_spy = (
            patch.object(i18n, "_lookup_catalog_display_name", wraps=lookup)
            if enabled
            else patch.object(i18n, "_lookup_catalog_display_name", None)
        )
        project_spy = (
            patch.object(i18n, "_project_catalog_language_codes", wraps=project)
            if enabled
            else patch.object(i18n, "_project_catalog_language_codes", None)
        )
        first = "value:پارسی".removeprefix("value:")
        code = "value:fa-synthetic".removeprefix("value:")
        rows = [
            {"code": "en", "locale": "en-US", "name": "English"},
            {"code": code, "locale": "Fa_IR", "name": first, "percent_translated": 5},
            {"code": "fake", "locale": code, "name": "Later", "percent_translated": 4},
            {"code": code, "locale": "fa", "name": "Third", "percent_translated": 99},
        ]
        with (
            lookup_spy as called_lookup,
            project_spy as called_project,
            patch.object(i18n, "get_language_list", return_value=rows) as load,
        ):
            self.assertIs(i18n.get_language_name(code), first)
            load.assert_called_once_with()
            load.reset_mock()
            projected = i18n.get_available_language_codes()
            self.assertEqual(projected, ["en", code, code])
            self.assertIs(projected[1], code)
            self.assertIs(projected[2], code)
            load.assert_called_once_with()
            if enabled:
                assert called_lookup is not None
                assert called_project is not None
                called_lookup.assert_called_once_with(rows, code)
                called_project.assert_called_once_with(rows, "en", 5)

    def test_lookup_miss_and_declined_lookup_keep_logging(self) -> None:
        rows = [{"code": "fa", "locale": "fa_IR", "name": "Persian"}]
        with patch.object(i18n, "get_language_list", return_value=rows) as load:
            for missing in ("FA", "\ud800"):
                with self.assertLogs(level="ERROR") as captured:
                    self.assertEqual(i18n.get_language_name(missing), "Unknown")
                self.assertEqual(captured.output, [f"ERROR:root:Unknown language code '{missing}'"])
            self.assertEqual(load.call_count, 2)

    def test_decline_preserves_legacy_comparisons_and_errors(self) -> None:
        rows = [
            {"code": "en"},
            {"code": "float", "percent_translated": 5.0},
            {"code": "bool", "percent_translated": True},
            {"code": "huge", "percent_translated": 2**90},
        ]
        with patch.object(i18n, "get_language_list", return_value=rows) as load:
            self.assertEqual(i18n.get_available_language_codes(), ["en", "float", "huge"])
            load.assert_called_once_with()
        with patch.object(i18n, "get_language_list", return_value=[{"code": "en"}]) as load:
            with self.assertRaises(KeyError) as error:
                i18n.get_language_name("en")
            self.assertEqual(error.exception.args, ("locale",))
            load.assert_called_once_with()
        with patch.object(
            i18n, "get_language_list", return_value=[{"code": "other", "percent_translated": None}]
        ) as load:
            with self.assertRaises(TypeError):
                i18n.get_available_language_codes()
            load.assert_called_once_with()

import logging
import os
from importlib import import_module
from typing import SupportsIndex
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import override_settings
from typing_extensions import override

from zerver.lib import logging_util


class LogDisplayRustTest(TestCase):
    def test_native_prepared_values(self) -> None:
        try:
            module = import_module("_zulip_backend")
            prefix = module.log_level_display_prefix
            template = module.standard_log_display_format
        except (ImportError, AttributeError):
            self.skipTest("Rust log display helpers are not available")

        short = "آ🙂"
        self.assertIs(prefix(short), short)
        self.assertEqual(prefix("🙂éبیxyz"), "🙂éبی")

        class Derived(str):
            __slots__ = ()

        for invalid in (Derived("test"), "\ud800abcd", b"test", 0, None):
            self.assertIsNone(prefix(invalid))
        self.assertEqual(
            template(True),
            "%(asctime)s %(zulip_level_abbrev)-4s pid:%(process)d [%(zulip_origin)s] %(message)s",
        )
        self.assertEqual(
            template(False),
            "%(asctime)s %(zulip_level_abbrev)-4s [%(zulip_origin)s] %(message)s",
        )
        for invalid in (0, 1, "false", None):
            self.assertIsNone(template(invalid))

    def test_abbreviation_keeps_the_original_eager_slice_and_level_map(self) -> None:
        native = logging_util._rust_log_level_display_prefix
        if os.environ.get("ZULIP_RUST_LOG_DISPLAY") != "1":
            self.assertIsNone(native)
        spy = Mock(wraps=native) if native is not None else None
        short = "آ🙂"
        visits: list[str] = []

        class Label(str):
            __slots__ = ()

            @override
            def __getitem__(self, key: SupportsIndex | slice) -> str:
                if isinstance(key, slice) and (key.start, key.stop, key.step) == (None, 4, None):
                    visits.append(self)
                    return "usual fallback"
                return super().__getitem__(key)

        with patch.object(logging_util, "_rust_log_level_display_prefix", spy):
            self.assertIs(logging_util.abbrev_log_levelname(short), short)
            if spy is not None:
                spy.assert_called_once_with(short)
            self.assertEqual(logging_util.abbrev_log_levelname("\ud800آ🙂élong"), "\ud800آ🙂é")
            self.assertEqual(logging_util.abbrev_log_levelname(Label("custom")), "usual fallback")
            self.assertIs(
                logging_util.abbrev_log_levelname(Label("WARNING")),
                logging_util.log_level_abbrevs["WARNING"],
            )
            self.assertEqual(visits, ["custom", "WARNING"])

    def test_standard_formatter_only_changes_the_literal_template_computation(self) -> None:
        native = logging_util._rust_standard_log_display_format
        if os.environ.get("ZULIP_RUST_LOG_DISPLAY") != "1":
            self.assertIsNone(native)
        for include_pid in (False, True):
            spy = Mock(wraps=native) if native is not None else None
            record = logging.LogRecord(
                "synthetic", logging.ERROR, __file__, 1, "example %s", ("🙂",), None
            )
            record.process = 123
            with (
                self.subTest(include_pid=include_pid),
                override_settings(LOGGING_SHOW_PID=include_pid),
                patch.object(logging_util, "_rust_standard_log_display_format", spy),
                patch.object(logging_util, "find_log_origin", return_value="synthetic") as origin,
            ):
                formatter = logging_util.ZulipFormatter()
                if spy is not None:
                    spy.assert_called_once_with(include_pid)
                expected = (
                    " ERR  pid:123 [synthetic] example 🙂"
                    if include_pid
                    else " ERR  [synthetic] example 🙂"
                )
                self.assertTrue(formatter.format(record).endswith(expected))
                self.assertTrue(formatter.format(record).endswith(expected))
                origin.assert_called_once_with(record)

    def test_decline_and_python_truthiness(self) -> None:
        truthiness = Mock(return_value=True)

        class Setting:
            def __bool__(self) -> bool:
                return truthiness()

        with (
            override_settings(LOGGING_SHOW_PID=Setting()),
            patch.object(
                logging_util, "_rust_log_level_display_prefix", return_value=None
            ) as prefix,
            patch.object(
                logging_util, "_rust_standard_log_display_format", return_value=None
            ) as template,
        ):
            self.assertEqual(logging_util.abbrev_log_levelname("CUSTOM"), "CUST")
            formatter = logging_util.ZulipFormatter()
            self.assertEqual(
                formatter._fmt,
                "%(asctime)s %(zulip_level_abbrev)-4s pid:%(process)d [%(zulip_origin)s] %(message)s",
            )
        truthiness.assert_called_once_with()
        prefix.assert_called_once_with("CUSTOM")
        template.assert_called_once_with(True)

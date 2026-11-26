import os
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from io import StringIO
from typing import SupportsIndex
from unittest import TestCase
from unittest.mock import call, patch

from django.test import SimpleTestCase
from typing_extensions import override

from zerver.lib import timezone
from zerver.lib.native_backend import supports_default_rust_backend


class TimezoneAliasesRustTest(TestCase):
    def test_pure_alias_text_and_lookup(self) -> None:
        try:
            module = import_module("_zulip_backend")
            parse = module.parse_timezone_alias_lines
            lookup = module.lookup_timezone_alias
        except (ImportError, AttributeError):
            self.skipTest("Rust timezone alias helpers are not available")

        result = parse(
            [
                "# a comment",
                "Link A first",
                "\xa0Li\x1fB\u2000two\n",
                "linK C first",
                "Linked ignored aliases extras",
                "\u200bL ignored fields",
                "L D four",
                "İn ignored",
                " ",
            ]
        )
        self.assertEqual(list(result.items()), [("first", "C"), ("two", "B"), ("four", "D")])
        self.assertEqual(parse([]), {})
        for malformed in (["Link A"], ["L A a extra"], ["L \ud800 a"], [b"L A a"], ("L A a",)):
            self.assertIsNone(parse(malformed))

        key = "value:nonexistent".removeprefix("value:")
        name = "value:Canonical".removeprefix("value:")
        self.assertIs(lookup({"first": name}, "first"), name)
        self.assertIs(lookup({"first": name}, key), key)
        self.assertEqual(lookup({"FIRST": name}, "first"), "first")
        self.assertIsNone(lookup({"first": 2}, "first"))

        class Derived(str):
            __slots__ = ()

        self.assertIsNone(lookup({Derived("first"): name}, "first"))
        self.assertIsNone(lookup({"first": name}, Derived("first")))


class TimezoneAliasesIntegrationTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        timezone.get_canonical_timezone_map.cache_clear()
        self.addCleanup(timezone.get_canonical_timezone_map.cache_clear)

    def test_existing_stream_cache_and_lookup_selection(self) -> None:
        explicit_request = os.environ.get("ZULIP_RUST_TIMEZONE_ALIASES") == "1"
        try:
            backend = import_module("_zulip_backend")
        except ImportError:
            backend = None
        default_parse = (
            supports_default_rust_backend()
            and getattr(backend, "TIMEZONE_ALIAS_SOURCE_API", None) == 1
        )
        parse = timezone._parse_timezone_alias_lines
        lookup = timezone._lookup_timezone_alias
        self.assertEqual(
            parse is not None, default_parse or (explicit_request and backend is not None)
        )
        default_lookup = (
            supports_default_rust_backend()
            and getattr(backend, "TIMEZONE_ALIAS_LOOKUP_API", None) == 1
        )
        self.assertEqual(
            lookup is not None, default_lookup or (explicit_request and backend is not None)
        )
        parse_spy = (
            patch.object(timezone, "_parse_timezone_alias_lines", wraps=parse)
            if parse
            else (patch.object(timezone, "_parse_timezone_alias_lines", None))
        )
        lookup_spy = (
            patch.object(timezone, "_lookup_timezone_alias", wraps=lookup)
            if lookup
            else (patch.object(timezone, "_lookup_timezone_alias", None))
        )
        lines = ["# comment\n", "L Initial Alias\n", "\xa0linK Final Alias\n", "LI Two B"]
        key = "value:unknown key".removeprefix("value:")
        source = StringIO("".join(lines))
        with (
            parse_spy as native_parse,
            lookup_spy as native_lookup,
            patch.object(timezone, "get_tzdata_zi", return_value=source) as opener,
        ):
            aliases = timezone.get_canonical_timezone_map()
            self.assertEqual(list(aliases.items()), [("Alias", "Final"), ("B", "Two")])
            self.assertIs(timezone.get_canonical_timezone_map(), aliases)
            self.assertIs(timezone.canonicalize_timezone("Alias"), aliases["Alias"])
            self.assertIs(timezone.canonicalize_timezone(key), key)
            self.assertTrue(source.closed)
            opener.assert_called_once_with()
            if native_parse is not None:
                self.assertEqual(native_parse.call_args_list, [call([line]) for line in lines])
            if native_lookup is not None:
                self.assertEqual(
                    native_lookup.call_args_list, [call(aliases, "Alias"), call(aliases, key)]
                )
                self.assertIs(native_lookup.call_args.args[0], aliases)
                self.assertIs(native_lookup.call_args.args[1], key)

    def test_installed_tzdata_has_identical_aliases(self) -> None:
        parse = timezone._parse_timezone_alias_lines
        with patch.object(timezone, "_parse_timezone_alias_lines", None):
            expected = list(timezone.get_canonical_timezone_map().items())
        timezone.get_canonical_timezone_map.cache_clear()
        self.assertEqual(list(timezone.get_canonical_timezone_map().items()), expected)
        self.assertGreater(len(expected), 100)
        self.assertEqual(timezone.canonicalize_timezone("US/Pacific"), "America/Los_Angeles")
        self.assertIs(timezone._parse_timezone_alias_lines, parse)

    def test_early_malformed_line_precedes_later_stream_error(self) -> None:
        events: list[str] = []

        def lines() -> Iterator[str]:
            events.append("first")
            yield "L missing_alias"
            events.append("late")
            raise RuntimeError("later stream error")

        @contextmanager
        def stream() -> Iterator[Iterator[str]]:
            events.append("open")
            try:
                yield lines()
            finally:
                events.append("close")

        with patch.object(timezone, "get_tzdata_zi", side_effect=stream) as opener:
            with self.assertRaisesRegex(ValueError, "not enough values to unpack"):
                timezone.get_canonical_timezone_map()
            opener.assert_called_once_with()
        self.assertEqual(events, ["open", "first", "close"])
        self.assertEqual(timezone.get_canonical_timezone_map.cache_info().currsize, 0)

    def test_dynamically_split_line_and_key_decline_to_python(self) -> None:
        class SplitLine(str):
            __slots__ = ()

            @override
            def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
                return ["L", "Supplied", "Custom"]

        class CustomKey(str):
            __slots__ = ()

        @contextmanager
        def source() -> Iterator[Iterator[str]]:
            yield iter([SplitLine("not an alias")])

        with patch.object(timezone, "get_tzdata_zi", side_effect=source) as opener:
            aliases = timezone.get_canonical_timezone_map()
            self.assertEqual(aliases, {"Custom": "Supplied"})
            self.assertIs(timezone.canonicalize_timezone(CustomKey("Custom")), aliases["Custom"])
            missing = CustomKey("unknown")
            self.assertIs(timezone.canonicalize_timezone(missing), missing)
            opener.assert_called_once_with()

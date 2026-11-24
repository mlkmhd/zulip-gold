import os
from collections.abc import Iterator
from importlib import import_module
from typing import cast, override
from unittest import TestCase
from unittest.mock import call, patch

from zerver.webhooks.jotform import view


class JotformValueDisplayRustTest(TestCase):
    def test_native_preserves_exact_text_and_singleton_identity(self) -> None:
        try:
            join = import_module("_zulip_backend").join_jotform_display_values
        except (ImportError, AttributeError):
            self.skipTest("Rust Jotform display helper is not available")

        surrogate = chr(0xD800)
        self.assertIs(join([surrogate]), surrogate)
        text = f" label {len(surrogate)} فارسی "
        self.assertIs(join([text]), text)
        self.assertEqual(join([]), "")
        self.assertEqual(join(["", "x\x00", "متن\r\n😀 "]), " x\x00 متن\r\n😀 ")
        self.assertIsNone(join(["ordinary", surrogate]))

        class Derived(str):
            __slots__ = ()

        self.assertIsNone(join([Derived("plain")]))
        self.assertIsNone(join([1]))
        self.assertIsNone(join(("plain",)))
        self.assertIsNone(join({"form-key": "value"}))

    def test_real_helper_passes_only_ordered_display_values(self) -> None:
        native = view._rust_join_jotform_display_values
        enabled = os.environ.get("ZULIP_RUST_JOTFORM_VALUE_DISPLAY") == "1"
        self.assertEqual(callable(native), enabled)
        raw = f" فارسی {len(self.id())} "
        with (
            patch.object(view, "_rust_join_jotform_display_values", wraps=native)
            if enabled
            else patch.object(view, "_rust_join_jotform_display_values", None) as dispatch
        ):
            self.assertIs(view.format_value(raw), raw)
            self.assertEqual(view.format_value(["α", " فارسی "]), "α  فارسی ")
            self.assertEqual(view.format_value({"unused key": "z", "other key": "😀"}), "z 😀")
            if dispatch is not None:
                self.assertEqual(
                    dispatch.call_args_list, [call(["α", " فارسی "]), call(["z", "😀"])]
                )

    def test_subclass_dispatch_and_legacy_exceptions(self) -> None:
        class LegacyList(list[str]):
            @override
            def __iter__(self) -> Iterator[str]:
                return iter(("subclass", "value"))

        class LegacyDict(dict[str, str]):
            pass

        class LegacyStr(str):
            __slots__ = ()

        text = LegacyStr("submitted")
        with patch.object(view, "_rust_join_jotform_display_values", return_value=None) as native:
            self.assertEqual(view.format_value(LegacyList(["unused"])), "subclass value")
            self.assertEqual(
                view.format_value(LegacyDict({"ignored": "first", "key": "second"})), "first second"
            )
            self.assertIs(view.format_value(text), text)
            native.assert_not_called()

        with self.assertRaisesRegex(TypeError, "sequence item 1: expected str instance, int found"):
            view.format_value(cast("list[str]", ["fine", 1]))
        with self.assertRaisesRegex(AttributeError, "object has no attribute 'values'"):
            view.format_value(cast("dict[str, str]", ("text",)))

    def test_declining_and_incompatible_wheel_keeps_exact_fallback(self) -> None:
        value = chr(0xD800)
        for unsupported in (None, (), False):
            with patch.object(
                view, "_rust_join_jotform_display_values", return_value=unsupported
            ) as native:
                self.assertIs(view.format_value([value]), value)
                self.assertIs(view.format_value({"hidden": value}), value)
                self.assertEqual(view.format_value([value, " x "]), value + "  x ")
                native.assert_has_calls([call([value]), call([value]), call([value, " x "])])

from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from typing_extensions import override

from zerver.data_import import rocketchat


class RocketChatDisplayNameRustTest(TestCase):
    def test_literal_display_name_and_slice(self) -> None:
        try:
            module = import_module("_zulip_backend")
            clip = module.truncate_rocketchat_display_name
            prepend = module.prefix_rocketchat_team_display_name
        except (ImportError, AttributeError):
            self.skipTest("Rust Rocket.Chat display name helpers are not available")

        plain = f"synthetic {len(self.id())}"
        self.assertIs(clip(plain, len(plain), None), plain)
        self.assertEqual(clip("abcdefghij", 3, " [ID]"), "abcdefgh [ID]")
        self.assertEqual(clip("abcdef", -90, " [ID]"), " [ID]")
        self.assertEqual(clip("😀مرحبا긴", 5, "😀①"), "😀مر😀①")
        self.assertEqual(clip("abcd", 2, " [x]"), "ab [x]")
        self.assertEqual(prepend("مرحبا 🌐"), "[TEAM] مرحبا 🌐")
        self.assertEqual(prepend(""), "[TEAM] ")

        class Derived(str):
            __slots__ = ()

        for bad in (b"test", Derived("plain"), None):
            self.assertIsNone(prepend(bad))
            self.assertIsNone(clip(bad, 1, " [new]"))
        self.assertIsNone(clip("abc", 2**90, " [new]"))
        self.assertIsNone(clip("abc", -(2**63), " [new]"))
        self.assertIsNone(clip("abc", True, " [new]"))

        if getattr(module, "ROCKETCHAT_DISPLAY_NAME_API", None) == 1:
            special = "".join(map(chr, range(0xD800, 0xE000))) + "🙂"
            self.assertIs(clip(special, len(special), None), special)
            self.assertEqual(prepend(special), f"[TEAM] {special}")
            suffix = " \ud800[ID]"
            for length in (-4096, -13, -1, 1, 24, len(special) - 1):
                self.assertEqual(
                    clip(special, length, suffix), special[: length - len(suffix)] + suffix
                )

    def test_real_team_branch_and_optional_literal_clip(self) -> None:
        prefix = rocketchat._rust_prefix_rocketchat_team_display_name
        clip = rocketchat._rust_truncate_rocketchat_display_name
        enabled = prefix is not None
        self.assertEqual(clip is not None, enabled)
        chosen = "فارسی😀 " * 12
        prepared = f"[TEAM] {chosen}"
        with (
            patch.object(rocketchat, "_rust_prefix_rocketchat_team_display_name", wraps=prefix)
            if enabled
            else patch.object(rocketchat, "_rust_prefix_rocketchat_team_display_name", None) as add,
            patch.object(rocketchat, "_rust_truncate_rocketchat_display_name", wraps=clip)
            if enabled
            else patch.object(rocketchat, "_rust_truncate_rocketchat_display_name", None) as trim,
        ):
            self.assertEqual(
                rocketchat.get_stream_name({"teamMain": True, "name": chosen, "_id": 9}),
                prepared[:56] + " [9]",
            )
            if add is not None:
                add.assert_called_once_with(chosen)
            if trim is not None:
                trim.assert_called_once_with(prepared, 60, " [9]")
        literal = f"already chosen {len(self.id())}"
        self.assertIs(
            rocketchat.get_stream_name({"teamMain": False, "name": literal, "_id": 9}), literal
        )

    def test_existing_formatting_dispatch_and_no_new_id_work(self) -> None:
        evaluations: list[tuple[str, str]] = []

        class NameId(int):
            @override
            def __format__(self, format_spec: str) -> str:
                evaluations.append(("id", format_spec))
                return "tag"

        class LegacyName:
            @override
            def __format__(self, format_spec: str) -> str:
                evaluations.append(("name", format_spec))
                return "custom"

        normal = f"plain {len(self.id())}"
        self.assertIs(rocketchat.truncate_name(normal, NameId(9), len(normal)), normal)
        self.assertEqual(evaluations, [])
        self.assertEqual(rocketchat.truncate_name("abcdef", NameId(9), 2), "ab [tag]")
        self.assertEqual(evaluations, [("id", "")])
        evaluations.clear()
        with patch.object(rocketchat, "_rust_prefix_rocketchat_team_display_name") as prefix:
            self.assertEqual(
                rocketchat.get_stream_name(
                    {"teamMain": True, "name": LegacyName(), "_id": NameId(9)}
                ),
                "[TEAM] custom",
            )
            prefix.assert_not_called()
        self.assertEqual(evaluations, [("name", "")])

    def test_real_fallback_preserves_unusual_python_values(self) -> None:
        for unsupported in (None, (), False):
            with (
                patch.object(
                    rocketchat,
                    "_rust_prefix_rocketchat_team_display_name",
                    return_value=unsupported,
                ) as prefix,
                patch.object(
                    rocketchat, "_rust_truncate_rocketchat_display_name", return_value=unsupported
                ) as clip,
            ):
                self.assertEqual(
                    rocketchat.get_stream_name({"teamMain": True, "name": "نام", "_id": 9}),
                    "[TEAM] نام",
                )
                prefix.assert_called_once_with("نام")
                self.assertEqual(rocketchat.truncate_name("abcdef", 9, -1), "a [9]")
                clip.assert_called_once_with("abcdef", -1, " [9]")
        self.assertEqual(rocketchat.truncate_name("abcdef", 9, -(2**63)), " [9]")
        self.assertEqual(rocketchat.truncate_name("abcdef", 9, True), "abc [9]")
        self.assertEqual(rocketchat.truncate_name("\ud800abcdef", 9, 6), "\ud800a [9]")
        self.assertEqual(
            rocketchat.get_stream_name({"teamMain": True, "name": "\ud800", "_id": 9}),
            "[TEAM] \ud800",
        )

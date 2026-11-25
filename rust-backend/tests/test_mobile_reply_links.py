# ruff: noqa: INP001
import importlib.util
import os
import random
import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from zerver.lib.native_backend import supports_default_rust_backend


def load_reply_links(*, enabled: bool | None) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "zerver/lib/markdown/reply_links.py"
    spec = importlib.util.spec_from_file_location("reply_links", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    default_enabled = enabled is None and supports_default_rust_backend()
    flag = "ZULIP_RUST_PLAIN_REPLY_LINKS"
    with (
        patch.dict(os.environ),
        patch(
            "zerver.lib.native_backend.supports_default_rust_backend", return_value=default_enabled
        ),
    ):
        os.environ.pop(flag, None)
        if enabled is not None:
            os.environ[flag] = str(int(enabled))
        spec.loader.exec_module(module)
    return module


START = '<a class="message-reply-link" href="#narrow/channel/123-hello">'
SENDER = '<strong class="message-reply-sender" dir="auto">'
EXCERPT = '<span class="message-reply-excerpt" dir="auto">'
REPLY = f"{START}{SENDER}A &amp; B</strong>{EXCERPT}<mark>سلام</mark>\nworld</span></a>"
PLAIN = '<a href="#narrow/channel/123-hello">A &amp; B · <mark>سلام</mark>\nworld</a>'


class MobileReplyLinksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = load_reply_links(enabled=False)
        cls.native = load_reply_links(enabled=True)
        assert cls.native._rust_reply_links is not None

    def test_matching_wheel_uses_default_and_preserves_every_code_point(self) -> None:
        extension = self.native._rust_reply_links
        if getattr(extension, "MOBILE_REPLY_LINKS_API", None) != 1:
            self.skipTest("Matching reply link contract is not available")
        self.assertIsNone(self.reference._rust_reply_links)
        if supports_default_rust_backend():
            self.assertIs(load_reply_links(enabled=None)._rust_reply_links, extension)
        surrogates = "".join(map(chr, range(0xD800, 0xE000)))
        html = REPLY.replace("world", surrogates).replace("hello", "é\udfff")
        expected = self.reference.render_plain_reply_links(html)
        self.assertEqual(extension.render_mobile_plain_reply_links(html), expected)
        self.assertEqual(
            extension.mobile_plain_reply_link_fields({"content": html}, ("content",)),
            {"content": expected},
        )
        self.assertIs(extension.mobile_client_needs_plain_reply_links("\ud800"), False)

    def test_client_names_are_exact(self) -> None:
        names = ["ZulipFlutter", "ZulipMobile", "ZulipAndroid", "ZulipiOS"]
        for name in names:
            self.assertIs(self.native.needs_plain_reply_links(name), True)
        for other in [None, "", "ZulipDesktop", *[f"{name}/1.0" for name in names]]:
            self.assertIs(self.native.needs_plain_reply_links(other), False)
        for other_name in ["zulipios", "ZulipIOS", "ZulipFlutter ", "\ud800"]:
            self.assertEqual(
                self.native.needs_plain_reply_links(other_name),
                self.reference.needs_plain_reply_links(other_name),
            )

    def test_render_and_select_fields(self) -> None:
        input_html = f"<p>{REPLY}</p><p>no reply link</p>{REPLY}"
        expected = f"<p>{PLAIN}</p><p>no reply link</p>{PLAIN}"
        self.assertEqual(self.native.render_plain_reply_links(input_html), expected)
        marker_only = '<a href="/other" class="message-reply-link">unaltered</a>'
        data = {
            "previous": input_html,
            "content": marker_only,
            "without": "<p>plain</p>",
            "nil": None,
        }
        fields = ("missing", "content", "previous", "content", "without", "nil")
        rendered = self.native.plain_reply_link_fields(data, *fields)
        self.assertEqual(list(rendered.items()), [("content", marker_only), ("previous", expected)])
        self.assertIs(rendered["content"], marker_only)
        self.assertEqual(data["previous"], input_html)
        unchanged = data["without"]
        self.assertIs(self.native.render_plain_reply_links(unchanged), unchanged)

    def test_seeded_differential(self) -> None:
        rng = random.Random(92741)
        atoms = [
            START,
            SENDER,
            EXCERPT,
            "</span>",
            "</strong>",
            "</a>",
            "<p>",
            "</p>",
            REPLY,
            "&#91;",
            "🦊🇦🇿",
            "\ud800\udfff",
            "\r\n",
            "$1",
            "&amp;",
            "<mark>test</mark>",
            'title="label"',
        ]
        for _ in range(8000):
            html = "".join(rng.choices(atoms, k=rng.randrange(13)))
            self.assertEqual(
                self.native.render_plain_reply_links(html),
                self.reference.render_plain_reply_links(html),
            )
            data = {"content": html, "previous": REPLY, "nil": None}
            fields = ("content", "missing", "previous", "content", "nil")
            self.assertEqual(
                list(self.native.plain_reply_link_fields(data, *fields).items()),
                list(self.reference.plain_reply_link_fields(data, *fields).items()),
            )

    def test_unsupported_types_and_old_wheel(self) -> None:
        class RecordingMapping(Mapping[str, str]):
            def __init__(self) -> None:
                self.values_by_key = {"content": REPLY}
                self.lookups: list[str] = []

            def __getitem__(self, field: str) -> str:
                self.lookups.append(field)
                return self.values_by_key[field]

            def __len__(self) -> int:
                return len(self.values_by_key)

            def __iter__(self) -> Iterator[str]:
                return iter(self.values_by_key)

        class CustomName(str):
            __slots__ = ()

            def __hash__(self) -> int:
                return hash("ZulipiOS")

            def __eq__(self, other: object) -> bool:
                return other == "ZulipiOS"

        data = RecordingMapping()
        self.assertEqual(
            self.native.plain_reply_link_fields(data, "content", "missing", "content"),
            {"content": PLAIN},
        )
        self.assertEqual(data.lookups, ["content", "missing", "content"])
        self.assertTrue(self.native.needs_plain_reply_links(CustomName("not an exact str")))
        self.assertEqual(self.native.render_plain_reply_links(REPLY + "\ud800"), PLAIN + "\ud800")
        self.assertEqual(
            self.native.plain_reply_link_fields({"bad": REPLY + "\ud800"}, "bad"),
            {"bad": PLAIN + "\ud800"},
        )
        with patch.object(self.native, "_rust_reply_links", ModuleType("_zulip_backend")):
            self.assertTrue(self.native.needs_plain_reply_links("ZulipFlutter"))
            self.assertEqual(self.native.render_plain_reply_links(REPLY), PLAIN)
            self.assertEqual(
                self.native.plain_reply_link_fields({"content": REPLY}, "content"),
                {"content": PLAIN},
            )

    def test_public_helpers_use_native_exports_when_enabled(self) -> None:
        extension = self.native._rust_reply_links
        for export, call, args in (
            (
                "mobile_client_needs_plain_reply_links",
                self.native.needs_plain_reply_links,
                ("ZulipFlutter",),
            ),
            ("render_mobile_plain_reply_links", self.native.render_plain_reply_links, (REPLY,)),
            (
                "mobile_plain_reply_link_fields",
                self.native.plain_reply_link_fields,
                ({"content": REPLY}, "content"),
            ),
        ):
            with (
                self.subTest(export=export),
                patch.object(extension, export, wraps=getattr(extension, export)) as native,
            ):
                call(*args)
                native.assert_called_once()


if __name__ == "__main__":
    unittest.main()

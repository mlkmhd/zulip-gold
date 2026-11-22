# ruff: noqa: INP001
"""Compare actual preview processor methods without bootstrapping Django."""

import ast
import importlib.util
import os
import random
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal
from unittest.mock import patch
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class InlineMedia:
    type: Literal["image", "video"]
    media_url: str


@dataclass(frozen=True)
class DeferredMedia:
    type: Literal["file", "folder"]


def load_bridge(*, enabled: bool) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "native_preview_urls", ROOT / "zerver/lib/markdown/native_preview_urls.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_MARKDOWN_PREVIEW_URLS": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def load_processor(bridge: ModuleType) -> type:
    path = ROOT / "zerver/lib/markdown/__init__.py"
    source = ast.parse(path.read_text(), filename=str(path))
    processor = next(
        node
        for node in source.body
        if isinstance(node, ast.ClassDef) and node.name == "InlineInterestingLinkProcessor"
    )
    methods = {
        "get_actual_image_url",
        "corrected_image_source",
        "youtube_id",
        "youtube_image",
        "vimeo_id",
        "dropbox_media",
    }
    processor.bases = []
    processor.body = [
        node
        for node in processor.body
        if isinstance(node, ast.FunctionDef) and node.name in methods
    ]
    retain = {
        "_PREVIEW_URLSPLIT",
        "_PREVIEW_URLJOIN",
        "_PREVIEW_PARSE_QS",
        "_PREVIEW_PARSE_QSL",
        "_PREVIEW_URLENCODE",
        "_PREVIEW_RE_MATCH",
    }
    oracle = ast.Module(
        body=[
            node
            for node in source.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in retain
        ]
        + [processor],
        type_ignores=[],
    )
    namespace = {
        "native_preview_urls": bridge,
        "urlsplit": urlsplit,
        "urljoin": urljoin,
        "parse_qs": parse_qs,
        "parse_qsl": parse_qsl,
        "urlencode": urlencode,
        "re": re,
        "DropboxInlineMediaInfo": InlineMedia,
        "DropboxDeferredPreviewInfo": DeferredMedia,
    }
    # Compile only the selected methods from repository source, not fixture input.
    exec(compile(oracle, str(path), "exec"), namespace)  # noqa: S102
    return namespace[processor.name]


def make_processor(cls: type, *, enabled: bool = True) -> SimpleNamespace:
    processor = cls()
    processor.zmd = SimpleNamespace(image_preview_enabled=enabled)
    processor.calls = []

    def detect_image(url: str) -> bool:
        processor.calls.append(("image", url))
        return urlsplit(url).path.lower().endswith((".png", ".jpg"))

    def detect_video(path: str) -> bool:
        processor.calls.append(("video", path))
        return path.lower().endswith((".mp4", ".webm"))

    processor.is_image = detect_image
    processor.is_video = detect_video
    return processor


def outcome(processor: SimpleNamespace, method: str, url: str) -> tuple[object, list[object]]:
    processor.calls.clear()
    try:
        value = getattr(processor, method)(url)
    except ValueError as error:
        value = type(error), str(error)
    return value, processor.calls.copy()


class PreviewUrlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native_class = load_processor(cls.bridge)
        cls.reference_class = load_processor(load_bridge(enabled=False))

    def test_positive_and_disabled_paths(self) -> None:
        source = "https://www.dropbox.com/scl/fi/asset/image.JPG?q=h%C3%A9llo&q=last&raw=0#anchor"
        native = make_processor(self.native_class)
        self.assertEqual(
            native.dropbox_media(source),
            InlineMedia(
                "image",
                "https://www.dropbox.com/scl/fi/asset/image.JPG?q=last&raw=1#anchor",
            ),
        )
        self.assertEqual(native.calls, [("image", source), ("video", "/scl/fi/asset/image.JPG")])
        self.assertEqual(
            native.dropbox_media("https://dropbox.com/scl/fo/asset/folder"), DeferredMedia("folder")
        )
        self.assertEqual(
            native.dropbox_media("https://dropbox.com/scl/fi/asset/document.pdf?q=one"),
            DeferredMedia("file"),
        )
        self.assertEqual(
            native.youtube_image("https://WWW.YOUTUBE.COM:443/watch?v=&%76=f%2Di_rst&v=later"),
            "https://i.ytimg.com/vi/f-i_rst/mqdefault.jpg",
        )
        self.assertEqual(native.vimeo_id("https://vimeo.com/channels/short_clips/0037/?\n"), "0037")
        self.assertEqual(
            native.get_actual_image_url("https://github.com/org/repo/blob/main/photo.png?raw=1"),
            "https://raw.githubusercontent.com/org/repo/main/photo.png",
        )
        self.assertEqual(
            native.corrected_image_source(
                "HTTPS://FA.WIKIPEDIA.ORG/wiki/File:سلام.png?size=1#title"
            ),
            "https://FA.WIKIPEDIA.ORG/wiki/Special:FilePath/File:سلام.png?size=1#title",
        )
        self.assertEqual(
            native.corrected_image_source("https://linx.li/photo.png?size=1#title"),
            "https://linx.li/s/photo.png",
        )
        native = make_processor(self.native_class, enabled=False)
        self.assertIsNone(native.dropbox_media(source))
        self.assertIsNone(native.youtube_image("https://youtu.be/one"))
        self.assertIsNone(native.vimeo_id("https://vimeo.com/123"))
        self.assertEqual(native.calls, [])

    def test_fallbacks_old_wheel_and_standard_callbacks(self) -> None:
        native = make_processor(self.native_class)
        ref = make_processor(self.reference_class)
        cases = [
            ("youtube_id", "https://www.youtube.com/watch?v=%FF&v=other"),
            ("youtube_id", "https://[2001:db8::1]/hello"),
            ("youtube_id", "https://[2001:db8::1/hello"),
            ("youtube_id", "https://youtube.com/watch?v=\ud800"),
            ("vimeo_id", "https://vimeo.com/channels/ویدئو/۱۲۳"),
            ("get_actual_image_url", "https://github.com/org/repo/blob/main/../old.png;"),
            ("corrected_image_source", "https://é.wikipedia.org/wiki/File:photo.png"),
            ("dropbox_media", "https://dropbox.com/scl/fi/asset/image.png?q=%FF"),
        ]
        for method, url in cases:
            self.assertEqual(outcome(native, method, url), outcome(ref, method, url))
        with patch.object(self.bridge, "_backend", ModuleType("_zulip_backend")):
            self.assertEqual(native.youtube_id("https://youtu.be/first"), "first")
            self.assertEqual(
                native.dropbox_media("https://dropbox.com/scl/fi/asset/my.mp4"),
                InlineMedia("video", "https://dropbox.com/scl/fi/asset/my.mp4?raw=1"),
            )
        url = "https://dropbox.com/scl/fi/asset/image.png?q=one"
        with patch.dict(
            native.dropbox_media.__func__.__globals__, {"urlsplit": lambda _: urlsplit(url)}
        ):
            self.assertEqual(
                native.dropbox_media("https://example.test/image.png"),
                InlineMedia("image", "https://dropbox.com/scl/fi/asset/image.png?q=one&raw=1"),
            )

    def test_native_bridge_takes_all_five_dispatch_paths(self) -> None:
        native = make_processor(self.native_class)
        extension = self.bridge._backend
        value = extension.markdown_preview_url_value
        dropbox = extension.markdown_dropbox_preview
        with (
            patch.object(extension, "markdown_preview_url_value", wraps=value) as value_call,
            patch.object(extension, "markdown_dropbox_preview", wraps=dropbox) as dropbox_call,
        ):
            self.assertEqual(native.youtube_id("https://youtu.be/id"), "id")
            self.assertEqual(native.vimeo_id("https://vimeo.com/123"), "123")
            self.assertEqual(
                native.get_actual_image_url("https://github.com/o/r/blob/main/im.jpg"),
                "https://raw.githubusercontent.com/o/r/main/im.jpg",
            )
            self.assertEqual(
                native.corrected_image_source("https://linx.li/image.png"),
                "https://linx.li/s/image.png",
            )
            self.assertEqual(
                native.dropbox_media("https://dropbox.com/sc/folder"), DeferredMedia("folder")
            )
            self.assertEqual(value_call.call_count, 4)
            dropbox_call.assert_called_once()

    def test_seeded_differential(self) -> None:
        native = make_processor(self.native_class)
        ref = make_processor(self.reference_class)
        rng = random.Random(212_309)
        cases = {
            "youtube_id": (
                [
                    "youtube.com",
                    "www.YOUTUBE.COM:443",
                    "m.youtube.com",
                    "youtu.be",
                    "www.youtube-nocookie.com",
                    "example.test",
                    "éxample.test",
                ],
                [
                    "/watch",
                    "/watch_popup",
                    "/watch_videos",
                    "/v/test",
                    "/embed/short/test",
                    "/shorts/a-B",
                    "/name",
                    "/bad/path",
                    "/中文",
                ],
            ),
            "vimeo_id": (
                ["vimeo.com", "www.vimeo.com", "VIMEO.com", "example.test"],
                [
                    "/123",
                    "/001/?",
                    "/channels/123",
                    "/channels/two_1/234",
                    "/channels/موضوع/۱۲۳",
                    "/groups/group/videos/345",
                    "/group/1",
                ],
            ),
            "get_actual_image_url": (
                ["github.com", "www.github.com", "GITHUB.COM", "github.com:443", "example.test"],
                [
                    "/a/b/blob",
                    "/a/b/blob/",
                    "/a/b/blob/main/a.png",
                    "/a/b/blob/main/../b.png",
                    "/a/b/blob/main/./a.png",
                    "/a/b/blob/main/a.png;",
                    "/a/b/tree/main/a.png",
                    "/a/b/blob/main/a//b.png",
                    "/o/r/blob/main/测试.png",
                ],
            ),
            "corrected_image_source": (
                [
                    "fa.wikipedia.org",
                    "FA.WIKIPEDIA.ORG",
                    "wikipedia.org",
                    "linx.li",
                    "LINX.LI",
                    "example.test",
                ],
                [
                    "/wiki/File:abc.png",
                    "/wiki/File:سلام.png",
                    "/other.png",
                    "/wiki/File:%C3%A9.jpg",
                    "",
                ],
            ),
            "dropbox_media": (
                ["dropbox.com", "files.dropbox.com", "DROPBOX.com", "example.test"],
                [
                    "/sc/album",
                    "/scl/fo/album",
                    "/scl/fi/x/image.JPG",
                    "/scl/fi/x/doc.pdf",
                    "/scl/fi/x/movie.mp4",
                    "/scl/fi/x/video.webm",
                    "/static/image.png",
                    "/scl/fi/x/عکس.png",
                ],
            ),
        }
        forms = (
            "v=",
            "v=%FF",
            "%76=ok",
            "v=good-1",
            "v=bad+space",
            "v=a%2Fb",
            "video_ids=first%2Csecond",
            "video_ids=third,fourth",
            "v=سلام",
            "name=first",
            "name=last",
            "raw=0",
            "r%61w=2",
            "raw=",
            "=%21",
            "ab%2=~",
            "part=x%26y%3Dz",
            "unicode=سلام+%E9%9B%AA",
            "p=%00",
            "x=%F0%9F",
            "blank",
            "",
            "one=two=three",
        )
        for method, (hosts, paths) in cases.items():
            for _ in range(6000):
                prefix = rng.choice(("https://", "http://", "HTTPS://", "//", " \thttps://"))
                query = rng.choice(("", "?")) + "&".join(rng.choices(forms, k=rng.randrange(5)))
                if method == "vimeo_id":
                    query = rng.choice(("", "\n", "?foo=1", "#part"))
                fragment = rng.choice(("", "", "", "#", "#fragment", "#عنوان"))
                url = f"{prefix}{rng.choice(hosts)}{rng.choice(paths)}{query}{fragment}"
                self.assertEqual(
                    outcome(native, method, url), outcome(ref, method, url), (method, url)
                )


if __name__ == "__main__":
    unittest.main()

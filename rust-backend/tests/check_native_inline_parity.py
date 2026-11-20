# ruff: noqa: INP001
import argparse
import hashlib
import json
import os
import platform
import random
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast
from xml.etree.ElementTree import Element

if TYPE_CHECKING:
    from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor

Text = tuple[str, bool] | None


@dataclass
class Node:
    tag: str
    text: Text
    tail: Text
    children: list[int]


@dataclass
class Case:
    nodes: list[Node]
    ancestors: list[str] | None
    registry: str = "default"
    accepted: bool | None = None
    translate_emoticons: bool = False
    channels: bool = False
    empty_topic_only: bool = False
    mentions: bool = False
    mention_active: bool = True
    media: int = 0
    previous_image: bool = True
    cloud: bool = False


def build(case: Case) -> Element:
    from markdown.util import AtomicString

    def text(value: Text) -> str | None:
        if value is None:
            return None
        return AtomicString(value[0]) if value[1] else value[0]

    nodes = [Element(spec.tag, {"source": str(index)}) for index, spec in enumerate(case.nodes)]
    for node, spec in zip(nodes, case.nodes, strict=True):
        node.text = text(spec.text)
        node.tail = text(spec.tail)
        node[:] = [nodes[index] for index in spec.children]
    return nodes[0]


def snapshot(processor: "PythonInlineProcessor", root: Element) -> dict[str, object]:
    from zerver.lib.markdown.native_inline import RegistryInternals

    nodes = [root]
    indices = {root: 0}

    def index(node: Element) -> int:
        if node not in indices:
            indices[node] = len(nodes)
            nodes.append(node)
        return indices[node]

    def text(value: str | None) -> tuple[str | None, str]:
        return value, type(value).__name__

    stash = [
        (key, text(value) if isinstance(value, str) else index(value))
        for key, value in processor.stashed_nodes.items()
    ]
    parents = [(index(node), index(parent)) for node, parent in processor.parent_map.items()]
    tree = [
        (
            node.tag,
            text(node.text),
            text(node.tail),
            list(node.attrib.items()),
            [index(c) for c in node],
        )
        for node in nodes
    ]
    registry = cast(RegistryInternals, processor.inlinePatterns)
    return {
        "tree": tree,
        "stash": stash,
        "parents": parents,
        "ancestors": processor.ancestors,
        "registry": (list(registry._priority), registry._is_sorted),
        "html_stash": (
            processor.md.htmlStash.html_counter,
            processor.md.htmlStash.rawHtmlBlocks[:],
        ),
    }


def exercise(case: Case, *, native: bool) -> tuple[bool, dict[str, object]]:
    if not case.cloud:
        return _exercise(case, native=native)
    from unittest.mock import patch

    from django.test import override_settings

    from zerver.lib import upload
    from zerver.lib.upload.s3 import S3UploadBackend

    backend = object.__new__(S3UploadBackend)
    backend.public_upload_url_base = "https://cdn.example/public/"
    with (
        override_settings(LOCAL_UPLOADS_DIR=None, CAMO_URI="https://proxy.example/"),
        patch.object(upload, "_upload_backend", backend),
    ):
        return _exercise(case, native=native)


def _exercise(case: Case, *, native: bool) -> tuple[bool, dict[str, object]]:
    from markdown import util
    from markdown.treeprocessors import InlineProcessor as PythonInlineProcessor

    from zerver.lib.markdown import ZulipMarkdown, native_inline
    from zerver.lib.markdown.inline_stash import InlineProcessor

    md = ZulipMarkdown(linkifiers=[], linkifiers_key=0, email_gateway=False)
    md.zulip_db_data = None
    md.zulip_realm = None
    if case.translate_emoticons:
        from zerver.lib.markdown import DbData
        from zerver.lib.mention import MentionBackend, MentionData
        from zerver.lib.thumbnail import AttachmentData

        empty_mentions = MentionData.__new__(MentionData)
        empty_mentions.mention_backend = MentionBackend(1)
        empty_mentions.full_name_info = {}
        empty_mentions.user_id_info = {}
        empty_mentions.user_group_name_info = {}
        empty_mentions.user_group_members = {}
        md.zulip_db_data = DbData(
            mention_data=empty_mentions,
            realm_url="https://example.com",
            realm_alert_words_automaton=None,
            active_realm_emoji={},
            sent_by_bot=False,
            stream_names={},
            topic_info={},
            translate_emoticons=True,
            user_upload_previews=AttachmentData(audio_path_ids=set(), image_metadata={}),
        )
    if case.channels:
        from zerver.lib.markdown import DbData
        from zerver.lib.mention import ChannelInfo, ChannelTopicInfo, MentionBackend, MentionData
        from zerver.lib.thumbnail import AttachmentData
        from zerver.models import Realm
        from zerver.models.streams import StreamTopicsPolicyEnum

        backend = MentionBackend(1)
        backend.stream_cache = {
            "dev tools": ChannelInfo(42, 142, True, StreamTopicsPolicyEnum.inherit.value),
            "empty": ChannelInfo(17, 117, True, StreamTopicsPolicyEnum.empty_topic_only.value),
        }
        mentions = MentionData.__new__(MentionData)
        mentions.mention_backend = backend
        mentions.full_name_info = {}
        mentions.user_id_info = {}
        mentions.user_group_name_info = {}
        mentions.user_group_members = {}
        md.zulip_db_data = DbData(
            mention_data=mentions,
            realm_url="https://example.com",
            realm_alert_words_automaton=None,
            active_realm_emoji={},
            sent_by_bot=False,
            stream_names={"dev tools": 42, "empty": 17, "dev tools>t@1": 99, "empty>no": 123},
            topic_info={ChannelTopicInfo("dev tools", "t"): 123, ChannelTopicInfo("empty", ""): 77},
            translate_emoticons=case.translate_emoticons,
            user_upload_previews=AttachmentData(audio_path_ids=set(), image_metadata={}),
        )
        md.zulip_realm = Realm(
            topics_policy=(
                StreamTopicsPolicyEnum.empty_topic_only.value
                if case.empty_topic_only
                else StreamTopicsPolicyEnum.allow_empty_topic.value
            )
        )
    if case.mentions:
        from zerver.lib.markdown import DbData
        from zerver.lib.mention import FullNameInfo, MentionData
        from zerver.lib.thumbnail import AttachmentData
        from zerver.models import NamedUserGroup

        mentions = MentionData.__new__(MentionData)
        alice = FullNameInfo(42, "Alice <&> فارسی", case.mention_active)
        inactive = FullNameInfo(43, "Inactive", False)
        mentions.full_name_info = {"alice": alice, "inactive": inactive, "k": alice}
        mentions.user_id_info = {42: alice, 43: inactive}
        mentions.user_group_name_info = {
            "devs": NamedUserGroup(
                id=52, name="Devs <&>", is_system_group=False, deactivated=not case.mention_active
            ),
            "old": NamedUserGroup(id=53, name="Old", is_system_group=False, deactivated=True),
            "role:everyone": NamedUserGroup(
                id=54, name="role:everyone", is_system_group=True, deactivated=False
            ),
        }
        if md.zulip_db_data is None:
            md.zulip_db_data = DbData(
                mention_data=mentions,
                realm_url="https://example.com",
                realm_alert_words_automaton=None,
                active_realm_emoji={},
                sent_by_bot=False,
                stream_names={},
                topic_info={},
                translate_emoticons=case.translate_emoticons,
                user_upload_previews=AttachmentData(audio_path_ids=set(), image_metadata={}),
            )
        else:
            mentions.mention_backend = md.zulip_db_data.mention_data.mention_backend
            md.zulip_db_data.mention_data = mentions
    if case.media:
        from zerver.lib.markdown import DbData
        from zerver.lib.mention import MentionData
        from zerver.lib.thumbnail import AttachmentData, MarkdownImageMetadata
        from zerver.models import Realm
        from zerver.models.streams import StreamTopicsPolicyEnum

        if case.media == 1:
            md.zulip_db_data = None
        else:
            previews = AttachmentData(
                audio_path_ids=set() if case.media == 2 else {"1/voice", "1/both", "1/voice?x#y"},
                image_metadata={}
                if case.media == 2
                else {
                    path: MarkdownImageMetadata(None, False, 12, 24, "image/png")
                    for path in ["1/picture", "1/both", "1/picture?x#y"]
                },
            )
            if md.zulip_db_data is None:
                mentions = MentionData.__new__(MentionData)
                mentions.full_name_info = {}
                mentions.user_id_info = {}
                mentions.user_group_name_info = {}
                md.zulip_db_data = DbData(
                    mentions,
                    "https://example.com",
                    None,
                    {},
                    False,
                    {},
                    {},
                    case.translate_emoticons,
                    previews,
                )
            else:
                md.zulip_db_data.user_upload_previews = previews
        md.zulip_realm = Realm(
            string_id="zulip",
            topics_policy=(
                StreamTopicsPolicyEnum.empty_topic_only.value
                if case.empty_topic_only
                else StreamTopicsPolicyEnum.allow_empty_topic.value
            ),
        )
    from zerver.lib.markdown import MessageRenderingResult

    md.zulip_rendering_result = MessageRenderingResult(
        "sentinel",
        True,
        False,
        {1},
        {2},
        {"word"},
        {"url"},
        {3},
        ["a", "a"],
        {"spinner"},
        case.previous_image,
        True,
    )
    registry = cast(native_inline.RegistryInternals, md.inlinePatterns)
    if case.registry == "empty":
        md.inlinePatterns = util.Registry()
    elif case.registry == "unicode":
        for name in list(registry._data):
            if name != "unicodeemoji":
                md.inlinePatterns.deregister(name)
    elif case.registry == "formatting":
        for name in list(registry._data):
            if name not in {
                "backtick",
                "strong_em",
                "strong",
                "emphasis",
                "del",
                "not_strong",
                "nl",
            }:
                md.inlinePatterns.deregister(name)
    elif case.registry == "excluded":
        for name in ("backtick", "strong", "emphasis"):
            md.inlinePatterns[name].__dict__["ANCESTOR_EXCLUDES"] = ("P", "outer")
    processor = InlineProcessor(md) if native else PythonInlineProcessor(md)
    md.treeprocessors.register(processor, "inline", 25)
    root = build(case)
    ancestors = None if case.ancestors is None else case.ancestors[:]
    accepted = False
    if native:
        original_rendering = asdict(md.zulip_rendering_result)
        original_nodes = list(dict.fromkeys(root.iter()))
        original_values = [
            (node.tag, node.text, node.tail, list(node.attrib.items()), list(node))
            for node in original_nodes
        ]
        original_attributes = [node.attrib for node in original_nodes]
        original_registry = cast(native_inline.RegistryInternals, md.inlinePatterns)
        priorities = original_registry._priority[:]
        sorted_before = original_registry._is_sorted
        accepted = native_inline.try_run(
            cast(InlineProcessor, processor),
            root,
            ancestors,
            import_module("_zulip_backend").plan_markdown_native_inline,
            import_module("_zulip_backend").plan_markdown_native_inline_media,
        )
        assert set(original_nodes).issubset(root.iter()), "Plan replaced an original element"
        assert all(
            node.attrib is attrs
            for node, attrs in zip(original_nodes, original_attributes, strict=True)
        )
        if not accepted:
            assert asdict(md.zulip_rendering_result) == original_rendering, (
                "Rejected plan mutated metadata"
            )
            assert original_values == [
                (node.tag, node.text, node.tail, list(node.attrib.items()), list(node))
                for node in original_nodes
            ], "Rejected plan mutated the input graph"
            assert (priorities, sorted_before) == (
                original_registry._priority,
                original_registry._is_sorted,
            ), "Rejected plan mutated the registry"
            assert not hasattr(processor, "stashed_nodes"), "Rejected plan created processor state"
    error = None
    if not accepted:
        try:
            PythonInlineProcessor.run(processor, root, ancestors)
        except (RecursionError, ValueError, TypeError, IndexError, KeyError) as exc:
            error = (type(exc).__name__, str(exc))
    state = snapshot(processor, root)
    state["error"] = error
    state["input_ancestors"] = ancestors
    state["rendering"] = asdict(md.zulip_rendering_result)
    return accepted, state


def cases(seed: int, count: int, *, unicode_scalars: bool = False) -> Iterator[Case]:
    for registry in ("default", "formatting", "excluded", "empty"):
        for initial_text in (
            None,
            ("", False),
            ("plain فارسی", False),
            ("**bold**", False),
            ("**atomic**", True),
        ):
            yield Case(
                [Node("div", None, None, [1]), Node("p", initial_text, None, [])],
                None,
                registry,
                accepted=True,
            )
    for unsupported in (
        ":نام:",
        "$$x$$",
        "&copy;",
        "[link](https://[::1])",
        "\ud800",
    ):
        yield Case(
            [
                Node("div", None, None, [1]),
                Node("p", (f"**earlier** {unsupported}", False), None, []),
            ],
            None,
            accepted=False,
        )
    for value in (
        "**before** [**atomic**](https://example.com/سلام 'title') **after**",
        "[x](javascript:alert(1)) [yes](example.com)",
        "[Alice | hello](#narrow/dm/1/near/123 'zulip-message-reply')",
        "[x `code`](https://example.com/`path` 'title')",
        "[ ](#fragment '') [](/path) [x](file:///etc/passwd)",
        "[[nested]](https://example.com/(a(b)c)) [unclosed](x",
    ):
        yield Case(
            [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
            None,
            accepted=True,
        )
    for value in (":smile:", ":zulip:", ":unknown:", ":+1:", "***:smile:*** `:zulip:`"):
        yield Case(
            [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
            None,
            accepted=True,
        )
    for enabled in (False, True):
        for value in (
            ":) (: :( <3 :| :/ ;) :D :o :O :p :P",
            "**:)** `:)` ~~:D~~",
            "سلام :) ",
            "a:) :)a",
        ):
            yield Case(
                [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
                None,
                accepted=True,
                translate_emoticons=enabled,
            )
    for value in ("😀", "❤️", "1️⃣", "🇺🇸 👩🏽\u200d💻", "🇺🇸\u200d👩", "😀\u200d😀 😀", "↔ 1\u20e3"):
        yield Case(
            [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
            None,
            accepted=True,
        )
    for value in (
        "#**dev tools** #**hidden**",
        "#**dev tools>t** #**dev tools>unknown**",
        "#**empty>** #**empty>@۱٢3**",
        "#**empty>no** #**empty>no@0001**",
        "#**dev tools>t@1**",
        "`#**dev tools>t**` #**dev tools>t@1**",
        "[#**dev tools**] <#**dev tools**",
        "#**bad #**dev tools>t**",
    ):
        for empty_only in (False, True):
            yield Case(
                [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
                ["outer"],
                accepted=True,
                channels=True,
                empty_topic_only=empty_only,
            )
    for value in (
        "@**alice** @_**ALICE** @**inactive** @*devs* @_*devs* @*old*",
        "@**all** @_**everyone** @**topic** @**Everyone** @*missing*",
        "@**Alice <&> فارسی|42** @**|۴٢** @**|00042** @**|42\n**",
        "@**wrong|42** @**|999999999999999999999** @**K** @*role:everyone*",
        "`@**all** @*devs*` **@**alice****\n@**alice**",
        "@**alice** @**topic** @*devs* &copy;",
        "@**alice** @*devs* $$x$$",
        "@**alice** [x](https://[::1])",
    ):
        for active in (False, True):
            for channels in (False, True):
                yield Case(
                    [
                        Node("div", None, None, [1, 2]),
                        Node("p", (value, False), None, []),
                        Node("p", ("@**all** @*devs*", True), None, []),
                    ],
                    ["outer"],
                    mentions=True,
                    mention_active=active,
                    channels=channels,
                )
    for context in (1, 2, 3):
        for previous in (False, True):
            for value in (
                "![picture](/user_uploads/1/picture) ![voice](/user_uploads/1/voice)",
                "![picture](/user_uploads/1/picture?x#y) ![voice](/user_uploads/1/voice?x#y)",
                "![both](/user_uploads/1/both) ![both](/user_uploads/1/both)",
                "![unknown](/user_uploads/1/unknown) ![empty](/user_uploads/)",
                "@**alice** @*devs* #**dev tools>t** ![picture](/user_uploads/1/picture)",
                "**format** ![voice](https://other.example/a.mp3) ![picture](/user_uploads/1/picture '')",
            ):
                yield Case(
                    [
                        Node("div", None, None, [1, 2]),
                        Node("p", (value, False), None, []),
                        Node("code", (value, True), None, []),
                    ],
                    ["outer"],
                    accepted=True,
                    media=context,
                    previous_image=previous,
                    channels=True,
                    mentions=True,
                )
    for context in (1, 2, 3):
        for previous in (False, True):
            for value in (
                "![public](https://cdn.example/public/a.mp3) ![outside](https://cdn.example/public-evil/a.mp3)",
                "![picture](/user_uploads/1/picture) ![voice](/user_uploads/1/voice) @**alice** #**dev tools**",
                "![public](https://cdn.example/public/a.mp3) ![both](/user_uploads/1/both)",
            ):
                yield Case(
                    [
                        Node("div", None, None, [1, 2]),
                        Node("p", (value, False), None, []),
                        Node("code", (value, True), None, []),
                    ],
                    ["outer"],
                    accepted=True,
                    media=context,
                    previous_image=previous,
                    channels=True,
                    mentions=True,
                    cloud=True,
                )
    if unicode_scalars:
        for first in range(0, 0x110000, 256):
            value = " ".join(
                chr(point) for point in range(first, first + 256) if not 0xD800 <= point <= 0xDFFF
            )
            yield Case(
                [Node("div", None, None, [1]), Node("p", (value, False), None, [])],
                None,
                "unicode",
                accepted=True,
            )
    rng = random.Random(seed)
    pieces = [
        "***",
        "**",
        "*",
        "~~",
        "`",
        "\\",
        "فارسی",
        "abc",
        "[**label**](https://example.com/path 'title')",
        "[x](javascript:x)",
        "[Alice | hi](#narrow/dm/1/near/123 'zulip-message-reply')",
        "123",
        "\n",
        " ",
        "_",
        "^",
        "<&>",
        "\x02klzzwxh:bad",
        "\x02klzzwxh:9999\x03",
        "&copy;",
        ":smile:",
        ":zulip:",
        ":unknown:",
        ":+1:",
        ":)",
        "(:",
        ":D",
        "<3",
        ":/",
        ":نام:",
        "😀",
        "🏽",
        "🇺🇸",
        "👩",
        "\u200d",
        "\ufe0f",
        "\u20e3",
        "\U000e0067",
        "\U000e007f",
        "$$x$$",
        "[x](https://example.com)",
        "\ud800",
        "#**dev tools**",
        "#**dev tools>t**",
        "#**dev tools>t@1**",
        "#**empty>**",
        "#**empty>@۱٢3**",
        "#**empty>no**",
        "#**hidden>t**",
        "@**alice**",
        "@_**alice**",
        "@**inactive**",
        "@*devs*",
        "@_*old*",
        "@**all**",
        "@**topic**",
        "@**Everyone**",
        "@**missing**",
        "@*role:everyone*",
        "@**|۴٢**",
        "@**wrong|42**",
        "@**Alice <&> فارسی|42**",
        "@**|42\n**",
        "![picture](/user_uploads/1/picture)",
        "![voice](/user_uploads/1/voice)",
        "![both](/user_uploads/1/both)",
        "![unknown](/user_uploads/1/unknown)",
        "![query](/user_uploads/1/picture?x#y)",
        "![audio](https://other.example/a.mp3)",
        "![static](/static/sound.mp3)",
        "![public](https://cdn.example/public/audio.mp3)",
        "![outside](https://cdn.example/public-evil/audio.mp3)",
        "![empty](/user_uploads/)",
        "![malformed](http://[bad]/a)",
        "![unbounded](https://例.example/a)",
        "![",
    ]
    for _ in range(count):
        nodes = []
        for index in range(rng.randrange(1, 9)):
            values: list[Text] = []
            for _ in range(2):
                value = "".join(rng.choices(pieces, k=rng.randrange(10)))
                values.append(None if rng.randrange(5) == 0 else (value, rng.randrange(8) == 0))
            nodes.append(
                Node(
                    rng.choice(["div", "p", "P", "span", "code", "a", "strong", "İ", ""]),
                    values[0],
                    values[1],
                    [],
                )
            )
            if index:
                nodes[rng.randrange(index)].children.append(index)
                if rng.randrange(5) == 0:
                    nodes[rng.randrange(index)].children.append(index)
        yield Case(
            nodes,
            rng.choice([None, [], ["outer"], ["a", "P"]]),
            rng.choice(["default", "default", "formatting", "excluded", "empty", "unicode"]),
            translate_emoticons=bool(rng.randrange(2)),
            channels=bool(rng.randrange(2)),
            empty_topic_only=bool(rng.randrange(2)),
            mentions=bool(rng.randrange(2)),
            mention_active=bool(rng.randrange(2)),
            media=rng.randrange(4),
            previous_image=bool(rng.randrange(2)),
            cloud=bool(rng.randrange(2)),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare native inline graph state with Python-Markdown."
    )
    parser.add_argument("--cases", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=962817)
    parser.add_argument("--unicode-scalars", action="store_true")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-failures", type=int, default=10)
    args = parser.parse_args()
    if args.cases < 0 or args.max_failures < 1:
        parser.error("cases must be nonnegative and max-failures must be positive")
    if any(name.startswith("ZULIP_RUST_") and value == "1" for name, value in os.environ.items()):
        parser.error("unset Rust feature flags to isolate native inline execution")
    sys.path.insert(0, str(args.repository.resolve()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.test_settings")
    import django

    django.setup()
    native = import_module("_zulip_backend")
    if not hasattr(native, "plan_markdown_native_inline"):
        parser.error("the loaded Rust extension does not contain the native inline planner")
    extension = (
        import_module(f"{native.__name__}._zulip_backend")
        if hasattr(native, "__path__")
        else native
    )
    extension_path = extension.__file__
    if extension_path is None:
        parser.error("the loaded extension has no file path")
    files = [
        Path(native.__file__ or extension_path),
        Path(extension_path),
        Path(__file__),
        args.repository / "rust-backend/src/markdown_native_inline.rs",
        args.repository / "rust-backend/src/markdown_links.rs",
        args.repository / "rust-backend/src/markdown_link_syntax.rs",
        args.repository / "rust-backend/src/markdown_unicode_emoji.rs",
        args.repository / "rust-backend/src/markdown_emoji_data.rs",
        args.repository / "rust-backend/src/python.rs",
        args.repository / "rust-backend/src/lib.rs",
        args.repository / "rust-backend/tools/generate_markdown_emoji_data.py",
        args.repository / "zerver/lib/markdown/__init__.py",
        args.repository / "zerver/lib/markdown/native_inline.py",
        args.repository / "zerver/lib/markdown/native_channels.py",
        args.repository / "zerver/lib/markdown/native_mentions.py",
        args.repository / "zerver/lib/markdown/native_media.py",
        args.repository / "zerver/lib/markdown/inline_stash.py",
        args.repository / "rust-backend/src/markdown_media.rs",
        args.repository / "rust-backend/src/python_media.rs",
        args.repository / "zerver/lib/camo.py",
        args.repository / "zerver/lib/mime_types.py",
        args.repository / "zerver/lib/subdomains.py",
        args.repository / "zerver/lib/upload/__init__.py",
        args.repository / "zerver/lib/upload/s3.py",
        args.repository / "zerver/lib/thumbnail.py",
        args.repository / "rust-backend/src/markdown_mention_syntax.rs",
        args.repository / "rust-backend/src/markdown_inline_mentions.rs",
        args.repository / "rust-backend/src/markdown_mentions.rs",
        args.repository / "zerver/lib/markdown/channel_links.py",
        args.repository / "zerver/lib/mention.py",
        args.repository / "zerver/lib/url_encoding.py",
        args.repository / "rust-backend/src/markdown_channel_syntax.rs",
        args.repository / "rust-backend/src/markdown_channel_spans.rs",
        args.repository / "rust-backend/src/url_fragments.rs",
    ]
    report: dict[str, object] = {
        "python": platform.python_version(),
        "extension": extension_path,
        "seed": args.seed,
        "generated_cases": args.cases,
        "unicode_scalars": args.unicode_scalars,
        "hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }
    failures = []
    accepted = delegated = checked = 0
    for number, case in enumerate(
        cases(args.seed, args.cases, unicode_scalars=args.unicode_scalars)
    ):
        if number % 100 == 0:
            print(f"Checking case {number}", flush=True)
        try:
            _, expected = exercise(case, native=False)
            used_native, actual = exercise(case, native=True)
        except Exception as exc:
            failures.append(
                {
                    "case": number,
                    "input": asdict(case),
                    "error": (type(exc).__name__, str(exc)),
                }
            )
            print(f"Case {number} raised {type(exc).__name__}: {exc}", flush=True)
            break
        accepted += used_native
        delegated += not used_native
        checked += 1
        fields = [field for field in expected if expected[field] != actual[field]]
        if case.accepted is not None and used_native != case.accepted:
            fields.append("native_acceptance")
        if fields:
            failures.append(
                {
                    "case": number,
                    "input": asdict(case),
                    "native": used_native,
                    "expected_native": case.accepted,
                    "fields": fields,
                    "expected": expected,
                    "actual": actual,
                }
            )
            print(f"Mismatch at case {number}: {', '.join(fields)}", flush=True)
            if len(failures) >= args.max_failures:
                break
    report.update(checked=checked, accepted=accepted, delegated=delegated, failures=failures)
    if args.report:
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=True, default=sorted) + "\n"
        )
    print(
        f"{checked} checked; {accepted} native; {delegated} delegated; {len(failures)} mismatches",
        flush=True,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

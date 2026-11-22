# ruff: noqa: INP001
"""Exercise native settings and catalogs against the checked-out Python paths."""

import ast
import json
import os
import random
import unittest
from enum import Enum
from importlib import util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_bridge(*, enabled: bool) -> ModuleType:
    path = ROOT / "zerver/lib/native_settings_payload.py"
    spec = util.spec_from_file_location("native_settings_payload", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    with patch.dict(os.environ, {"ZULIP_RUST_SETTINGS_PAYLOAD": str(int(enabled))}):
        spec.loader.exec_module(module)
    return module


def find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    )


class Policy(Enum):
    always = 1
    except_followed = 2
    never = 3


def load_formatters(source: Path, bridge: ModuleType) -> ModuleType:
    module = ModuleType("settings_formatters")
    vars(module).update(
        os=SimpleNamespace(path=os.path),
        static_path=lambda value: value,
        native_settings_payload=bridge,
        format_emoji_choices=bridge.format_emoji_choices,
        ResolvedTopicNoticeAutoReadPolicyEnum=Policy,
        canonicalize_timezone=lambda tz: "America/Los_Angeles" if tz == "US/Pacific" else tz,
        want=lambda name: True,
    )

    users = source / "models/users.py"
    func = find_function(ast.parse(users.read_text(), filename=str(users)), "emojiset_choices")
    func.decorator_list = []
    exec(compile(ast.Module([func], []), str(users), "exec"), vars(module))  # noqa: S102
    sounds = source / "lib/sounds.py"
    func = find_function(
        ast.parse(sounds.read_text(), filename=str(sounds)), "get_available_notification_sounds"
    )
    exec(compile(ast.Module([func], []), str(sounds), "exec"), vars(module))  # noqa: S102

    events = source / "lib/events.py"
    state = find_function(
        ast.parse(events.read_text(), filename=str(events)), "fetch_initial_state_data"
    )
    sections = [
        node
        for node in state.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "want"
        and len(node.test.args) == 1
        and isinstance(node.test.args[0], ast.Constant)
        and node.test.args[0].value in ("realm_user_settings_defaults", "user_settings")
    ]
    assert len(sections) == 2
    wrapper = ast.parse("def get_settings(state, settings_user, realm):\n    return state")
    fn = wrapper.body[0]
    assert isinstance(fn, ast.FunctionDef)
    fn.body[0:0] = sections
    exec(compile(ast.fix_missing_locations(wrapper), str(events), "exec"), vars(module))  # noqa: S102
    return module


class SettingsSource:
    def __init__(self, fields: dict[str, object]) -> None:
        self.fields = fields
        self.reads: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.reads.append(name)
        if name not in self.fields:
            raise AttributeError(name)
        return self.fields[name]


class SettingsPayloadParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = ROOT / "zerver"
        baseline = Path(os.environ.get("ZULIP_SETTINGS_REFERENCE_DIR", source))
        cls.bridge = load_bridge(enabled=True)
        assert cls.bridge._backend is not None
        cls.native = load_formatters(source, cls.bridge)
        cls.reference = load_formatters(baseline, load_bridge(enabled=False))

    def test_seeded_register_personal_and_realm_defaults(self) -> None:
        rng = random.Random(185754)
        realm = object()
        for i in range(4000):
            fields: dict[str, object] = {
                "resolved_topic_notice_auto_read_policy": rng.randrange(1, 4),
                "timezone": rng.choice(("US/Pacific", "Asia/Tehran", "")),
                "unrelated_metadata": i,
            }
            fields.update({f"prop_{n}": rng.randrange(200) for n in range(rng.randrange(20))})
            schema = dict.fromkeys(
                key for key in fields if key not in ("timezone", "unrelated_metadata")
            )
            choices = (("google", "Google"), ("example", "نمونه"), ("text", "Plain text"))
            sounds = ["zulip.ogg", "zulip.hash.ogg", "ding.ogg", "نه.mp3", "سلام.ogg", ".ogg"]
            outputs = []
            traces = []
            for formatter in (self.native, self.reference):
                own_settings = SettingsSource(fields)
                realm_settings = SettingsSource(
                    {**fields, "resolved_topic_notice_auto_read_policy": 3}
                )
                formatter.UserProfile = SimpleNamespace(
                    property_types=schema,
                    EMOJISET_CHOICES=choices,
                    emojiset_choices=formatter.emojiset_choices,
                )
                formatter.RealmUserDefault = SimpleNamespace(
                    property_types=schema,
                    emojiset_choices=formatter.emojiset_choices,
                    objects=SimpleNamespace(
                        get=lambda realm_settings=realm_settings, **kwargs: realm_settings
                    ),
                )
                formatter.os.listdir = lambda path, sounds=sounds: sounds
                outputs.append(json.dumps(formatter.get_settings({}, own_settings, realm)))
                traces.extend([own_settings.reads, realm_settings.reads])
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(traces[:2], traces[2:])
            self.assertNotIn("unrelated_metadata", traces[0])
            native_projection = self.bridge._backend.snapshot_settings_properties(
                SettingsSource(fields), schema
            )
            self.assertIsNotNone(native_projection)
            self.assertEqual(list(native_projection), list(schema))

    def test_catalog_filtering_and_fallback(self) -> None:
        rng = random.Random(87192)
        stems = ["", "x", ".", "a.b", "نو", "😀", "z", "ᄀ", "ᄁ"]
        suffixes = ["", ".ogg", ".OGG", ".mp3", ".hash.ogg"]
        for _ in range(1200):
            filenames = [rng.choice(stems) + rng.choice(suffixes) for _ in range(rng.randrange(30))]
            self.reference.os.listdir = lambda path, filenames=filenames: filenames
            self.assertEqual(
                self.bridge.format_notification_sounds(filenames),
                self.reference.get_available_notification_sounds(),
            )
        self.assertIsNone(self.bridge.format_notification_sounds(["not/a-basename.ogg"]))
        self.assertIsNone(self.bridge.format_notification_sounds(["\ud800.ogg"]))
        self.assertIsNone(self.bridge.format_emoji_choices([("google", "Google")]))
        self.assertEqual(
            self.bridge.format_emoji_choices((("plain", "نمونه"),)),
            [{"key": "plain", "text": "نمونه"}],
        )
        with patch.object(self.bridge, "_backend", SimpleNamespace()):
            self.assertEqual(
                self.bridge.snapshot_properties(SettingsSource({"key": 3}), {"key": int}),
                {"key": 3},
            )
            self.assertIsNone(self.bridge.format_notification_sounds([]))
            self.assertIsNone(self.bridge.format_emoji_choices(()))

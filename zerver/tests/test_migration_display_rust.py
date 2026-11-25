from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib import migration_status


class MigrationDisplayRustTest(TestCase):
    def test_legacy_cosmetics_only(self) -> None:
        try:
            module = import_module("_zulip_backend")
            strip = module.strip_migration_display_sgr
            row = module.format_migration_display_row
        except (ImportError, AttributeError):
            self.skipTest("Rust migration display formatters are not available")

        unchanged = "plain\x1b[3K\x1b[۳m\n"
        self.assertIs(strip(unchanged), unchanged)
        self.assertEqual(strip("الف\x1b[0;31m漢\x1b[m]\x1b[;m🐚"), "الف漢]🐚")
        self.assertEqual(strip("\x1b[001\x1b[0m"), "\x1b[001")
        self.assertEqual(strip("\x1b[2J\x9b31m\x1b[1:m"), "\x1b[2J\x9b31m\x1b[1:m")
        self.assertEqual(row("X", "0001_title", 0), "[X] 0001_title")
        self.assertEqual(row("-", "0002_title", 1), "[-] 0002_title (1 squashed migrations)")
        self.assertEqual(row(" ", "موضوع", 19), "[ ] موضوع (19 squashed migrations)")

        class Derived(str):
            __slots__ = ()

        for value in (b"text", Derived("test")):
            self.assertIsNone(strip(value))
        for args in (
            ("!", "test", 0),
            ("X", Derived("test"), 0),
            ("-", "test", -1),
            ("X", "test", True),
            (" ", "test", 2**100),
        ):
            self.assertIsNone(row(*args))

        if getattr(module, "MIGRATION_DISPLAY_API", None) == 1:
            surrogates = "".join(map(chr, range(0xD800, 0xE000))) + "🙂"
            self.assertIs(strip(surrogates), surrogates)
            self.assertEqual(strip(surrogates + "\x1b[31m\ud800A"), surrogates + "\ud800A")
            self.assertEqual(row("X", surrogates, 1), f"[X] {surrogates} (1 squashed migrations)")

    def test_real_buffered_output_path_changes_only_sgr_cosmetics(self) -> None:
        native = migration_status._rust_strip_migration_display_sgr
        spy = Mock(wraps=native) if native is not None else None
        value = "الف\x1b[0;31m漢\x1b[m]\x1b[;m🐚\x1b[2J\x1b[۳m\n"

        def show_results(*args: object, **options: object) -> None:
            output = options["stdout"]
            assert isinstance(output, StringIO)
            output.write(value)

        with (
            patch("django.apps.apps.get_app_configs", return_value=[]),
            patch("django.core.management.call_command", side_effect=show_results) as command,
            patch.object(migration_status, "_rust_strip_migration_display_sgr", spy),
        ):
            self.assertEqual(
                migration_status.get_migration_status(app_label="prepared", database="fixture"),
                "الف漢]🐚\x1b[2J\x1b[۳m\n",
            )
        self.assertEqual(command.call_args.args, ("showmigrations", "--list", "prepared"))
        self.assertEqual(command.call_args.kwargs["database"], "fixture")
        if spy is not None:
            spy.assert_called_once_with(value)

    def test_graph_selection_remains_python_and_only_decorated_rows_are_formatted(self) -> None:
        native = migration_status._rust_format_migration_display_row
        spy = Mock(wraps=native) if native is not None else None
        recorded = ("prepared", "001_recorded")
        substituted = ("prepared", "002_جانشین")
        pending = ("prepared", "003_pending")
        stale = ("prepared", "004_stale")
        loader = Mock()
        loader.migrated_apps = {"prepared", "blank"}
        loader.graph.leaf_nodes.side_effect = lambda app: [pending] if app == "prepared" else []
        loader.graph.forwards_plan.return_value = [recorded, substituted, stale, pending, recorded]
        loader.graph.nodes = {
            recorded: SimpleNamespace(replaces=[]),
            substituted: SimpleNamespace(replaces=("a", "b")),
            pending: SimpleNamespace(replaces=[]),
        }
        loader.applied_migrations = {recorded: True, substituted: True}
        with (
            patch.object(migration_status, "MigrationLoader", return_value=loader),
            patch.object(migration_status, "MigrationRecorder") as recorder,
            patch.object(migration_status, "_rust_format_migration_display_row", spy),
        ):
            recorder.return_value.applied_migrations.return_value = {recorded: True}
            self.assertEqual(
                migration_status.parse_migration_status(stale_migrations=[stale]),
                {
                    "blank": ["(no migrations)"],
                    "prepared": [
                        "[X] 001_recorded",
                        "[-] 002_جانشین (2 squashed migrations)",
                        "[ ] 003_pending",
                    ],
                },
            )
        if spy is not None:
            self.assertEqual(
                [call.args for call in spy.call_args_list],
                [
                    ("X", "001_recorded", 0),
                    ("-", "002_جانشین (2 squashed migrations)", 0),
                    (" ", "003_pending", 0),
                ],
            )

    def test_decline_uses_legacy_regex_and_python_row_format(self) -> None:
        def show_results(*args: object, **options: object) -> None:
            output = options["stdout"]
            assert isinstance(output, StringIO)
            output.write("آ\ud800\x1b[31mB")

        with (
            patch("django.apps.apps.get_app_configs", return_value=[]),
            patch("django.core.management.call_command", side_effect=show_results),
            patch.object(
                migration_status, "_rust_strip_migration_display_sgr", return_value=None
            ) as strip,
            patch.object(
                migration_status, "_rust_format_migration_display_row", return_value=None
            ) as row,
        ):
            self.assertEqual(migration_status.get_migration_status(), "آ\ud800B")
            self.assertEqual(
                migration_status._format_migration_display_row("X", "\ud800"), "[X] \ud800"
            )
        strip.assert_called_once_with("آ\ud800\x1b[31mB")
        row.assert_called_once_with("X", "\ud800", 0)

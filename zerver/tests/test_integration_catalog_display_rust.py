import os
from importlib import import_module
from types import SimpleNamespace
from typing import cast
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import RequestFactory

from zerver.lib.integrations import Integration
from zerver.views import documentation


class IntegrationCatalogDisplayRustTest(TestCase):
    def test_pure_display_count_and_mapping(self) -> None:
        try:
            module = import_module("_zulip_backend")
            count = module.round_integration_display_count
            project = module.project_integration_display_categories
        except (ImportError, AttributeError):
            self.skipTest("Rust integration catalog display is not available")

        for given, expected in [(-10, -20), (0, -10), (1, 0), (10, 0), (11, 10), (99, 90)]:
            self.assertEqual(count(given), expected)
        self.assertIsNone(count(True))
        self.assertIsNone(count(-(2**63)))
        self.assertIsNone(count(-(2**63) + 1))
        self.assertIsNone(count(2**90))

        name = f"synthetic {len(self.id())}"
        latest_slug = f"last-{len(self.id())}"
        mapping = {"first": name, "synthetic": "فارسی🚀", latest_slug: name}
        ordered = ["فارسی🚀", name, name]
        result = project(mapping, ordered)
        self.assertEqual(
            result, [("synthetic", "فارسی🚀"), (latest_slug, name), (latest_slug, name)]
        )
        self.assertIs(result[1][0], latest_slug)
        self.assertIs(result[1][1], name)
        self.assertEqual(project(mapping, []), [])
        self.assertIsNone(project(mapping, ["missing"]))
        self.assertIsNone(project({"one": "\ud800"}, ["\ud800"]))
        self.assertIsNone(project(mapping, ("فارسی🚀",)))
        self.assertIsNone(project({"one": True}, []))

    def test_real_catalog_rounds_only_after_original_count(self) -> None:
        native = documentation._rust_round_integration_display_count
        enabled = os.environ.get("ZULIP_RUST_INTEGRATION_CATALOG_DISPLAY") == "1"
        self.assertEqual(callable(native), enabled)
        integrations = {str(i): Mock() for i in range(12)}
        for i, item in enumerate(integrations.values()):
            item.is_enabled_in_catalog.return_value = i != 1
        visible: list[Integration] = []
        with (
            patch.object(documentation, "INTEGRATIONS", integrations),
            patch.object(documentation, "CATEGORIES", {"sample": "متن"}),
            patch.object(documentation, "add_base_integrations_context", return_value={}),
            patch.object(
                documentation, "get_visible_integrations_for_category", return_value=visible
            ) as select,
            patch.object(documentation, "_rust_round_integration_display_count", wraps=native)
            if enabled
            else patch.object(
                documentation, "_rust_round_integration_display_count", None
            ) as round_count,
        ):
            context = documentation.add_catalog_integrations_context(
                RequestFactory().get("/integrations/"), "sample"
            )
            if round_count is not None:
                round_count.assert_called_once_with(11)
        for item in integrations.values():
            item.is_enabled_in_catalog.assert_called_once_with()
        select.assert_called_once_with("sample")
        self.assertEqual(context["integrations_count_display"], 10)
        self.assertEqual(list(context["categories_dict"].items()), [("sample", "متن")])
        self.assertIs(context["visible_integrations"], visible)

    def test_documentation_only_projection_preserves_route_default(self) -> None:
        native = documentation._rust_project_integration_display_categories
        enabled = os.environ.get("ZULIP_RUST_INTEGRATION_CATALOG_DISPLAY") == "1"
        self.assertEqual(callable(native), enabled)
        label = f" فارسی {len(self.id())}"
        final_slug = f"last-{len(label)}"
        names = [label, "other", label]
        mapping = {"first": label, "untouched": "other", final_slug: label}
        integration = cast("Integration", SimpleNamespace(categories=names))
        expected = [(final_slug, label), ("untouched", "other"), (final_slug, label)]
        with (
            patch.object(documentation, "CATEGORIES", mapping),
            patch.object(documentation, "add_base_integrations_context", return_value={}),
            patch.object(
                documentation,
                "build_integration_doc_html",
                return_value="inert rendered placeholder",
            ),
            patch.object(
                documentation, "_rust_project_integration_display_categories", wraps=native
            )
            if enabled
            else patch.object(
                documentation, "_rust_project_integration_display_categories", None
            ) as project,
        ):
            self.assertEqual(documentation.get_categories_for_integration(integration), expected)
            if project is not None:
                project.assert_not_called()
            context = documentation.add_doc_integrations_context(
                RequestFactory().get("/integrations/"), integration, "untouched"
            )
            if project is not None:
                project.assert_called_once_with(mapping, names)
        self.assertEqual(context["integration_categories"], expected)
        self.assertIs(context["integration_categories"][0][0], final_slug)
        self.assertIs(context["integration_categories"][0][1], label)
        self.assertEqual(context["return_category_slug"], "untouched")

    def test_malformed_declining_and_missing_category_keep_python(self) -> None:
        category = SimpleNamespace(categories=["selected"])
        integration = cast("Integration", category)
        for malformed in (None, (), [], [("wrong", "selected")], [("other", "selected")]):
            with (
                patch.object(
                    documentation, "CATEGORIES", {"actual": "selected", "other": "different"}
                ),
                patch.object(
                    documentation,
                    "_rust_project_integration_display_categories",
                    return_value=malformed,
                ),
                patch.object(
                    documentation, "_rust_round_integration_display_count", return_value=malformed
                ),
            ):
                self.assertEqual(
                    documentation.get_categories_for_integration(
                        integration, rust_for_display=True
                    ),
                    [("actual", "selected")],
                )
                self.assertEqual(documentation._round_integration_display_count(-10), -20)
        for supplied in ["missing", "\ud800"]:
            category.categories = [supplied]
            with patch.object(documentation, "CATEGORIES", {"surrogate": "\ud800"}):
                if supplied == "missing":
                    with self.assertRaises(AssertionError):
                        documentation.get_categories_for_integration(
                            integration, rust_for_display=True
                        )
                else:
                    self.assertEqual(
                        documentation.get_categories_for_integration(
                            integration, rust_for_display=True
                        ),
                        [("surrogate", supplied)],
                    )
        huge = 2**90
        self.assertEqual(
            documentation._round_integration_display_count(huge), ((huge - 1) // 10) * 10
        )

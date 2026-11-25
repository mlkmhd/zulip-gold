import os
from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest import TestCase
from unittest.mock import call, mock_open, patch

from django.http import HttpRequest
from lxml import html
from typing_extensions import override

from zerver.lib.native_backend import supports_default_rust_backend
from zerver.views import documentation


class DocumentationDisplayTitleRustTest(TestCase):
    def test_literal_host_chosen_title(self) -> None:
        try:
            module = import_module("_zulip_backend")
            article = module.format_documentation_display_title
            integration = module.format_integration_documentation_title
        except (ImportError, AttributeError):
            self.skipTest("Rust documentation title formatters are not available")

        base = f"سنت {len(self.id())} \0😀"
        self.assertIs(article(None, base, False), base)
        self.assertEqual(article("", base, True), " | " + base)
        self.assertEqual(article("strong title", "", True), "strong title | ")
        self.assertEqual(article("literal <&>", "display", True), "literal <&> | display")
        self.assertEqual(integration("فارسی"), "فارسی | Zulip integrations")
        self.assertEqual(integration(""), " | Zulip integrations")

        class Derived(str):
            __slots__ = ()

        for invalid in (None, b"title", 1, Derived("title")):
            self.assertIsNone(article(invalid, base, True))
            self.assertIsNone(integration(invalid))
        self.assertIsNone(article("other", base, 1))
        self.assertIsNone(article("other", Derived(base), False))

    def test_prepared_titles_preserve_all_surrogate_code_points(self) -> None:
        try:
            module = import_module("_zulip_backend")
        except ImportError:
            self.skipTest("Wheel is absent")
        if getattr(module, "DOCUMENTATION_DISPLAY_TITLE_API", None) != 1:
            self.skipTest("Wheel predates surrogate-safe documentation titles")
        article = module.format_documentation_display_title
        integration = module.format_integration_documentation_title
        base = "نمایش 😀"
        for code in range(0xD800, 0xE000):
            unusual = f"آ{chr(code)}\0😀"
            self.assertEqual(article(unusual, base, True), unusual + " | " + base)
            self.assertEqual(article(base, unusual, True), base + " | " + unusual)
            self.assertEqual(integration(unusual), unusual + " | Zulip integrations")

    def test_real_prepared_article_branch_does_not_infer_from_empty_title(self) -> None:
        native = documentation._rust_format_documentation_display_title
        enabled = os.environ.get("ZULIP_RUST_DOCUMENTATION_DISPLAY_TITLE") == "1" or (
            supports_default_rust_backend()
            and getattr(
                documentation._rust_documentation_display_title,
                "DOCUMENTATION_DISPLAY_TITLE_API",
                None,
            )
            == 1
        )
        self.assertEqual(callable(native), enabled)
        article = documentation.DocumentationArticle("/synthetic/article.md", 200, None, None)
        index = documentation.DocumentationArticle("/synthetic/index.md", 200, None, None)
        sidebar = documentation.DocumentationArticle("/synthetic/absent.md", 404, None, None)
        document = documentation.MarkdownDirectoryView()
        document.policies_view = True
        document.request = HttpRequest()

        def new_context() -> dict[str, Any]:
            return {}

        with (
            patch.object(documentation.ApiURLView, "get_context_data", side_effect=new_context),
            patch.object(document, "get_path", side_effect=[article, sidebar, index, sidebar]),
            patch.object(documentation, "open", mock_open(read_data="# \n"), create=True),
            patch.object(os.path, "exists", return_value=True),
            patch.object(documentation, "add_canonical_link_context"),
            patch.object(documentation, "add_api_url_context"),
            patch.object(documentation, "add_google_analytics_context"),
            patch.object(html, "fragment_fromstring", return_value=[]),
            patch.object(documentation, "sidebar_headings", return_value=[]),
            patch.object(documentation, "sidebar_links", return_value=[]),
            patch.object(documentation, "_rust_format_documentation_display_title", wraps=native)
            if enabled
            else patch.object(
                documentation, "_rust_format_documentation_display_title", None
            ) as render,
        ):
            self.assertEqual(
                document.get_context_data(article="article")["PAGE_TITLE"],
                " | Zulip terms and policies",
            )
            self.assertEqual(
                document.get_context_data(article="index")["PAGE_TITLE"], "Zulip terms and policies"
            )
            if render is not None:
                render.assert_called_once_with("", "Zulip terms and policies", True)

    def test_synthetic_integration_and_category_display_do_not_touch_root(self) -> None:
        native = documentation._rust_format_integration_documentation_title
        enabled = os.environ.get("ZULIP_RUST_DOCUMENTATION_DISPLAY_TITLE") == "1" or (
            supports_default_rust_backend()
            and getattr(
                documentation._rust_documentation_display_title,
                "DOCUMENTATION_DISPLAY_TITLE_API",
                None,
            )
            == 1
        )
        self.assertEqual(callable(native), enabled)
        with (
            patch.object(
                documentation, "INTEGRATIONS", {"synthetic": SimpleNamespace(display_name="فارسی")}
            ),
            patch.object(documentation, "CATEGORIES", {"demo": ""}),
            patch.object(
                documentation, "_rust_format_integration_documentation_title", wraps=native
            )
            if enabled
            else patch.object(
                documentation, "_rust_format_integration_documentation_title", None
            ) as render,
        ):
            for name, expected in (
                ("synthetic", "فارسی | Zulip integrations"),
                ("demo", " | Zulip integrations"),
                ("integrations", "Zulip integrations"),
            ):
                request = HttpRequest()
                request.path = "/" + name
                context: dict[str, Any] = {}
                documentation.add_integrations_open_graph_context(context, request)
                self.assertEqual(context["PAGE_TITLE"], expected)
            if render is not None:
                self.assertEqual(render.call_args_list, [call("فارسی"), call("")])

    def test_declining_and_noncanonical_values_keep_original_python(self) -> None:
        class Derived(str):
            __slots__ = ()

            @override
            def __format__(self, format_spec: str) -> str:
                return "custom"

        for unsupported in (None, (), False):
            with (
                patch.object(
                    documentation,
                    "_rust_format_documentation_display_title",
                    return_value=unsupported,
                ) as article,
                patch.object(
                    documentation,
                    "_rust_format_integration_documentation_title",
                    return_value=unsupported,
                ) as integration,
            ):
                self.assertEqual(
                    documentation._format_documentation_display_title("\ud800", "base", True),
                    "\ud800 | base",
                )
                self.assertEqual(
                    documentation._format_integration_documentation_title("\ud800"),
                    "\ud800 | Zulip integrations",
                )
                article.assert_called_once_with("\ud800", "base", True)
                integration.assert_called_once_with("\ud800")
        custom = Derived("synthetic")
        self.assertEqual(
            documentation._format_documentation_display_title(custom, "base", True), "custom | base"
        )
        self.assertEqual(
            documentation._format_integration_documentation_title(custom),
            "custom | Zulip integrations",
        )
        self.assertIs(
            documentation._format_documentation_display_title(custom, custom, False), custom
        )

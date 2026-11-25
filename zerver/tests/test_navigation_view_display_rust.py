import json
import os
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from django.test import RequestFactory
from typing_extensions import override

from zerver.lib import navigation_views as display
from zerver.lib.native_backend import supports_default_rust_backend
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import NavigationView, UserProfile
from zerver.views import navigation_views


class NavigationViewDisplayRustTest(TestCase):
    def test_opaque_ordered_projection(self) -> None:
        try:
            module = import_module("_zulip_backend")
            batch = module.render_navigation_view_display_rows
        except (ImportError, AttributeError):
            self.skipTest("Rust navigation view display projection is not available")

        fragment = "opaqueمتن\0😀\ud800"
        name = "plain display label"
        parts = (fragment, True, name)
        rendered = batch([parts])[0]
        keys = ["fragment", "is_pinned", "name"]
        self.assertEqual(list(rendered), keys)
        for value, original in zip(rendered.values(), parts, strict=True):
            self.assertIs(value, original)
        first = ("a", False, None)
        result = batch([first, parts, parts])
        self.assertEqual(result, [dict(zip(keys, first, strict=True)), rendered, rendered])
        self.assertIsNot(result[1], result[2])
        self.assertEqual(batch([]), [])

        class Derived(str):
            __slots__ = ()

        for invalid in (
            list(parts),
            parts[:2],
            (*parts, None),
            (Derived(fragment), False, None),
            (fragment, 1, name),
            (fragment, None, name),
            (fragment, False, Derived(name)),
            (fragment, True, 5),
        ):
            self.assertIsNone(batch([invalid]))
            self.assertIsNone(batch([parts, invalid]))
        self.assertIsNone(batch((parts,)))

    @override
    def setUp(self) -> None:
        self.request = RequestFactory().get("/api/v1/navigation_views")
        self.user = UserProfile()
        self.rows = [
            NavigationView(fragment="/opaque /%2Fمتن", is_pinned=False, name=" عنوان 😀 "),
            NavigationView(fragment="inbox", is_pinned=True, name=None),
            NavigationView(fragment="/opaque /%2Fمتن", is_pinned=False, name=" عنوان 😀 "),
        ]
        self.parts = [(row.fragment, row.is_pinned, row.name) for row in self.rows]
        self.expected = [display.get_navigation_view_dict(row) for row in self.rows]

    def test_real_get_and_unchanged_bootstrap_display(self) -> None:
        native = display._render_navigation_view_display_rows
        enabled = os.environ.get("ZULIP_RUST_NAVIGATION_VIEW_DISPLAY") == "1" or (
            supports_default_rust_backend()
            and getattr(display._rust_navigation_view_display, "NAVIGATION_VIEW_DISPLAY_API", None)
            == 1
        )
        self.assertEqual(callable(native), enabled)
        with (
            patch.object(NavigationView.objects, "filter", return_value=self.rows) as query,
            patch.object(display, "_render_navigation_view_display_rows", wraps=native)
            if enabled
            else patch.object(display, "_render_navigation_view_display_rows", None) as dispatch,
        ):
            self.assertEqual(display.get_navigation_views_for_user(self.user), self.expected)
            payload = navigation_views.get_navigation_views(self.request, self.user)
            if dispatch is not None:
                dispatch.assert_called_once_with(self.parts)
        self.assertEqual(payload.status_code, 200)
        self.assertEqual(json.loads(payload.content)["navigation_views"], self.expected)
        self.assertEqual(query.call_count, 2)
        for args, kwargs in query.call_args_list:
            self.assertEqual(args, ())
            self.assertEqual(kwargs, {"user": self.user})

    def test_real_get_decline_and_malformed_preserve_exact_original(self) -> None:
        for result in (None, (), [], [0, 0, 0]):
            with (
                patch.object(NavigationView.objects, "filter", return_value=iter(self.rows)),
                patch.object(
                    display, "_render_navigation_view_display_rows", return_value=result
                ) as native,
            ):
                payload = navigation_views.get_navigation_views(self.request, self.user)
                native.assert_called_once_with(self.parts)
            actual = json.loads(payload.content)["navigation_views"]
            self.assertEqual(actual, self.expected)
            self.assertEqual([list(row) for row in actual], [list(row) for row in self.expected])

    def test_unsupported_values_and_noncanonical_rows_keep_original(self) -> None:
        class Derived(str):
            __slots__ = ()

        fragment = Derived("opaque source")
        special = NavigationView(fragment=fragment, is_pinned=True, name=None)
        with (
            patch.object(NavigationView.objects, "filter", return_value=[special]),
            patch.object(
                display, "_render_navigation_view_display_rows", return_value=None
            ) as native,
        ):
            result = display.get_navigation_views_for_user(self.user, rust_display_for_get=True)
            native.assert_called_once_with([(fragment, True, None)])
        self.assertIs(result[0]["fragment"], fragment)

        equivalent = NavigationView(fragment="prepared", is_pinned=False, name="")
        with (
            patch.object(NavigationView.objects, "filter", return_value=[object()]),
            patch.object(
                display,
                "get_navigation_view_dict",
                return_value=display.get_navigation_view_dict(equivalent),
            ),
            patch.object(display, "_render_navigation_view_display_rows") as native,
        ):
            actual = display.get_navigation_views_for_user(self.user, rust_display_for_get=True)
        native.assert_not_called()
        self.assertEqual(actual, [{"fragment": "prepared", "is_pinned": False, "name": ""}])


class NavigationViewNativeGetIsolationTest(ZulipTestCase):
    def test_matching_wheel_renders_only_the_current_users_views(self) -> None:
        if not supports_default_rust_backend() or os.environ.get(
            "ZULIP_RUST_NAVIGATION_VIEW_DISPLAY"
        ):
            self.skipTest("No-flag startup is tested on Ubuntu 24.04 x86-64")
        backend = display._rust_navigation_view_display
        if getattr(backend, "NAVIGATION_VIEW_DISPLAY_API", None) != 1:
            self.skipTest("Wheel predates the default navigation-view contract")

        reader = self.example_user("hamlet")
        other = self.example_user("cordelia")
        own = NavigationView.objects.create(
            user=reader, fragment="narrow/is/mentioned", is_pinned=False, name="نمای من"
        )
        NavigationView.objects.create(user=other, fragment="inbox", is_pinned=True)
        native = display._render_navigation_view_display_rows
        assert native is not None
        self.login_user(reader)
        with patch.object(display, "_render_navigation_view_display_rows", wraps=native) as render:
            basic = self.api_get(reader, "/api/v1/navigation_views")
            web = self.client_get("/json/navigation_views")
        expected = [display.get_navigation_view_dict(own)]
        self.assertEqual(self.assert_json_success(basic)["navigation_views"], expected)
        self.assertEqual(self.assert_json_success(web)["navigation_views"], expected)
        self.assertEqual(render.call_count, 2)
        for invocation in render.call_args_list:
            self.assertEqual(invocation.args, ([(own.fragment, own.is_pinned, own.name)],))

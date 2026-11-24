import os
from importlib import import_module
from typing import TypedDict

from django.utils.translation import gettext as _

from zerver.lib.exceptions import ResourceNotFoundError
from zerver.models import NavigationView, UserProfile


class NavigationViewDict(TypedDict):
    fragment: str
    is_pinned: bool
    name: str | None


def get_navigation_view(user: UserProfile, fragment: str) -> NavigationView:
    try:
        navigation_view = NavigationView.objects.get(user=user, fragment=fragment)
        return navigation_view
    except NavigationView.DoesNotExist:
        raise ResourceNotFoundError(_("Navigation view does not exist."))


def get_navigation_view_dict(navigation_view: NavigationView) -> NavigationViewDict:
    return NavigationViewDict(
        fragment=navigation_view.fragment,
        is_pinned=navigation_view.is_pinned,
        name=navigation_view.name,
    )


try:
    _rust_navigation_view_display = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_NAVIGATION_VIEW_DISPLAY") == "1"
        else None
    )
except ImportError:
    _rust_navigation_view_display = None

_render_navigation_view_display_rows = getattr(
    _rust_navigation_view_display, "render_navigation_view_display_rows", None
)


def get_navigation_views_for_user(
    user: UserProfile, *, rust_display_for_get: bool = False
) -> list[NavigationViewDict]:
    navigation_views = NavigationView.objects.filter(user=user)
    if rust_display_for_get and callable(_render_navigation_view_display_rows):
        # The queryset already fetches its rows before exposing its iterator.
        views = list(iter(navigation_views))
        if all(type(view) is NavigationView for view in views):
            rows = [(view.fragment, view.is_pinned, view.name) for view in views]
            rendered = _render_navigation_view_display_rows(rows)
            if (
                type(rendered) is list
                and len(rendered) == len(rows)
                and all(type(row) is dict for row in rendered)
            ):
                return rendered
            return [
                NavigationViewDict(fragment=fragment, is_pinned=is_pinned, name=name)
                for fragment, is_pinned, name in rows
            ]
        return [get_navigation_view_dict(view) for view in views]
    return [get_navigation_view_dict(navigation_view) for navigation_view in navigation_views]

from typing import Any

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.event_types import (
    SavedSnippetFields,
    SavedSnippetsAddEvent,
    SavedSnippetsRemoveEvent,
    SavedSnippetsUpdateEvent,
)
from zerver.lib.exceptions import ResourceNotFoundError
from zerver.lib.native_backend import load_native_contract
from zerver.models import RealmAuditLog, SavedSnippet, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.tornado.django_api import send_event_on_commit


@transaction.atomic(durable=True)
def do_create_saved_snippet(
    title: str,
    content: str,
    user_profile: UserProfile,
) -> SavedSnippet:
    saved_snippet = SavedSnippet.objects.create(
        realm=user_profile.realm,
        user_profile=user_profile,
        title=title,
        content=content,
    )

    RealmAuditLog.objects.create(
        realm=user_profile.realm,
        acting_user=user_profile,
        modified_user=user_profile,
        event_type=AuditLogEventType.SAVED_SNIPPET_CREATED,
        event_time=timezone_now(),
        extra_data={"saved_snippet_id": saved_snippet.id},
    )

    event = SavedSnippetsAddEvent(
        saved_snippet=SavedSnippetFields(**saved_snippet.to_api_dict()),
    )
    send_event_on_commit(user_profile.realm, event, [user_profile.id])

    return saved_snippet


def do_edit_saved_snippet(
    saved_snippet_id: int,
    title: str | None,
    content: str | None,
    user_profile: UserProfile,
) -> SavedSnippet:
    try:
        saved_snippet = SavedSnippet.objects.get(id=saved_snippet_id, user_profile=user_profile)
    except SavedSnippet.DoesNotExist:
        raise ResourceNotFoundError(_("Saved snippet does not exist."))

    update_fields = []
    if title is not None:
        saved_snippet.title = title
        update_fields.append("title")
    if content is not None:
        saved_snippet.content = content
        update_fields.append("content")

    with transaction.atomic(durable=True):
        saved_snippet.save(update_fields=update_fields)

        event = SavedSnippetsUpdateEvent(
            saved_snippet=SavedSnippetFields(**saved_snippet.to_api_dict()),
        )
        send_event_on_commit(user_profile.realm, event, [user_profile.id])

    return saved_snippet


try:
    _rust_saved_snippet_display = load_native_contract(
        "ZULIP_RUST_SAVED_SNIPPET_DISPLAY", "SAVED_SNIPPET_DISPLAY_API"
    )
except ImportError:
    _rust_saved_snippet_display = None

_render_saved_snippet_display_rows = getattr(
    _rust_saved_snippet_display, "render_saved_snippet_display_rows", None
)


def do_get_saved_snippets(
    user_profile: UserProfile, *, rust_display_for_get: bool = False
) -> list[dict[str, Any]]:
    saved_snippets = SavedSnippet.objects.filter(user_profile=user_profile)

    if rust_display_for_get and callable(_render_saved_snippet_display_rows):
        # The existing queryset fetches all rows before exposing its iterator.
        # Only renderer input, not the queryset or timestamp semantics, is native.
        snippets = list(iter(saved_snippets))
        if all(type(snippet) is SavedSnippet for snippet in snippets):
            rows = [
                (snippet.id, snippet.title, snippet.content, int(snippet.date_created.timestamp()))
                for snippet in snippets
            ]
            rendered = _render_saved_snippet_display_rows(rows)
            if (
                type(rendered) is list
                and len(rendered) == len(rows)
                and all(type(row) is dict for row in rendered)
            ):
                return rendered
            return [
                {"id": snippet_id, "title": title, "content": content, "date_created": date_created}
                for snippet_id, title, content, date_created in rows
            ]
        return [snippet.to_api_dict() for snippet in snippets]

    return [saved_snippet.to_api_dict() for saved_snippet in saved_snippets]


def do_delete_saved_snippet(
    saved_snippet_id: int,
    user_profile: UserProfile,
) -> None:
    try:
        saved_snippet = SavedSnippet.objects.get(id=saved_snippet_id, user_profile=user_profile)
    except SavedSnippet.DoesNotExist:
        raise ResourceNotFoundError(_("Saved snippet does not exist."))
    saved_snippet.delete()

    event = SavedSnippetsRemoveEvent(saved_snippet_id=saved_snippet_id)
    send_event_on_commit(user_profile.realm, event, [user_profile.id])

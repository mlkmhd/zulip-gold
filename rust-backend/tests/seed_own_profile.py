# ruff: noqa: INP001
"""Capture the self-profile response in an owner-private synthetic fixture."""

from __future__ import annotations

import argparse
import json
import os
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

import django
from read_proxy_fixture import validate_fixture, validate_local_root, verify_private_database


def emit_reference(path: Path) -> None:
    from django.test import Client

    from zerver.actions.message_send import internal_send_private_message
    from zerver.lib.avatar import get_avatar_field
    from zerver.lib.timezone import canonicalize_timezone
    from zerver.lib.users import is_cross_realm_bot_email
    from zerver.models import CustomProfileField, CustomProfileFieldValue, UserProfile

    members = UserProfile.objects.filter(realm__string_id="zulip", is_active=True)
    owner = members.get(delivery_email="desdemona@zulip.com")
    admin = members.get(delivery_email="iago@zulip.com")
    member = members.get(delivery_email="hamlet@zulip.com")
    guest = members.get(delivery_email="polonius@zulip.com")
    bot = members.get(delivery_email="default-bot@zulip.com")
    other = UserProfile.objects.filter(
        realm__string_id="zephyr", is_active=True, is_bot=False, role=UserProfile.ROLE_MEMBER
    ).first()
    assert other is not None and owner.is_realm_owner and admin.is_realm_admin
    assert guest.is_guest and bot.is_bot

    UserProfile.objects.filter(id=member.id).update(
        timezone="US/Eastern",
        date_joined=datetime(2020, 2, 29, 19, 30, 59, 999999, tzinfo=timezone.utc),
        email="hidden-self@testserver",
        email_address_visibility=UserProfile.EMAIL_ADDRESS_VISIBILITY_NOBODY,
    )
    member.refresh_from_db()

    text = CustomProfileField.objects.create(
        realm=member.realm, name="Pilot short text", field_type=CustomProfileField.SHORT_TEXT
    )
    paragraph = CustomProfileField.objects.create(
        realm=member.realm, name="Pilot paragraph", field_type=CustomProfileField.PARAGRAPH
    )
    date = CustomProfileField.objects.create(
        realm=member.realm, name="Pilot date", field_type=CustomProfileField.DATE
    )
    foreign = CustomProfileField.objects.create(
        realm=other.realm, name="Other org text", field_type=CustomProfileField.SHORT_TEXT
    )
    CustomProfileFieldValue.objects.bulk_create(
        [
            CustomProfileFieldValue(
                user_profile=member, field=text, value="مثال", rendered_value="<p>مثال</p>"
            ),
            CustomProfileFieldValue(user_profile=member, field=paragraph, value="A paragraph."),
            CustomProfileFieldValue(
                user_profile=member, field=date, value="2025-02-01", rendered_value="unused"
            ),
            CustomProfileFieldValue(user_profile=other, field=foreign, value="Other realm"),
        ]
    )

    # Local test messages establish different maxima. No notification leaves the fixture.
    own_latest = internal_send_private_message(
        owner, member, "Synthetic self-profile fixture", disable_external_notifications=True
    )
    different_latest = internal_send_private_message(
        guest, admin, "Separate synthetic history", disable_external_notifications=True
    )
    assert own_latest is not None and different_latest is not None and different_latest > own_latest

    profiles = []
    for user in (owner, admin, member, guest, bot, other):
        # Storage configuration remains Django-owned; do not take inputs from the response.
        avatar = get_avatar_field(
            user.id,
            user.realm_id,
            user.delivery_email,
            user.avatar_source,
            user.avatar_version,
            medium=False,
            client_gravatar=False,
        )
        timezone_name = canonicalize_timezone(user.timezone)
        for mode in ("basic", "session"):
            if user.is_bot and mode == "session":
                continue
            client = Client()
            headers = {"HTTP_HOST": user.realm.host}
            if mode == "basic":
                token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
                headers["HTTP_AUTHORIZATION"] = f"Basic {token}"
                url = "/api/v1/users/me"
            else:
                client.force_login(user)
                url = "/json/users/me"
            response = client.get(url, {"ignored_example": "1"}, **headers)
            assert response.status_code == 200
            body = response.json()
            assert body.pop("result") == "success" and body.pop("msg") == ""
            assert "ignored_parameters_unsupported" not in body
            assert body["user_id"] == user.id and body["delivery_email"] == user.delivery_email
            assert body["avatar_url"] == avatar and body["timezone"] == timezone_name
            if user == member:
                assert timezone_name == "America/New_York"
                assert body["date_joined"] == "2020-02-29T19:30+00:00"
                assert body["email"] != body["delivery_email"]
                assert body["max_message_id"] == own_latest
                stored_fields = body["profile_data"]
                assert stored_fields[str(text.id)] == {
                    "value": "مثال",
                    "rendered_value": "<p>مثال</p>",
                }
                assert stored_fields[str(paragraph.id)] == {
                    "value": "A paragraph.",
                    "rendered_value": None,
                }
                assert stored_fields[str(date.id)] == {"value": "2025-02-01"}
                assert str(foreign.id) not in stored_fields
            elif user in (guest, admin):
                assert body["max_message_id"] == different_latest
            elif user.is_bot or user == other:
                assert body["max_message_id"] == -1
            if user.is_bot:
                assert "profile_data" not in body and "bot_type" in body and "bot_owner_id" in body
            profiles.append(
                {
                    "mode": mode,
                    "user": user.id,
                    "realm": user.realm_id,
                    "avatar": avatar,
                    "is_system_bot": is_cross_realm_bot_email(user.email),
                    "timezone_aliases": {user.timezone: timezone_name}
                    if user.timezone != timezone_name
                    else {},
                    "payload": body,
                }
            )

    assert len(profiles) == 11
    with path.open("x") as output:
        json.dump(profiles, output)
    print("Django self-profile references: 6 Basic and 5 session responses.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if any(name.startswith("ZULIP_RUST_") for name in os.environ):
        raise RuntimeError("the reference must use Django with native bridges disabled")
    validate_fixture(root)
    django.setup()
    verify_private_database()
    emit_reference(root / "own-profile.json")


if __name__ == "__main__":
    main()

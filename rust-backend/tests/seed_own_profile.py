# ruff: noqa: INP001
"""Capture the self-profile response in an owner-private synthetic fixture."""

from __future__ import annotations

import argparse
import json
import os
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import django
from django.conf import settings
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
        avatar_source=UserProfile.AVATAR_FROM_GRAVATAR,
        date_joined=datetime(2020, 2, 29, 19, 30, 59, 999999, tzinfo=timezone.utc),
        email="hidden-self@testserver",
        email_address_visibility=UserProfile.EMAIL_ADDRESS_VISIBILITY_NOBODY,
    )
    member.refresh_from_db()
    UserProfile.objects.filter(id=other.id).update(avatar_source=UserProfile.AVATAR_FROM_USER)
    other.refresh_from_db()

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
        assert avatar is not None
        avatar_kind = (
            "system_bot"
            if is_cross_realm_bot_email(user.delivery_email)
            else "stored"
            if user.avatar_source
            in {UserProfile.AVATAR_FROM_USER, UserProfile.AVATAR_FROM_JDENTICON}
            else "default"
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
                    "avatar_kind": avatar_kind,
                    "avatar_version": user.avatar_version,
                    "payload": body,
                }
            )

    assert len(profiles) == 11
    with path.open("x") as output:
        json.dump(profiles, output)
    print("Django self-profile references: 6 Basic and 5 session responses.")


def emit_presentation_reference(root: Path) -> None:
    from scripts.lib.zulip_tools import get_tzdata_zi
    from zerver.lib.avatar import get_avatar_field
    from zerver.lib.timezone import get_canonical_timezone_map
    from zerver.models.users import is_cross_realm_bot_email

    # Use the same installed read-only timezone source as Django.
    with get_tzdata_zi() as tzdata:
        (root / "profile-tzdata.zi").write_text(tzdata.read())
    with (root / "profile-timezone-aliases.json").open("x") as alias_output:
        json.dump(get_canonical_timezone_map(), alias_output)

    # Test Django's avatar branches directly without any login, storage or fetch.
    system = min(settings.CROSS_REALM_BOT_EMAILS).upper()
    ordinary = "normal@example.test"
    references = []
    with (
        patch("zerver.lib.avatar.get_static_avatar_url", return_value="system_bot"),
        patch("zerver.lib.avatar.user_avatar_base_path_from_ids", return_value="fixture-avatar"),
        patch("zerver.lib.avatar.get_avatar_url", return_value="stored"),
        patch("zerver.lib.avatar.get_gravatar_url", return_value="default"),
    ):
        for public in (system, ordinary):
            for delivery in (system, ordinary):
                for source in ("G", "J", "U"):
                    value = get_avatar_field(
                        1, 1, delivery, source, 1, medium=False, client_gravatar=False
                    )
                    references.append(
                        {
                            "email": public,
                            "delivery_email": delivery,
                            "source": source,
                            "kind": value,
                            "is_system_bot": is_cross_realm_bot_email(public),
                        }
                    )
    with (root / "profile-avatar-cases.json").open("x") as output:
        json.dump(
            {"system_bot_emails": sorted(settings.CROSS_REALM_BOT_EMAILS), "cases": references},
            output,
        )
    print("Django presentation references: complete tzdata aliases and 12 avatar/system-bot cases.")


def emit_avatar_handoff_reference(root: Path) -> None:
    """Produce resolved URLs without an HTTP request or a storage backend."""
    from django.test import override_settings

    from zerver.lib.avatar import get_avatar_field
    from zerver.lib.timezone import canonicalize_timezone
    from zerver.models import Realm, UserProfile

    realm = Realm.objects.get(string_id="zulip")
    other_realm = Realm.objects.get(string_id="zephyr")
    bot_email = "handoff-bot@example.invalid"
    fixtures = (
        ("stored_user", realm.id, UserProfile.AVATAR_FROM_USER, False),
        ("stored_jdenticon", other_realm.id, UserProfile.AVATAR_FROM_JDENTICON, False),
        ("realm_default", other_realm.id, UserProfile.AVATAR_FROM_GRAVATAR, False),
        ("system_bot", realm.id, UserProfile.AVATAR_FROM_USER, True),
    )

    users = []
    for label, realm_id, source, is_bot in fixtures:
        address = bot_email if is_bot else f"{label}@example.invalid"
        user = UserProfile(
            realm_id=realm_id,
            full_name=f"Avatar handoff fixture {label}",
            email=address,
            delivery_email=address,
            is_bot=is_bot,
            bot_type=UserProfile.DEFAULT_BOT if is_bot else None,
            avatar_source=source,
            avatar_version=17,
            timezone="US/Eastern",
        )
        user.set_unusable_password()
        users.append(user)
    UserProfile.objects.bulk_create(users)

    cases = []
    for generation in (1, 2):
        upload_base = f"https://upload-{generation}.example.invalid"
        static_base = f"https://static-{generation}.example.invalid"
        default_uri = "https://default.example.invalid/avatar.png"
        gravatar_overrides = {other_realm.id: True} if generation == 2 else {}
        with (
            override_settings(
                CROSS_REALM_BOT_EMAILS={bot_email},
                WELCOME_BOT=bot_email,
                DEBUG=False,
                STATIC_ROOT="",
                ENABLE_GRAVATAR=False,
                DEFAULT_AVATAR_URI=default_uri,
                GRAVATAR_REALM_OVERRIDE=gravatar_overrides,
            ),
            patch("zerver.lib.avatar.staticfiles_storage") as static,
            patch(
                "zerver.lib.avatar.user_avatar_base_path_from_ids",
                side_effect=lambda user_id, version, realm_id: f"{realm_id}/{user_id}/{version}",
            ) as stored_path,
            patch(
                "zerver.lib.avatar.get_avatar_url",
                side_effect=lambda path, medium, base=upload_base: f"{base}/{path}.png",
            ) as upload,
            patch("zerver.lib.avatar.gravatar_hash", return_value="synthetic-gravatar") as gravatar,
        ):
            static.url.side_effect = lambda path, base=static_base: f"{base}/{path}"
            for (label, _, _, is_bot), user in zip(fixtures, users, strict=True):
                avatar = get_avatar_field(
                    user.id,
                    user.realm_id,
                    user.delivery_email,
                    user.avatar_source,
                    user.avatar_version,
                    medium=False,
                    client_gravatar=False,
                )
                if label.startswith("stored_"):
                    kind = "stored"
                    expected = f"{upload_base}/{user.realm_id}/{user.id}/17.png"
                    stored_path.assert_called_with(user.id, 17, user.realm_id)
                    upload.assert_called_with(f"{user.realm_id}/{user.id}/17", medium=False)
                elif is_bot:
                    kind = "system_bot"
                    expected = f"{static_base}/images/static_avatars/welcome-bot.png"
                    static.url.assert_called_once_with("images/static_avatars/welcome-bot.png")
                elif generation == 1:
                    kind = "default"
                    expected = "https://default.example.invalid/avatar.png?version=17"
                    gravatar.assert_not_called()
                else:
                    kind = "default"
                    expected = "https://secure.gravatar.com/avatar/synthetic-gravatar?d=identicon&version=17"
                    gravatar.assert_called_once_with(user.delivery_email)

                assert avatar == expected, (label, generation)
                timezone_name = canonicalize_timezone(user.timezone)
                assert timezone_name == "America/New_York"
                cases.append(
                    {
                        "label": label,
                        "generation": generation,
                        "user": user.id,
                        "realm": user.realm_id,
                        "avatar_version": user.avatar_version,
                        "avatar_source": user.avatar_source,
                        "avatar_kind": kind,
                        "avatar_url": avatar,
                        "expected_url": expected,
                        "timezone": timezone_name,
                        "is_bot": is_bot,
                    }
                )
            assert stored_path.call_count == upload.call_count == 2

    with (root / "profile-avatar-handoff.json").open("x") as output:
        json.dump({"system_bot_emails": [bot_email], "cases": cases}, output)
    print("Django avatar handoff references: 8 exact URLs across 2 settings generations.")


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
    emit_presentation_reference(root)
    emit_avatar_handoff_reference(root)


if __name__ == "__main__":
    main()

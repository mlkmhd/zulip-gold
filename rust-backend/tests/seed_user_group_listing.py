# ruff: noqa: INP001
"""Write Django's user-group list contract from a disposable synthetic fixture."""

from __future__ import annotations

import argparse
import json
import os
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

import django
from read_proxy_fixture import validate_fixture, validate_local_root, verify_private_database
from seed_user_group_details import emit_detail_cases


def emit_cases(output: Path) -> None:
    from django.test import Client

    from zerver.actions.user_groups import check_add_user_group
    from zerver.lib.user_groups import user_groups_in_realm_serialized
    from zerver.models import GroupGroupMembership, UserGroup, UserGroupMembership, UserProfile

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    second = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    inactive = UserProfile.objects.get(realm=member.realm, delivery_email="cordelia@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    other = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert other is not None and guest.is_guest and not member.is_guest

    realm = member.realm
    # Create ordinary separate groups, then model a historical deactivation and
    # unnamed setting membership directly in the disposable database.
    parent = check_add_user_group(
        realm, "Pilot group", [inactive, second, member], "گروه آزمایشی", acting_user=member
    )
    child = check_add_user_group(realm, "Pilot child", [], acting_user=member)
    retired = check_add_user_group(realm, "Pilot retired", [second], acting_user=member)
    outside = check_add_user_group(other.realm, "Other pilot group", [other], acting_user=other)
    UserProfile.objects.filter(id=inactive.id).update(is_active=False)
    parent.date_created = datetime(1969, 12, 31, 23, 59, 59, 500000, tzinfo=timezone.utc)
    child.date_created = None
    child.creator = None
    retired.deactivated = True
    child.save(update_fields=["date_created", "creator"])
    retired.save(update_fields=["deactivated"])
    # Group IDs use PostgreSQL bigint even though user IDs use integer.
    unnamed = UserGroup.objects.create(id=2**31 + 9, realm=realm)
    UserGroupMembership.objects.bulk_create(
        [UserGroupMembership(user_group=unnamed, user_profile=user) for user in (second, inactive)]
    )
    GroupGroupMembership.objects.bulk_create(
        [
            GroupGroupMembership(supergroup=parent, subgroup=child),
            GroupGroupMembership(supergroup=unnamed, subgroup=child),
        ]
    )
    parent.can_add_members_group = unnamed
    parent.save(update_fields=["date_created", "can_add_members_group"])

    client = Client()

    def headers(user: UserProfile) -> dict[str, str]:
        token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"HTTP_HOST": user.realm.host, "HTTP_AUTHORIZATION": f"Basic {token}"}

    path = "/api/v1/user_groups"
    denied = client.get(path, **headers(guest))
    assert denied.status_code == 400
    assert denied.json()["msg"] == "Not allowed for guest users"
    assert client.get(path, HTTP_HOST=realm.host).status_code == 401

    cases = []
    for user in (member, other):
        for include in (False, True):
            response = client.get(
                path, {"include_deactivated_groups": json.dumps(include)}, **headers(user)
            )
            assert response.status_code == 200
            result = response.json()["user_groups"]
            assert (
                result
                == user_groups_in_realm_serialized(
                    user.realm, include_deactivated_groups=include
                ).api_groups
            )
            by_id = {row["id"]: row for row in result}
            if user == member:
                assert outside.id not in by_id
                assert (retired.id in by_id) == include
                assert by_id[parent.id]["date_created"] == 0
                assert by_id[parent.id]["members"] == sorted([member.id, second.id])
                assert by_id[parent.id]["direct_subgroup_ids"] == [child.id]
                assert by_id[parent.id]["can_add_members_group"] == {
                    "direct_members": [second.id],
                    "direct_subgroups": [child.id],
                }
                assert by_id[child.id]["members"] == []
                assert by_id[child.id]["date_created"] is None
                assert by_id[child.id]["creator_id"] is None
            else:
                assert outside.id in by_id and parent.id not in by_id
            cases.append(
                {"realm": user.realm_id, "include_deactivated": include, "listing": result}
            )

    with output.open("x") as fixture:
        json.dump(cases, fixture)
    print("Django GET: four allowed list cases, one guest rejection and one anonymous rejection.")
    emit_request_cases(output.with_name("user-group-listing-requests.json"))


def emit_request_cases(output: Path) -> None:
    from django.test import Client

    from zerver.models import UserProfile

    users = UserProfile.objects.filter(is_active=True, is_bot=False)
    member = users.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = users.get(
        realm=member.realm, role=UserProfile.ROLE_GUEST, delivery_email="polonius@zulip.com"
    )
    admin = users.filter(realm=member.realm, role=UserProfile.ROLE_REALM_ADMINISTRATOR).first()
    other = users.filter(realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER).first()
    assert admin is not None and other is not None and not member.is_guest
    parameter = "include_deactivated_groups"
    cases = [
        (member, None, 200),
        (member, f"{parameter}=false", 200),
        (member, f"{parameter}=true", 200),
        (member, f"{parameter}=false&{parameter}=true", 200),
        (member, f"z=one&client=website&a=two&{parameter}=true", 200),
        (member, f"{parameter}=%20true%20", 200),
        (member, f"{parameter}=undefined", 400),
        (member, f"{parameter}=1", 400),
        (member, f"{parameter}=%22yes%22", 400),
        (member, f"{parameter}=", 400),
        (guest, None, 400),
        (guest, f"{parameter}=undefined", 400),
        (guest, f"{parameter}=true", 400),
        (admin, f"{parameter}=true", 200),
        (other, f"{parameter}=true", 200),
    ]
    rows = []
    for mode in ("basic", "session"):
        for user, query, status in cases:
            client = Client()
            headers = {"HTTP_HOST": user.realm.host}
            if mode == "basic":
                token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
                headers["HTTP_AUTHORIZATION"] = f"Basic {token}"
                url = "/api/v1/user_groups"
            else:
                client.force_login(user)
                url = "/json/user_groups"
            if query is not None:
                url = f"{url}?{query}"
            response = client.get(url, **headers)
            payload = response.json()
            assert response.status_code == status, (mode, query, payload)
            if status == 200:
                assert payload.pop("result") == "success" and payload.pop("msg") == ""
            else:
                assert payload["result"] == "error" and payload["code"] == "BAD_REQUEST", payload
                if user.is_guest:
                    assert payload["msg"] == "Not allowed for guest users", payload
            rows.append(
                {
                    "mode": mode,
                    "user": user.id,
                    "realm": user.realm_id,
                    "query": query,
                    "status": status,
                    "payload": payload,
                }
            )

    with output.open("x") as fixture:
        json.dump(rows, fixture)
    print(f"Django authenticated group query references: {len(rows)} Basic and session cases.")


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
    emit_detail_cases(root / "user-group-details.json")
    emit_cases(root / "user-group-listing.json")


if __name__ == "__main__":
    main()

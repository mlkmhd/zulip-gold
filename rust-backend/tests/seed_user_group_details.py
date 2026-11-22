# ruff: noqa: INP001
"""Capture ordinary Django group member and subgroup reads in a synthetic fixture."""

from __future__ import annotations

import json
from base64 import b64encode
from pathlib import Path


def emit_detail_cases(output: Path) -> None:
    from django.test import Client

    from zerver.actions.user_groups import check_add_user_group
    from zerver.models import GroupGroupMembership, UserGroup, UserProfile

    member = UserProfile.objects.get(realm__string_id="zulip", delivery_email="hamlet@zulip.com")
    guest = UserProfile.objects.get(realm=member.realm, delivery_email="polonius@zulip.com")
    second = UserProfile.objects.get(realm=member.realm, delivery_email="othello@zulip.com")
    nested = UserProfile.objects.get(realm=member.realm, delivery_email="shiva@zulip.com")
    inactive = (
        UserProfile.objects.filter(
            realm=member.realm, role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
        )
        .exclude(id__in=[member.id, second.id, nested.id])
        .exclude(delivery_email="cordelia@zulip.com")
        .first()
    )
    bot = UserProfile.objects.get(realm=member.realm, delivery_email="default-bot@zulip.com")
    other = UserProfile.objects.filter(
        realm__string_id="zephyr", role=UserProfile.ROLE_MEMBER, is_active=True, is_bot=False
    ).first()
    assert inactive is not None and other is not None and guest.is_guest and bot.is_bot

    def group(name: str, users: list[UserProfile]) -> UserGroup:
        return check_add_user_group(member.realm, f"Detail {name}", users, acting_user=member)

    root = group("root", [second, inactive])
    left = group("left", [member])
    right = group("right", [])
    leaf = group("leaf", [member, nested])
    empty = group("empty", [])
    retired = check_add_user_group(member.realm, "Detail retired", [nested], acting_user=member)
    foreign = check_add_user_group(other.realm, "Detail outside", [other], acting_user=other)
    unnamed = UserGroup.objects.create(id=2**31 + 10, realm=member.realm)
    GroupGroupMembership.objects.bulk_create(
        [
            GroupGroupMembership(supergroup=parent, subgroup=child)
            for parent, child in ((root, left), (root, right), (left, leaf), (right, leaf))
        ]
    )
    UserProfile.objects.filter(id=inactive.id).update(is_active=False)
    retired.deactivated = True
    retired.save(update_fields=["deactivated"])

    references = []
    for kind, field, expected_direct, expected_recursive in (
        ("members", "direct_member_only", [second.id], sorted([member.id, second.id, nested.id])),
        (
            "subgroups",
            "direct_subgroup_only",
            sorted([left.id, right.id]),
            sorted([left.id, right.id, leaf.id]),
        ),
    ):
        fixtures = [
            (member, root.id, None, expected_recursive),
            (member, root.id, f"{field}=true", expected_direct),
            (member, root.id, f"{field}=false", expected_recursive),
            (
                member,
                root.id,
                f"z=1&{field}=false&client=website&{field}=true&a=2",
                expected_direct,
            ),
            (member, root.id, f"{field}=undefined", f"{field} is not valid JSON"),
            (member, root.id, f"{field}=1", f"{field} is not a boolean"),
            (guest, root.id, None, "Not allowed for guest users"),
            (guest, root.id, f"{field}=undefined", "Not allowed for guest users"),
            (member, retired.id, None, "User group is deactivated."),
            (member, foreign.id, None, "Invalid user group"),
            (member, foreign.id, f"{field}=undefined", f"{field} is not valid JSON"),
            (member, unnamed.id, None, "Invalid user group"),
            (member, 0, None, "Invalid user group"),
            (member, empty.id, None, []),
            (other, foreign.id, None, [other.id] if kind == "members" else []),
        ]
        for mode in ("basic", "session"):
            cases = (
                [*fixtures, (bot, root.id, None, expected_recursive)]
                if mode == "basic"
                else fixtures
            )
            for user, group_id, query, expected in cases:
                client = Client()
                headers = {"HTTP_HOST": user.realm.host}
                if mode == "basic":
                    token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
                    headers["HTTP_AUTHORIZATION"] = f"Basic {token}"
                    path = f"/api/v1/user_groups/{group_id}/{kind}"
                else:
                    client.force_login(user)
                    path = f"/json/user_groups/{group_id}/{kind}"
                if query is not None:
                    path = f"{path}?{query}"
                response = client.get(path, **headers)
                body = response.json()
                if isinstance(expected, list):
                    assert response.status_code == 200, (mode, kind, query, body)
                    assert body.pop("result") == "success" and body.pop("msg") == ""
                    body[kind].sort()
                    assert body[kind] == expected, (mode, kind, query, body)
                else:
                    assert response.status_code == 400, (mode, kind, query, body)
                    assert body == {"result": "error", "code": "BAD_REQUEST", "msg": expected}, body
                references.append(
                    {
                        "mode": mode,
                        "user": user.id,
                        "realm": user.realm_id,
                        "group": group_id,
                        "kind": kind,
                        "query": query,
                        "status": response.status_code,
                        "payload": body,
                    }
                )

    with output.open("x") as fixture:
        json.dump(references, fixture)
    print(
        f"Django group detail references: {len(references)} member/subgroup Basic and session cases."
    )

# ruff: noqa: INP001
"""Verify the own-profile formatter with Django authentication in a private fixture."""

from __future__ import annotations

import argparse
import json
import os
from base64 import b64encode
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import django
from read_proxy_fixture import validate_fixture, validate_local_root, verify_private_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    validate_local_root(root)
    if any(name.startswith("ZULIP_RUST_") for name in os.environ):
        raise RuntimeError("start with all Rust features disabled")
    validate_fixture(root)
    django.setup()
    verify_private_database()

    from django.test import Client

    from zerver.models import UserProfile
    from zerver.views import users as views

    native = import_module("_zulip_backend")
    reference = json.loads((root / "own-profile.json").read_text())
    assert len(reference) == 11
    for case in reference:
        user = UserProfile.objects.get(id=case["user"], realm_id=case["realm"])
        client = Client()
        headers = {"HTTP_HOST": user.realm.host}
        if case["mode"] == "basic":
            token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
            headers["HTTP_AUTHORIZATION"] = f"Basic {token}"
            url = "/api/v1/users/me"
        else:
            client.force_login(user)
            url = "/json/users/me"

        expected = {"result": "success", "msg": "", **case["payload"]}
        with patch.object(views, "get_own_profile_with_rust", side_effect=AssertionError):
            default = client.get(url, {"ignored_example": "1"}, **headers)
            assert default.status_code == 200 and default.json() == expected
        with (
            patch.dict(os.environ, {"ZULIP_RUST_OWN_PROFILE_PAYLOAD": "1"}),
            patch.object(views, "get_users_for_api", side_effect=AssertionError),
        ):
            enabled = client.get(url, {"ignored_example": "1"}, **headers)
            assert enabled.status_code == 200 and enabled.json() == expected
        with (
            patch.dict(os.environ, {"ZULIP_RUST_OWN_PROFILE_PAYLOAD": "1"}),
            patch.object(native, "assemble_own_profile", return_value=None),
        ):
            fallback = client.get(url, {"ignored_example": "1"}, **headers)
            assert fallback.status_code == 200 and fallback.json() == expected

    print("Matched 11 Django own-profile responses in default, native and fallback modes.")


if __name__ == "__main__":
    main()

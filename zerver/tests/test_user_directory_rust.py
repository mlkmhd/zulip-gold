from collections.abc import Callable
from importlib import import_module
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import Mock, patch

import orjson
from django.test import SimpleTestCase

from zerver.actions.user_settings import do_change_user_setting
from zerver.lib import native_user_directory
from zerver.lib.native_user_directory import UserRowPresentation
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.users import APIUserDict, _render_user_row_presentation, get_users_for_api
from zerver.models import UserProfile


def extension() -> Callable[[list[UserRowPresentation]], object]:
    try:
        backend = import_module("_zulip_backend")
    except ModuleNotFoundError:
        raise SkipTest("Rust extension is not installed") from None
    method = getattr(backend, "assemble_user_directory", None)
    if not callable(method):
        raise SkipTest("Rust extension does not support user directory assembly")
    return method


class UserDirectoryProjectionTest(SimpleTestCase):
    def test_optional_fields_duplicate_id_and_legacy_values(self) -> None:
        native = extension()
        base = UserRowPresentation(
            email="synthetic@example.test",
            user_id=15,
            avatar_source="G",
            avatar_version=1,
            is_admin=False,
            is_owner=False,
            is_guest=False,
            role=UserProfile.ROLE_MEMBER,
            is_bot=False,
            full_name="پروفایل 🙂",
            timezone="",
            is_active=True,
            date_joined="2000-01-01T12:01+00:00",
            delivery_email=None,
            is_imported_stub=False,
            is_deleted=False,
            avatar=None,
            bot=None,
            profile_data=None,
        )
        rows = [
            base,
            base._replace(user_id=16, timezone=None, date_joined="2000-01-01", avatar=(None,)),
            base._replace(
                user_id=17,
                is_bot=True,
                bot=(None, True, None),
                avatar=("https://assets.example.test/avatar.png",),
            ),
            base._replace(user_id=18, is_bot=True, bot=(2, False, 15)),
            base._replace(user_id=19, profile_data={}, is_deleted=True),
            base._replace(
                user_id=20,
                profile_data={"4": {"value": "متن", "rendered_value": "<p>متن</p>"}},
                is_imported_stub=True,
            ),
            base._replace(user_id=15, full_name="updated"),
        ]
        expected = {row.user_id: _render_user_row_presentation(row) for row in rows}
        self.assertEqual(native(rows), expected)
        self.assertEqual(
            orjson.dumps(native(rows), option=orjson.OPT_NON_STR_KEYS),
            orjson.dumps(expected, option=orjson.OPT_NON_STR_KEYS),
        )
        self.assertEqual(native([]), {})
        # A literal bool is not an integer field, even though Python subclasses int.
        self.assertIsNone(native([base, base._replace(avatar_version=True)]))
        self.assertIsNone(native([base, base._replace(bot=(0, False, None), profile_data={})]))


class UserDirectoryEndpointTest(ZulipTestCase):
    def test_real_views_format_only_django_prepared_cards(self) -> None:
        native = extension()
        reader = self.example_user("cordelia")
        hidden = self.example_user("hamlet")
        peer = self.example_user("othello")
        do_change_user_setting(
            hidden,
            "email_address_visibility",
            UserProfile.EMAIL_ADDRESS_VISIBILITY_NOBODY,
            acting_user=None,
        )
        options = {"user_ids": orjson.dumps([hidden.id, peer.id]).decode()}

        def equivalence(label: str, request: Callable[[], bytes]) -> None:
            with patch.object(native_user_directory, "_backend", None):
                expected = request()
            for mode, implementation in (
                ("native", Mock(wraps=native)),
                ("declined", Mock(return_value=None)),
                ("older", None),
            ):
                backend = (
                    SimpleNamespace(assemble_user_directory=implementation)
                    if implementation is not None
                    else SimpleNamespace()
                )
                with (
                    self.subTest(case=label, mode=mode),
                    patch.object(native_user_directory, "_backend", backend),
                    patch(
                        "zerver.lib.users._render_user_row_presentation",
                        wraps=_render_user_row_presentation,
                    ) as python_format,
                ):
                    self.assertEqual(request(), expected)
                    if implementation is not None:
                        self.assertEqual(implementation.call_count, 1)
                        rows = implementation.call_args.args[0]
                        hidden_row = next(row for row in rows if row.user_id == hidden.id)
                        self.assertIsNone(hidden_row.delivery_email)
                        if label == "spectator":
                            self.assertIsNone(hidden_row.timezone)
                            self.assertNotIn("T", hidden_row.date_joined)
                            self.assertIsNone(hidden_row.profile_data)
                        else:
                            self.assertIn("T", hidden_row.date_joined)
                        if mode == "native":
                            self.assertIsNotNone(native(rows))
                    if mode == "native":
                        self.assertEqual(python_format.call_count, 0)
                    else:
                        self.assertGreater(python_format.call_count, 0)

        equivalence(
            "spectator",
            lambda: (
                self.client_get(
                    "/json/users", options | {"include_custom_profile_fields": "true"}
                ).content
            ),
        )
        self.login_user(reader)
        equivalence(
            "session listing",
            lambda: (
                self.client_get(
                    "/json/users", options | {"include_custom_profile_fields": "true"}
                ).content
            ),
        )
        equivalence(
            "Basic listing",
            lambda: self.api_get(reader, "/api/v1/users", options).content,
        )
        equivalence(
            "session individual",
            lambda: self.client_get(f"/json/users/{hidden.id}").content,
        )
        equivalence(
            "Basic individual",
            lambda: self.api_get(reader, f"/api/v1/users/{hidden.id}").content,
        )

    def test_omitted_avatar_is_distinct_from_null(self) -> None:
        backend = SimpleNamespace(assemble_user_directory=extension())
        reader = self.example_user("cordelia")
        target = self.example_user("hamlet")
        target.long_term_idle = True

        def fetch() -> APIUserDict:
            return get_users_for_api(
                reader.realm,
                reader,
                target_user=target,
                client_gravatar=False,
                user_avatar_url_field_optional=True,
            )[target.id]

        with patch.object(native_user_directory, "_backend", None):
            expected = fetch()
            self.assertNotIn("avatar_url", expected)
        with (
            patch.object(native_user_directory, "_backend", backend),
            patch("zerver.lib.users._render_user_row_presentation", side_effect=AssertionError),
        ):
            self.assertEqual(fetch(), expected)

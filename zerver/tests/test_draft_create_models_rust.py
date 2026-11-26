from unittest.mock import call, patch

from django.test import SimpleTestCase
from django.utils.timezone import now as timezone_now

from zerver.lib import drafts
from zerver.models import UserProfile


class DraftCreateModelsRustTest(SimpleTestCase):
    def test_order_and_model_fields_match_without_database_operations(self) -> None:
        backend = drafts._rust_draft_create_models
        if backend is None:
            self.skipTest("No prepared Rust draft create models ABI")
        user = UserProfile(id=55)
        incoming = [
            drafts.DraftData(type="", to=[], content=f"input {index}", topic="")
            for index in (8, 2, 5)
        ]
        when = timezone_now()
        values = [
            drafts.ValidatedDraftData(None, "عنوان", f"prepared {index}", when)
            for index in (8, 2, 5)
        ]
        with (
            patch.object(drafts, "_rust_draft_create_models", None),
            patch.object(drafts, "further_validated_draft_dict", side_effect=values) as reference,
        ):
            expected = drafts._prepare_draft_create_models(incoming, user)
        with (
            patch.object(drafts, "further_validated_draft_dict", side_effect=values) as validate,
            patch.object(
                backend, "prepare_draft_create_models", wraps=backend.prepare_draft_create_models
            ) as build,
        ):
            actual = drafts._prepare_draft_create_models(incoming, user)
        build.assert_called_once()
        self.assertEqual(validate.call_args_list, reference.call_args_list)
        for left, right in zip(actual, expected, strict=True):
            self.assertEqual(
                (
                    left.user_profile_id,
                    left.recipient_id,
                    left.topic,
                    left.content,
                    left.last_edit_time,
                ),
                (
                    right.user_profile_id,
                    right.recipient_id,
                    right.topic,
                    right.content,
                    right.last_edit_time,
                ),
            )
            self.assertTrue(left._state.adding)
            self.assertIs(left.user_profile, user)

    def test_preparation_stops_at_first_error_without_retry(self) -> None:
        user = UserProfile(id=55)
        incoming = [
            drafts.DraftData(type="", to=[], content=f"input {index}", topic="")
            for index in (1, 2, 3)
        ]
        good = drafts.ValidatedDraftData(None, "", "first", timezone_now())
        error = ValueError("synthetic invalid prepared timestamp")
        with (
            patch.object(
                drafts, "further_validated_draft_dict", side_effect=[good, error]
            ) as validate,
            self.assertRaises(ValueError) as seen,
        ):
            drafts._prepare_draft_create_models(incoming, user)
        self.assertIs(seen.exception, error)
        self.assertEqual(
            validate.call_args_list, [call(incoming[0], user), call(incoming[1], user)]
        )

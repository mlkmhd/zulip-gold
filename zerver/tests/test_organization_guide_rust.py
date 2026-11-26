from types import SimpleNamespace
from typing import cast
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from zerver.lib import email_notifications as emails
from zerver.models import Realm


class OrganizationGuideRustTest(TestCase):
    def test_missing_link_is_a_hit_and_only_first_matching_entry_counts(self) -> None:
        catalog = {
            "other": {"id": 99, "onboarding_zulip_guide_url": "unused"},
            "education_nonprofit": {"id": 2, "onboarding_zulip_guide_url": None},
            "education": {"id": 2, "onboarding_zulip_guide_url": "conflicting"},
        }
        sent = Mock()
        with (
            patch.object(Realm, "ORG_TYPES", catalog),
            patch("zerver.lib.email_notifications.logging.error", sent),
        ):
            realm = Mock()
            observed_kind = PropertyMock(return_value=2)
            type(realm).org_type = observed_kind
            self.assertEqual(emails.get_org_type_zulip_guide(realm), (None, "education"))
        self.assertEqual(observed_kind.call_count, 2)
        sent.assert_not_called()

    def test_unknown_logs_once_and_missing_selected_field_propagates(self) -> None:
        realm = cast("Realm", SimpleNamespace(org_type=19))
        with (
            patch.object(Realm, "ORG_TYPES", {}),
            patch("zerver.lib.email_notifications.logging.error") as log,
        ):
            self.assertEqual(emails.get_org_type_zulip_guide(realm), (None, ""))
        log.assert_called_once_with("Unknown organization type '%s'", 19)
        with patch.object(Realm, "ORG_TYPES", {"bad": {"id": 19}}), self.assertRaises(KeyError):
            emails.get_org_type_zulip_guide(realm)

    def test_current_native_returns_original_url_object(self) -> None:
        native = emails._rust_org_guide
        if native is None:
            self.skipTest("requires the current native organization guide contract")
        realm = cast("Realm", SimpleNamespace(org_type=3))
        link = object()
        catalog = {"any": {"id": 3, "onboarding_zulip_guide_url": link}}
        with (
            patch.object(Realm, "ORG_TYPES", catalog),
            patch.object(
                native,
                "select_prepared_organization_guide",
                wraps=native.select_prepared_organization_guide,
            ) as select,
        ):
            url, kind = emails.get_org_type_zulip_guide(realm)
        self.assertIs(url, link)
        self.assertEqual(kind, "any")
        select.assert_called_once_with(realm, catalog)

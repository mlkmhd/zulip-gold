from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from django.test import SimpleTestCase

from zerver.lib import email_notifications
from zerver.lib.email_notifications import get_onboarding_email_schedule
from zerver.models import UserProfile


class OnboardingEmailDelayDjangoRustTest(SimpleTestCase):
    def check_offsets(self, user: UserProfile, offsets: tuple[int, int, int]) -> None:
        schedule = get_onboarding_email_schedule(user)
        self.assertEqual(
            list(schedule),
            [
                "onboarding_zulip_topics",
                "onboarding_zulip_guide",
                "onboarding_team_to_zulip",
            ],
        )
        self.assertEqual(list(schedule.values()), [timedelta(days=n, hours=-1) for n in offsets])

    def test_full_iso_week_and_old_contract(self) -> None:
        cases = {
            1: (2, 4, 8),
            2: (2, 6, 8),
            3: (2, 6, 8),
            4: (4, 6, 8),
            5: (4, 6, 10),
            6: (2, 4, 6),
            7: (2, 4, 8),
        }
        for day, offsets in cases.items():
            with self.subTest(day=day):
                user = UserProfile(
                    timezone="", date_joined=datetime(2018, 1, day, 1, tzinfo=timezone.utc)
                )
                self.check_offsets(user, offsets)
                with patch.object(
                    email_notifications, "_rust_onboarding_email_delay", ModuleType("old")
                ):
                    self.check_offsets(user, offsets)

    def test_local_day_boundary_and_demo_clock(self) -> None:
        friday_utc = datetime(2018, 1, 5, 1, tzinfo=timezone.utc)
        user = UserProfile(timezone="America/Phoenix", date_joined=friday_utc)
        self.check_offsets(user, (4, 6, 8))
        monday_utc = datetime(2018, 1, 1, 12, tzinfo=timezone.utc)
        with patch.object(email_notifications, "timezone_now", return_value=monday_utc) as clock:
            self.check_offsets(user, (4, 6, 8))
            clock.assert_not_called()
            demo = get_onboarding_email_schedule(user, demo_organization_creator=True)
            clock.assert_called_once_with()
        self.assertEqual(list(demo.values()), [timedelta(days=d, hours=-1) for d in (2, 4, 8)])

    def test_invalid_zone_still_fails_before_dispatch(self) -> None:
        user = UserProfile(
            timezone="NoSuch/SyntheticZone", date_joined=datetime(2018, 1, 1, tzinfo=timezone.utc)
        )
        with self.assertRaises(ZoneInfoNotFoundError):
            get_onboarding_email_schedule(user)

    def test_native_dispatch_uses_local_iso_day(self) -> None:
        native = email_notifications._rust_onboarding_email_delay
        if native is None:
            self.skipTest("requires the current native onboarding schedule contract")
        user = UserProfile(
            timezone="America/Phoenix", date_joined=datetime(2018, 1, 5, 1, tzinfo=timezone.utc)
        )
        with patch.object(
            native,
            "prepared_onboarding_email_delay_days",
            wraps=native.prepared_onboarding_email_delay_days,
        ) as delay:
            self.check_offsets(user, (4, 6, 8))
        delay.assert_called_once_with(4)

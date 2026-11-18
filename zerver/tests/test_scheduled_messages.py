import os
import re
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from unittest import mock, skipUnless
from urllib.parse import quote, urlsplit

import orjson
import pika
import time_machine
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import LiveServerTestCase
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.message_send import check_message
from zerver.actions.realm_settings import (
    do_change_realm_permission_group_setting,
    do_set_realm_property,
)
from zerver.actions.scheduled_messages import (
    SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES,
    do_schedule_messages,
    edit_scheduled_message,
    try_deliver_one_scheduled_message,
)
from zerver.actions.streams import (
    do_change_stream_group_based_setting,
    do_deactivate_stream,
    do_set_stream_property,
)
from zerver.actions.users import change_user_is_active
from zerver.lib.addressee import Addressee
from zerver.lib.message import is_message_to_self
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_helpers import most_recent_message
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.lib.timestamp import timestamp_to_datetime
from zerver.lib.types import UserGroupMembersData
from zerver.models import (
    Attachment,
    Message,
    NamedUserGroup,
    Recipient,
    ScheduledMessage,
    Subscription,
    UserMessage,
    UserProfile,
)
from zerver.models.clients import get_client
from zerver.models.groups import SystemGroups
from zerver.models.realms import RealmTopicsPolicyEnum
from zerver.models.recipients import get_direct_message_group, get_or_create_direct_message_group
from zerver.models.streams import StreamTopicsPolicyEnum, get_stream
from zerver.models.users import get_system_bot

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class ScheduledMessageReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_scheduled_messages_and_reminders_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        third = self.example_user("ZOE")
        stream = get_stream("Denmark", user.realm)
        channel_recipient = stream.recipient
        assert channel_recipient is not None
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        api_headers = {"Authorization": f"Basic {token}"}

        with TemporaryDirectory() as directory, ExitStack() as cleanup:

            def group_recipient(ids: list[int]) -> Recipient:
                group = get_direct_message_group(ids)
                if group is None:
                    group = get_or_create_direct_message_group(ids)
                    assert group.recipient is not None
                    cleanup.callback(group.recipient.delete)
                    cleanup.callback(group.delete)
                assert group.recipient is not None
                return group.recipient

            group = group_recipient([user.id, other.id, third.id])
            dm_to_self = group_recipient([user.id])
            client = get_client("website")

            def create(
                sender: UserProfile,
                recipient: Recipient,
                timestamp: float,
                *,
                failed: bool = False,
                delivered: bool = False,
                reminder_target: int | None = None,
            ) -> ScheduledMessage:
                is_stream = recipient == channel_recipient
                message = ScheduledMessage(
                    realm=sender.realm,
                    sender=sender,
                    recipient=recipient,
                    stream=stream if is_stream else None,
                    content="متن **پیام**",
                    rendered_content="<p>متن <strong>پیام</strong></p>",
                    sending_client=client,
                    scheduled_timestamp=timestamp_to_datetime(timestamp),
                    read_by_sender=True,
                    failed=failed,
                    delivered=delivered,
                    reminder_target_message_id=reminder_target,
                    delivery_type=(
                        ScheduledMessage.SEND_LATER
                        if reminder_target is None
                        else ScheduledMessage.REMIND
                    ),
                )
                message.set_topic_name("سلام" if is_stream else Message.DM_TOPIC)
                message.save()
                cleanup.callback(message.delete)
                return message

            direct = create(user, group, 2, failed=True)
            channel = create(user, channel_recipient, -1.5)
            self_dm = create(user, dm_to_self, 3)
            delivered = create(user, group, 1, delivered=True)
            other_message = create(other, group, 1)
            reminder = create(user, dm_to_self, 1, failed=True, reminder_target=123)
            delivered_reminder = create(user, dm_to_self, 2, delivered=True, reminder_target=456)

            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(user.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": user.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            web_headers = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            service = (
                rust_http_service(
                    {
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ).decode(),
                    }
                )
                if os.environ.get("ZULIP_RUST_HTTP_BIN")
                else nullcontext(None)
            )
            with service as rust_port:

                def check(method: str, path: str, headers: dict[str, str], status: int) -> bytes:
                    django = request_test_http(django_port, method, user.realm.host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, user.realm.host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET":
                            self.assertEqual(orjson.loads(rust[1]), orjson.loads(django[1]))
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                for prefix, headers in (("/api/v1", api_headers), ("/json", web_headers)):
                    with self.subTest(prefix=prefix):
                        query = "?unknown=1&client=ZulipMobile"
                        messages_path = f"{prefix}/scheduled_messages{query}"
                        reminders_path = f"{prefix}/reminders{query}"
                        messages = orjson.loads(check("GET", messages_path, headers, 200))
                        self.assertEqual(messages["ignored_parameters_unsupported"], ["unknown"])
                        self.assertEqual(
                            messages["scheduled_messages"][:3],
                            [channel.to_dict(), direct.to_dict(), self_dm.to_dict()],
                        )
                        ids = {
                            item["scheduled_message_id"] for item in messages["scheduled_messages"]
                        }
                        self.assertTrue(
                            ids.isdisjoint({delivered.id, other_message.id, reminder.id})
                        )
                        reminders = orjson.loads(check("GET", reminders_path, headers, 200))
                        self.assertEqual(reminders["ignored_parameters_unsupported"], ["unknown"])
                        self.assertEqual(reminders["reminders"][0], reminder.to_reminder_dict())
                        ids = {item["reminder_id"] for item in reminders["reminders"]}
                        self.assertTrue(ids.isdisjoint({delivered_reminder.id, self_dm.id}))
                        for path in (messages_path, reminders_path):
                            self.assertEqual(check("HEAD", path, headers, 200), b"")
                            check("GET", path, {}, 401)


class ScheduledMessageTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        # populate_db schedules messages for a year after the test
        # database was built, which may already be due.
        ScheduledMessage.objects.all().delete()

    def last_scheduled_message(self) -> ScheduledMessage:
        return ScheduledMessage.objects.all().order_by("-id")[0]

    def get_scheduled_message(self, id: str) -> ScheduledMessage:
        return ScheduledMessage.objects.get(id=id)

    def do_schedule_message(
        self,
        msg_type: str,
        to: int | list[str] | list[int],
        msg: str,
        scheduled_delivery_timestamp: int,
    ) -> "TestHttpResponse":
        self.login("hamlet")

        topic_name = ""
        if msg_type in ["stream", "channel"]:
            topic_name = "Test topic"

        payload = {
            "type": msg_type,
            "to": orjson.dumps(to).decode(),
            "content": msg,
            "topic": topic_name,
            "scheduled_delivery_timestamp": scheduled_delivery_timestamp,
        }

        result = self.client_post("/json/scheduled_messages", payload)
        return result

    def test_schedule_message(self) -> None:
        content = "Test message"
        scheduled_delivery_timestamp = int(time.time() + 86400)
        verona_stream_id = self.get_stream_id("Verona")

        # Scheduling a message to a stream you are subscribed is successful.
        result = self.do_schedule_message(
            "channel", verona_stream_id, f"{content} 1", scheduled_delivery_timestamp
        )
        scheduled_message = self.last_scheduled_message()
        self.assert_json_success(result)
        self.assertEqual(scheduled_message.content, "Test message 1")
        self.assertEqual(scheduled_message.rendered_content, "<p>Test message 1</p>")
        self.assertEqual(scheduled_message.topic_name(), "Test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )

        # Scheduling a direct message with user IDs is successful.
        othello = self.example_user("othello")
        result = self.do_schedule_message(
            "direct", [othello.id], f"{content} 3", scheduled_delivery_timestamp
        )
        scheduled_message = self.last_scheduled_message()
        self.assert_json_success(result)
        self.assertEqual(scheduled_message.content, "Test message 3")
        self.assertEqual(scheduled_message.rendered_content, "<p>Test message 3</p>")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )
        self.assertEqual(scheduled_message.topic_name(), Message.DM_TOPIC)

        # Cannot schedule a direct message with user emails.
        result = self.do_schedule_message(
            "direct", [othello.email], f"{content} 4", scheduled_delivery_timestamp
        )
        self.assert_json_error(result, 'to["int"] is not an integer')

    def test_schedule_group_message_creates_and_reuses_the_recipient(self) -> None:
        sender, *recipients = [
            self.example_user(name).id for name in ("hamlet", "othello", "iago", "ZOE")
        ]
        members = [sender, *recipients]
        self.assertIsNone(get_direct_message_group(members))
        timestamp = int(time.time()) + 86400

        for to in [recipients, [*reversed(recipients), recipients[0]]]:
            response = self.do_schedule_message("direct", to, "Group message", timestamp)
            job_id = self.assert_json_success(response)["scheduled_message_id"]
            job = ScheduledMessage.objects.get(id=job_id)
            group = get_direct_message_group(members)
            assert group is not None
            self.assertEqual(job.recipient_id, group.recipient_id)
            self.assertCountEqual(
                Subscription.objects.filter(recipient=group.recipient).values_list(
                    "user_profile_id", "active", "is_user_active"
                ),
                [(member_id, True, True) for member_id in members],
            )

    def test_schedule_channel_posting_and_topic_permissions(self) -> None:
        sender = self.example_user("hamlet")
        admin = self.example_user("iago")
        bot = self.create_test_bot("scheduled-authorization", sender)
        stream = get_stream("Verona", sender.realm)
        members = NamedUserGroup.objects.get(name="hamletcharacters", realm=sender.realm)
        nobody = NamedUserGroup.objects.get(
            name=SystemGroups.NOBODY, realm=sender.realm, is_system_group=True
        )
        topic_name = "Scheduled %_ Topic"
        self.send_stream_message(sender, stream.name, topic_name=topic_name)
        do_change_stream_group_based_setting(
            stream,
            "can_send_message_group",
            UserGroupMembersData(direct_members=[], direct_subgroups=[members.id]),
            acting_user=admin,
        )
        do_change_stream_group_based_setting(
            stream, "can_create_topic_group", nobody, acting_user=admin
        )
        payload = {
            "type": "channel",
            "to": str(stream.id),
            "topic": topic_name.lower(),
            "content": "Scheduled reply",
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }

        response = self.api_post(admin, "/api/v1/scheduled_messages", payload)
        self.assert_json_error(response, "You do not have permission to post in this channel.")
        response = self.api_post(
            sender, "/api/v1/scheduled_messages", {**payload, "topic": "Scheduled XY Topic"}
        )
        self.assert_json_error(
            response, "You do not have permission to create new topics in this channel."
        )
        self.assertFalse(ScheduledMessage.objects.exists())

        for author in [sender, bot]:
            response = self.api_post(author, "/api/v1/scheduled_messages", payload)
            job = ScheduledMessage.objects.get(
                id=self.assert_json_success(response)["scheduled_message_id"]
            )
            self.assertEqual(job.sender, author)
            self.assertEqual(job.topic_name(), topic_name.lower())
            self.assertEqual(job.stream, stream)

    def test_scheduled_channel_topic_policy_applies_on_create_and_edit(self) -> None:
        sender = self.example_user("hamlet")
        admin = self.example_user("iago")
        realm = sender.realm
        stream = get_stream("Verona", realm)
        do_set_stream_property(stream, "topics_policy", StreamTopicsPolicyEnum.inherit.value, admin)
        do_set_realm_property(
            realm, "topics_policy", RealmTopicsPolicyEnum.disable_empty_topic, acting_user=admin
        )
        endpoint = "/api/v1/scheduled_messages"
        payload = {
            "type": "channel",
            "to": str(stream.id),
            "topic": "(no topic)",
            "content": "Scheduled topic policy test",
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }
        nobody = NamedUserGroup.objects.get(
            name=SystemGroups.NOBODY, realm=realm, is_system_group=True
        )
        do_change_stream_group_based_setting(
            stream, "can_create_topic_group", nobody, acting_user=admin
        )
        self.assert_json_error(
            self.api_post(sender, endpoint, payload),
            "You do not have permission to create new topics in this channel.",
        )
        everyone = NamedUserGroup.objects.get(
            name=SystemGroups.EVERYONE, realm=realm, is_system_group=True
        )
        do_change_stream_group_based_setting(
            stream, "can_create_topic_group", everyone, acting_user=admin
        )
        self.assert_json_error(
            self.api_post(sender, endpoint, payload),
            "Sending messages to the general chat is not allowed in this channel.",
        )
        self.assertFalse(ScheduledMessage.objects.exists())

        do_set_stream_property(
            stream, "topics_policy", StreamTopicsPolicyEnum.allow_empty_topic.value, admin
        )
        job_id = self.assert_json_success(self.api_post(sender, endpoint, payload))[
            "scheduled_message_id"
        ]
        do_set_stream_property(
            stream, "topics_policy", StreamTopicsPolicyEnum.empty_topic_only.value, admin
        )
        self.assert_json_error(
            self.api_patch(
                sender, f"{endpoint}/{job_id}", {"topic": "named topic", "content": "Edited"}
            ),
            "Only the general chat topic is allowed in this channel.",
        )
        self.assert_json_success(
            self.api_patch(sender, f"{endpoint}/{job_id}", {"content": "Edited general chat"})
        )
        job = ScheduledMessage.objects.get(id=job_id)
        self.assertEqual(job.topic_name(), "")
        self.assertEqual(job.content, "Edited general chat")

    def test_scheduling_bot_channel_notices(self) -> None:
        owner = self.example_user("hamlet")
        bot = self.create_test_bot("scheduled-notices", owner)
        stream = self.make_stream("Empty scheduled channel")
        self.subscribe(self.example_user("othello"), stream.name)
        endpoint = "/api/v1/scheduled_messages"
        now = timezone_now()
        payload = {
            "type": "channel",
            "to": str(stream.id),
            "topic": "Scheduled notices",
            "content": "A scheduled message",
            "scheduled_delivery_timestamp": int(now.timestamp()) + 86400,
        }
        notice_sender = self.notification_bot(owner.realm)
        recipient = get_or_create_direct_message_group(
            id_list=[notice_sender.id, owner.id]
        ).recipient
        notices = Message.objects.filter(
            realm=owner.realm, sender=notice_sender, recipient=recipient
        )
        with time_machine.travel(now, tick=False):
            self.assert_json_success(self.api_post(bot, endpoint, payload))
            self.assertFalse(notices.exists())
            missing_id = stream.id + 10000
            self.assert_json_error(
                self.api_post(bot, endpoint, {**payload, "to": str(missing_id)}),
                f"Channel with ID '{missing_id}' does not exist",
            )
            self.assertIn(f"channel ID {missing_id}", notices.get().content)
            bot.refresh_from_db()
            self.assertEqual(bot.last_reminder, now)

        change_user_is_active(self.example_user("othello"), False)
        boundary = now + timedelta(minutes=UserProfile.BOT_OWNER_STREAM_ALERT_WAITPERIOD)
        with time_machine.travel(boundary, tick=False):
            self.assert_json_success(self.api_post(bot, endpoint, payload))
            self.assertEqual(notices.count(), 1)

        after_boundary = boundary + timedelta(microseconds=1)
        with time_machine.travel(after_boundary, tick=False):
            self.assert_json_success(self.api_post(bot, endpoint, payload))
            self.assertEqual(notices.count(), 2)
            self.assertIn("does not have any subscribers.", notices.latest("id").content)
            bot.refresh_from_db()
            self.assertEqual(bot.last_reminder, after_boundary)
        self.assertEqual(ScheduledMessage.objects.filter(sender=bot, stream=stream).count(), 3)

    def test_scheduled_channel_permission_is_rechecked_on_edit_and_delivery(self) -> None:
        sender = self.example_user("hamlet")
        admin = self.example_user("iago")
        stream = get_stream("Verona", sender.realm)
        members = NamedUserGroup.objects.get(name="hamletcharacters", realm=sender.realm)
        do_change_stream_group_based_setting(
            stream, "can_send_message_group", members, acting_user=admin
        )
        endpoint = "/api/v1/scheduled_messages"
        content = "Scheduled before the channel was archived"
        payload = {
            "type": "channel",
            "to": str(stream.id),
            "topic": "Archive during scheduling",
            "content": content,
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }
        job_id = self.assert_json_success(self.api_post(sender, endpoint, payload))[
            "scheduled_message_id"
        ]
        job = ScheduledMessage.objects.get(id=job_id)

        do_deactivate_stream(stream, acting_user=admin)
        self.assert_json_error(
            self.api_post(admin, endpoint, payload),
            "You do not have permission to post in this channel.",
        )
        archived_error = f"Not authorized to send to channel '{stream.name}'"
        self.assert_json_error(
            self.api_patch(sender, f"{endpoint}/{job_id}", {"content": "Edited"}), archived_error
        )
        job.refresh_from_db()
        self.assertEqual(job.content, content)

        with time_machine.travel(job.scheduled_timestamp, tick=False):
            self.verify_deliver_scheduled_message_failure(job, archived_error)
        self.assertFalse(job.delivered)
        self.assertFalse(
            Message.objects.filter(
                realm=sender.realm, content=content, recipient=stream.recipient
            ).exists()
        )

    def test_schedule_direct_message_authorization_and_history(self) -> None:
        sender = self.example_user("hamlet")
        admin = self.example_user("iago")
        other = self.example_user("othello")
        realm = sender.realm
        administrators = NamedUserGroup.objects.get(
            name=SystemGroups.ADMINISTRATORS, realm=realm, is_system_group=True
        )
        authorizers = self.create_or_update_anonymous_group_for_setting([], [administrators])
        initiators = self.create_or_update_anonymous_group_for_setting([admin], [])
        for setting, group in [
            ("direct_message_permission_group", authorizers),
            ("direct_message_initiator_group", initiators),
        ]:
            do_change_realm_permission_group_setting(realm, setting, group, acting_user=None)
        payload = {
            "type": "direct",
            "to": orjson.dumps([admin.id, other.id]).decode(),
            "content": "Scheduled reply",
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }

        response = self.api_post(
            sender,
            "/api/v1/scheduled_messages",
            {**payload, "to": orjson.dumps([other.id]).decode()},
        )
        self.assert_json_error(
            response, "This conversation does not include any users who can authorize it."
        )
        self.assert_json_error(
            self.api_post(sender, "/api/v1/scheduled_messages", payload),
            "You do not have permission to initiate direct message conversations.",
        )
        self.assertFalse(ScheduledMessage.objects.exists())

        response = self.api_post(
            admin,
            "/api/v1/scheduled_messages",
            {**payload, "to": orjson.dumps([sender.id, other.id]).decode()},
        )
        pending_job = ScheduledMessage.objects.get(
            id=self.assert_json_success(response)["scheduled_message_id"]
        )
        self.assert_json_error(
            self.api_post(sender, "/api/v1/scheduled_messages", payload),
            "You do not have permission to initiate direct message conversations.",
        )

        self.send_group_direct_message(admin, [sender, other])
        response = self.api_post(sender, "/api/v1/scheduled_messages", payload)
        job = ScheduledMessage.objects.get(
            id=self.assert_json_success(response)["scheduled_message_id"]
        )
        self.assertEqual(job.sender, sender)
        self.assertEqual(job.recipient, pending_job.recipient)

    def test_schedule_direct_message_role_permissions(self) -> None:
        sender = self.example_user("hamlet")
        admin = self.example_user("iago")
        other = self.example_user("othello")
        realm = sender.realm
        administrators = NamedUserGroup.objects.get(
            name=SystemGroups.ADMINISTRATORS, realm=realm, is_system_group=True
        )
        full_members = NamedUserGroup.objects.get(
            name=SystemGroups.FULL_MEMBERS, realm=realm, is_system_group=True
        )
        do_change_realm_permission_group_setting(
            realm, "direct_message_permission_group", administrators, acting_user=None
        )
        payload = {
            "type": "direct",
            "to": orjson.dumps([other.id]).decode(),
            "content": "Scheduled role test",
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }
        endpoint = "/api/v1/scheduled_messages"
        self.assert_json_error(
            self.api_post(sender, endpoint, payload),
            "This conversation does not include any users who can authorize it.",
        )
        do_change_realm_permission_group_setting(
            realm, "direct_message_initiator_group", administrators, acting_user=None
        )
        self.assert_json_error(
            self.api_post(sender, endpoint, {**payload, "to": orjson.dumps([admin.id]).decode()}),
            "You do not have permission to initiate direct message conversations.",
        )
        self.assert_json_success(self.api_post(admin, endpoint, payload))
        do_change_realm_permission_group_setting(
            realm, "direct_message_permission_group", full_members, acting_user=None
        )
        do_change_realm_permission_group_setting(
            realm, "direct_message_initiator_group", full_members, acting_user=None
        )
        do_set_realm_property(realm, "waiting_period_threshold", 10, acting_user=admin)
        now = timezone_now()
        with time_machine.travel(now, tick=False):
            sender.date_joined = now - timedelta(days=9, hours=23)
            sender.save(update_fields=["date_joined"])
            other.date_joined = now - timedelta(days=11)
            other.save(update_fields=["date_joined"])
            self.assert_json_error(
                self.api_post(sender, endpoint, payload),
                "You do not have permission to initiate direct message conversations.",
            )
            sender.date_joined = now - timedelta(days=10)
            sender.save(update_fields=["date_joined"])
            self.assert_json_success(self.api_post(sender, endpoint, payload))
        self.assertEqual(ScheduledMessage.objects.count(), 2)

    def test_schedule_direct_message_respects_guest_recipient_visibility(self) -> None:
        self.set_up_db_for_testing_user_access()
        guest = self.example_user("polonius")
        visible = self.example_user("hamlet")
        hidden = self.example_user("othello")
        payload = {
            "type": "direct",
            "to": orjson.dumps([visible.id, hidden.id]).decode(),
            "content": "Scheduled DM",
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }
        response = self.api_post(guest, "/api/v1/scheduled_messages", payload)
        self.assert_json_error(
            response, "You do not have permission to access some of the recipients."
        )
        self.assertFalse(ScheduledMessage.objects.exists())
        self.assertIsNone(get_direct_message_group([guest.id, visible.id, hidden.id]))

        self.send_personal_message(hidden, guest)
        response = self.api_post(guest, "/api/v1/scheduled_messages", payload)
        job = ScheduledMessage.objects.get(
            id=self.assert_json_success(response)["scheduled_message_id"]
        )
        group = get_direct_message_group([guest.id, visible.id, hidden.id])
        assert group is not None
        self.assertEqual(job.recipient_id, group.recipient_id)

    def test_schedule_message_validates_strict_json_arguments(self) -> None:
        owner = self.example_user("hamlet")
        payload = {
            "type": "channel",
            "to": str(self.get_stream_id("Verona")),
            "topic": "Strict JSON",
            "content": "Scheduled content",
            "scheduled_delivery_timestamp": str(int(time.time()) + 86400),
        }
        cases = [
            ("to", "[true]", 'to["int"] is not an integer'),
            ("to", "[1,", "to is not valid JSON"),
            (
                "scheduled_delivery_timestamp",
                "42.0",
                "scheduled_delivery_timestamp is not an integer",
            ),
            ("read_by_sender", "1", "read_by_sender is not a boolean"),
        ]
        for parameter, value, message in cases:
            with self.subTest(parameter=parameter, value=value):
                response = self.api_post(
                    owner, "/api/v1/scheduled_messages", {**payload, parameter: value}
                )
                self.assert_json_error(response, message)
        self.assertFalse(ScheduledMessage.objects.exists())

        for read_by_sender in (False, True):
            response = self.api_post(
                owner,
                "/api/v1/scheduled_messages",
                {**payload, "read_by_sender": orjson.dumps(read_by_sender).decode()},
            )
            data = self.assert_json_success(response)
            job = ScheduledMessage.objects.get(id=data["scheduled_message_id"])
            self.assertEqual(job.read_by_sender, read_by_sender)

    def test_schedule_message_recipient_shapes_and_aliases(self) -> None:
        self.login("hamlet")
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        channel = self.get_stream_id("Verona")
        payload = {
            "topic": "Recipient validation",
            "content": "**Scheduled content**",
            "scheduled_delivery_timestamp": str(int(time.time()) + 86400),
        }
        for recipient_type, to, error in [
            ("channel", f"[{channel}]", "Invalid data type for channel ID"),
            ("stream", "[]", "Invalid data type for channel ID"),
            ("direct", str(othello.id), "Invalid data type for recipients"),
            ("private", "[]", "Message must have recipients"),
        ]:
            with self.subTest(recipient_type=recipient_type, to=to):
                self.assert_json_error(
                    self.api_post(
                        hamlet,
                        "/api/v1/scheduled_messages",
                        {**payload, "type": recipient_type, "to": to},
                    ),
                    error,
                )
        invalid_ids = [999_999_999, 999_999_998, 999_999_999]
        self.assert_json_error(
            self.api_post(
                hamlet,
                "/api/v1/scheduled_messages",
                {**payload, "type": "direct", "to": orjson.dumps(invalid_ids).decode()},
            ),
            f"Invalid user ID {next(iter(set(invalid_ids)))}",
        )
        self.assertFalse(ScheduledMessage.objects.exists())

        for recipient_type, to, expected_type, expected_users in [
            ("stream", str(channel), Recipient.STREAM, []),
            ("channel", str(channel), Recipient.STREAM, []),
            (
                "private",
                f"[{othello.id},{othello.id}]",
                Recipient.DIRECT_MESSAGE_GROUP,
                [hamlet.id, othello.id],
            ),
            ("direct", f"[{hamlet.id},{hamlet.id}]", Recipient.DIRECT_MESSAGE_GROUP, [hamlet.id]),
        ]:
            with self.subTest(recipient_type=recipient_type, to=to):
                data = self.assert_json_success(
                    self.client_post(
                        "/json/scheduled_messages",
                        {**payload, "type": recipient_type, "to": to},
                        HTTP_USER_AGENT="Mozilla/5.0",
                    )
                )
                job = ScheduledMessage.objects.get(id=data["scheduled_message_id"])
                self.assertEqual(job.recipient.type, expected_type)
                self.assertEqual(job.rendered_content, "<p><strong>Scheduled content</strong></p>")
                if expected_type == Recipient.STREAM:
                    self.assertEqual(job.recipient.type_id, channel)
                else:
                    group = get_direct_message_group(expected_users)
                    assert group is not None
                    self.assertEqual(job.recipient_id, group.recipient_id)
                self.assertEqual(job.read_by_sender, expected_users != [hamlet.id])

    def test_scheduled_read_by_sender_default_depends_on_client_and_recipient(self) -> None:
        owner = self.example_user("hamlet")
        other = self.example_user("othello")
        for user_agent, recipient, read_by_sender in [
            ("curl/8.0", other, False),
            ("ZulipMobile/26.22.145 (iOS 10.3.1)", other, True),
            ("ZulipMobile/26.22.145 (iOS 10.3.1)", owner, False),
        ]:
            with self.subTest(user_agent=user_agent, recipient=recipient.id):
                response = self.api_post(
                    owner,
                    "/api/v1/scheduled_messages",
                    {
                        "type": "direct",
                        "to": f"[{recipient.id}]",
                        "content": "Scheduled content",
                        "scheduled_delivery_timestamp": str(int(time.time()) + 86400),
                    },
                    HTTP_USER_AGENT=user_agent,
                )
                job_id = self.assert_json_success(response)["scheduled_message_id"]
                self.assertEqual(
                    ScheduledMessage.objects.get(id=job_id).read_by_sender, read_by_sender
                )

    def test_schedule_message_delivery_time_precedes_recipient_lookup(self) -> None:
        owner = self.example_user("hamlet")
        timestamp = 2_000_000_000
        deliver_at = timestamp_to_datetime(timestamp)
        payload = {
            "type": "channel",
            "to": "[]",
            "topic": "Delivery time",
            "content": "Scheduled content",
            "scheduled_delivery_timestamp": str(timestamp),
        }
        endpoint = "/api/v1/scheduled_messages"
        with time_machine.travel(deliver_at, tick=False):
            self.assert_json_error(
                self.api_post(owner, endpoint, payload),
                "Scheduled delivery time must be in the future.",
            )
            self.assert_json_error(
                self.api_post(owner, endpoint, {**payload, "to": "[true]"}),
                'to["int"] is not an integer',
            )
            self.assertFalse(ScheduledMessage.objects.exists())

        with time_machine.travel(deliver_at - timedelta(microseconds=1), tick=False):
            self.assert_json_error(
                self.api_post(owner, endpoint, payload), "Invalid data type for channel ID"
            )
            data = self.assert_json_success(
                self.api_post(owner, endpoint, {**payload, "to": str(self.get_stream_id("Verona"))})
            )
            job = ScheduledMessage.objects.get(id=data["scheduled_message_id"])
            self.assertEqual(job.scheduled_timestamp, deliver_at)

        with time_machine.travel(deliver_at, tick=False):
            self.assert_json_error(
                self.api_patch(
                    owner, f"{endpoint}/{job.id}", {"scheduled_delivery_timestamp": str(timestamp)}
                ),
                "Scheduled delivery time must be in the future.",
            )
        job.refresh_from_db()
        self.assertEqual(job.scheduled_timestamp, deliver_at)

    def test_scheduled_batch_preserves_order_and_rolls_back_attachment_claims(self) -> None:
        self.login("hamlet")
        owner = self.example_user("hamlet")
        other = self.example_user("othello")
        client = get_client("website")
        stream = get_stream("Verona", owner.realm)
        attachment_file = StringIO("zulip")
        attachment_file.name = "schedule.txt"
        upload = self.assert_json_success(
            self.client_post("/json/user_uploads", {"file": attachment_file})
        )
        attachment = Attachment.objects.get(path_id=upload["url"].removeprefix("/user_uploads/"))
        later = timezone_now() + timedelta(days=2)
        earlier = later - timedelta(days=1)
        first = check_message(
            owner,
            client,
            Addressee.for_stream(stream, "Batch topic"),
            f"[Attachment](http://{owner.realm.host}{upload['url']})",
        )
        first.deliver_at = later
        second = check_message(owner, client, Addressee.for_user_profile(other), "Second message")
        second.deliver_at = earlier

        with (
            self.capture_send_event_calls(expected_num_events=0),
            mock.patch.object(
                ScheduledMessage, "to_dict", side_effect=ValueError("Serialization failed")
            ),
            self.assertRaisesRegex(ValueError, "Serialization failed"),
        ):
            do_schedule_messages([first, second], owner, delivery_type=ScheduledMessage.SEND_LATER)
        self.assertFalse(ScheduledMessage.objects.exists())
        self.assertFalse(attachment.scheduled_messages.exists())

        with self.capture_send_event_calls(expected_num_events=1) as events:
            ids = do_schedule_messages(
                [first, second],
                owner,
                read_by_sender=True,
                delivery_type=ScheduledMessage.SEND_LATER,
            )
        first_id, second_id = ids
        stored = ScheduledMessage.objects.in_bulk(ids)
        self.assertEqual(stored[first_id].scheduled_timestamp, later)
        self.assertEqual(stored[second_id].scheduled_timestamp, earlier)
        self.assertEqual(stored[first_id].stream_id, stream.id)
        self.assertIsNone(stored[second_id].stream_id)
        self.assertTrue(stored[first_id].has_attachment)
        self.assertFalse(stored[second_id].has_attachment)
        self.assertTrue(stored[first_id].read_by_sender)
        self.assertTrue(stored[second_id].read_by_sender)
        self.assertEqual(
            list(attachment.scheduled_messages.values_list("id", flat=True)), [first_id]
        )
        self.assertEqual(
            events,
            [
                {
                    "event": {
                        "type": "scheduled_messages",
                        "op": "add",
                        "scheduled_messages": [stored[id].to_dict() for id in ids],
                    },
                    "users": [owner.id],
                }
            ],
        )

    def create_scheduled_message(self) -> None:
        content = "Test message"
        scheduled_delivery_datetime = timezone_now() + timedelta(minutes=5)
        scheduled_delivery_timestamp = int(scheduled_delivery_datetime.timestamp())
        verona_stream_id = self.get_stream_id("Verona")
        result = self.do_schedule_message(
            "channel", verona_stream_id, f"{content} 1", scheduled_delivery_timestamp
        )
        self.assert_json_success(result)

    def assert_scheduled_message_delivered(
        self, scheduled_message: ScheduledMessage, recipient: Recipient | None
    ) -> None:
        sender = self.example_user("hamlet")

        # mock current time to be greater than the scheduled time, so that the `scheduled_message` can be sent.
        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )

        with (
            time_machine.travel(more_than_scheduled_delivery_datetime, tick=False),
            self.assertLogs(level="INFO") as logs,
        ):
            result = try_deliver_one_scheduled_message()
            self.assertTrue(result)
            self.assertEqual(
                logs.output,
                [
                    f"INFO:root:Sending scheduled message {scheduled_message.id} with date {scheduled_message.scheduled_timestamp} (sender: {scheduled_message.sender_id})"
                ],
            )
            scheduled_message.refresh_from_db()

            assert isinstance(scheduled_message.delivered_message_id, int)
            self.assertEqual(scheduled_message.delivered, True)
            self.assertEqual(scheduled_message.failed, False)
            self.assertEqual(scheduled_message.recipient, recipient)
            self.assertEqual(scheduled_message.sender, sender)

            delivered_message = Message.objects.get(id=scheduled_message.delivered_message_id)
            self.assertEqual(delivered_message.content, scheduled_message.content)
            self.assertEqual(delivered_message.rendered_content, scheduled_message.rendered_content)
            self.assertEqual(delivered_message.topic_name(), scheduled_message.topic_name())
            self.assertEqual(delivered_message.date_sent, more_than_scheduled_delivery_datetime)
            self.assertEqual(delivered_message.recipient, scheduled_message.recipient)
            self.assertEqual(delivered_message.sender, scheduled_message.sender)

            sender_user_message = UserMessage.objects.get(
                message_id=scheduled_message.delivered_message_id, user_profile_id=sender.id
            )
            self.assertEqual(
                sender_user_message.flags.read, not is_message_to_self(delivered_message)
            )

    def test_claim_due_jobs_in_id_order(self) -> None:
        self.create_scheduled_message()
        delivered = self.last_scheduled_message()
        self.create_scheduled_message()
        failed = self.last_scheduled_message()
        self.create_scheduled_message()
        first = self.last_scheduled_message()
        self.create_scheduled_message()
        second = self.last_scheduled_message()
        self.create_scheduled_message()
        future = self.last_scheduled_message()

        now = timezone_now()
        ScheduledMessage.objects.exclude(id=future.id).update(
            scheduled_timestamp=now - timedelta(minutes=1)
        )
        ScheduledMessage.objects.filter(id=second.id).update(
            scheduled_timestamp=now - timedelta(minutes=2)
        )
        ScheduledMessage.objects.filter(id=delivered.id).update(delivered=True)
        ScheduledMessage.objects.filter(id=failed.id).update(failed=True)
        with (
            time_machine.travel(now, tick=False),
            mock.patch("zerver.actions.scheduled_messages.send_scheduled_message") as send,
        ):
            self.assertTrue(try_deliver_one_scheduled_message())
            send.assert_called_once_with(first)

            ScheduledMessage.objects.filter(id=first.id).update(delivered=True)
            send.reset_mock()
            self.assertTrue(try_deliver_one_scheduled_message())
            send.assert_called_once_with(second)

            ScheduledMessage.objects.filter(id=second.id).update(delivered=True)
            send.reset_mock()
            self.assertFalse(try_deliver_one_scheduled_message())
            send.assert_not_called()

    def test_successful_deliver_stream_scheduled_message(self) -> None:
        # No scheduled message
        result = try_deliver_one_scheduled_message()
        self.assertFalse(result)

        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        stream_id = self.get_stream_id("Verona")
        recipient = Recipient.objects.get(type=Recipient.STREAM, type_id=stream_id)
        self.assert_scheduled_message_delivered(scheduled_message, recipient)

    def test_successful_deliver_direct_scheduled_message_to_other(
        self,
    ) -> None:
        # No scheduled message
        self.assertFalse(try_deliver_one_scheduled_message())

        content = "Test message"
        scheduled_delivery_datetime = timezone_now() + timedelta(minutes=5)
        scheduled_delivery_timestamp = int(scheduled_delivery_datetime.timestamp())
        sender = self.example_user("hamlet")
        othello = self.example_user("othello")
        response = self.do_schedule_message(
            "direct", [othello.id], f"{content} 3", scheduled_delivery_timestamp
        )
        self.assert_json_success(response)
        scheduled_message = self.last_scheduled_message()

        direct_message_group = get_or_create_direct_message_group(id_list=[sender.id, othello.id])
        self.assert_scheduled_message_delivered(
            scheduled_message, recipient=direct_message_group.recipient
        )

        # Check error is sent if an edit happens after the scheduled
        # message is successfully sent.
        new_delivery_datetime = timezone_now() + timedelta(minutes=7)
        new_delivery_timestamp = int(new_delivery_datetime.timestamp())
        content = "New message content"
        payload = {
            "content": content,
            "scheduled_delivery_timestamp": new_delivery_timestamp,
        }
        updated_response = self.client_patch(
            f"/json/scheduled_messages/{scheduled_message.id}", payload
        )
        self.assert_json_error(updated_response, "Scheduled message was already sent")

    def test_successful_deliver_direct_scheduled_message_to_self(
        self,
    ) -> None:
        # No scheduled message
        self.assertFalse(try_deliver_one_scheduled_message())

        content = "Test message to self"
        scheduled_delivery_datetime = timezone_now() + timedelta(minutes=5)
        scheduled_delivery_timestamp = int(scheduled_delivery_datetime.timestamp())
        sender = self.example_user("hamlet")

        # Create a direct message group for the sender.
        direct_message_group = get_or_create_direct_message_group(id_list=[sender.id])

        response = self.do_schedule_message(
            "direct", [sender.id], content, scheduled_delivery_timestamp
        )
        self.assert_json_success(response)
        scheduled_message = self.last_scheduled_message()

        self.assert_scheduled_message_delivered(
            scheduled_message, recipient=direct_message_group.recipient
        )

    def verify_deliver_scheduled_message_failure(
        self, scheduled_message: ScheduledMessage, expected_failure_message: str
    ) -> None:
        with self.assertLogs(level="INFO") as logs:
            result = try_deliver_one_scheduled_message()
        self.assertTrue(result)
        scheduled_message.refresh_from_db()
        self.assertEqual(scheduled_message.failure_message, expected_failure_message)
        self.assertEqual(
            logs.output,
            [
                f"INFO:root:Sending scheduled message {scheduled_message.id} with date {scheduled_message.scheduled_timestamp} (sender: {scheduled_message.sender_id})",
                f"INFO:root:Failed with message: {scheduled_message.failure_message}",
            ],
        )
        self.assertTrue(scheduled_message.failed)

    def test_too_late_to_deliver_scheduled_message(self) -> None:
        expected_failure_message = "Message could not be sent at the scheduled time."
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        too_late_to_send_message_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES + 1
        )

        with time_machine.travel(too_late_to_send_message_datetime, tick=False):
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

        # Verify that the user was sent a message informing them about
        # the failed scheduled message.
        realm = scheduled_message.realm
        msg = most_recent_message(scheduled_message.sender)
        self.assertEqual(msg.recipient.type, msg.recipient.DIRECT_MESSAGE_GROUP)
        self.assertEqual(msg.sender_id, self.notification_bot(realm).id)
        self.assertIn(expected_failure_message, msg.content)

    def test_realm_deactivated_failed_to_deliver_scheduled_message(self) -> None:
        expected_failure_message = "This organization has been deactivated"
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        # Verify realm isn't deactivated and get user's most recent
        # message.
        self.assertFalse(scheduled_message.realm.deactivated)
        message_before_deactivation = most_recent_message(scheduled_message.sender)

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )

        with time_machine.travel(more_than_scheduled_delivery_datetime, tick=False):
            scheduled_message = self.last_scheduled_message()
            scheduled_message.realm.deactivated = True
            scheduled_message.realm.save()
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

        # Verify that no failed scheduled message notification was sent.
        self.assertTrue(scheduled_message.realm.deactivated)
        message_after_deactivation = most_recent_message(scheduled_message.sender)
        self.assertEqual(message_after_deactivation.content, message_before_deactivation.content)
        self.assertNotIn(expected_failure_message, message_after_deactivation.content)

    def test_sender_deactivated_failed_to_deliver_scheduled_message(self) -> None:
        expected_failure_message = "Account is deactivated"
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        # Verify user isn't deactivated and get user's most recent
        # message.
        self.assertTrue(scheduled_message.sender.is_active)
        message_before_deactivation = most_recent_message(scheduled_message.sender)

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )

        with time_machine.travel(more_than_scheduled_delivery_datetime, tick=False):
            scheduled_message = self.last_scheduled_message()
            change_user_is_active(scheduled_message.sender, False)
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

        # Verify that no failed scheduled message notification was sent.
        self.assertFalse(scheduled_message.sender.is_active)
        message_after_deactivation = most_recent_message(scheduled_message.sender)
        self.assertEqual(message_after_deactivation.content, message_before_deactivation.content)
        self.assertNotIn(expected_failure_message, message_after_deactivation.content)

    def test_failed_to_deliver_scheduled_message_unknown_exception(self) -> None:
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )
        with (
            mock.patch(
                "zerver.actions.scheduled_messages.send_scheduled_message",
                side_effect=Exception(),
            ),
            time_machine.travel(more_than_scheduled_delivery_datetime, tick=False),
        ):
            scheduled_message = self.last_scheduled_message()
            with self.assertLogs(level="INFO") as logs:
                result = try_deliver_one_scheduled_message()
            self.assertTrue(result)
            scheduled_message.refresh_from_db()
            self.assert_length(logs.output, 2)
            self.assertEqual(
                logs.output[0],
                f"INFO:root:Sending scheduled message {scheduled_message.id} with date {scheduled_message.scheduled_timestamp} (sender: {scheduled_message.sender_id})",
            )
            self.assertTrue(
                logs.output[1].startswith(
                    f"ERROR:root:Unexpected error sending scheduled message {scheduled_message.id} (sent: {scheduled_message.delivered})\nTraceback (most recent call last)"
                )
            )
            self.assertTrue(scheduled_message.failed)

        # Verify that the user was sent a message informing them about
        # the failed scheduled message.
        realm = scheduled_message.realm
        msg = most_recent_message(scheduled_message.sender)
        self.assertEqual(msg.recipient.type, msg.recipient.DIRECT_MESSAGE_GROUP)
        self.assertEqual(msg.sender_id, self.notification_bot(realm).id)
        self.assertIn("Internal server error", msg.content)

    def test_removal_event_error_preserves_delivery(self) -> None:
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()
        delivery = scheduled_message.scheduled_timestamp + timedelta(minutes=1)
        with (
            time_machine.travel(delivery, tick=False),
            mock.patch(
                "zerver.actions.scheduled_messages.notify_remove_scheduled_message",
                side_effect=ValueError("Event creation failed"),
            ),
            mock.patch(
                "zerver.actions.scheduled_messages.send_failed_scheduled_message_notification"
            ) as notify_failure,
            self.assertLogs(level="ERROR"),
        ):
            self.assertTrue(try_deliver_one_scheduled_message())
            self.assertFalse(try_deliver_one_scheduled_message())
        notify_failure.assert_not_called()

        scheduled_message.refresh_from_db()
        self.assertTrue(scheduled_message.delivered)
        self.assertTrue(scheduled_message.failed)
        self.assertEqual(scheduled_message.failure_message, "Internal server error")
        self.assertIsNotNone(scheduled_message.delivered_message_id)
        assert scheduled_message.delivered_message is not None
        self.assertEqual(scheduled_message.delivered_message.content, scheduled_message.content)

    def test_failed_to_deliver_scheduled_message_from_system_bot(self) -> None:
        # Cross-realm system bots have no human reading their DMs, so
        # they should not receive a failure notification -- doing so
        # would leave a stray notification-bot <-> system-bot
        # DirectMessageGroup attached to no realm.
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()
        realm = scheduled_message.realm
        welcome_bot = get_system_bot(settings.WELCOME_BOT, realm.id)
        scheduled_message.sender = welcome_bot
        scheduled_message.save(update_fields=["sender"])

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )
        with (
            mock.patch(
                "zerver.actions.scheduled_messages.send_scheduled_message",
                side_effect=Exception(),
            ),
            time_machine.travel(more_than_scheduled_delivery_datetime, tick=False),
            self.assertLogs(level="INFO"),
        ):
            self.assertTrue(try_deliver_one_scheduled_message())
        scheduled_message.refresh_from_db()
        self.assertTrue(scheduled_message.failed)

        # No notification-bot -> welcome-bot message should have been
        # sent.  internal_prep_private_message lands such a message in
        # the sender's realm (the system bot realm) when the recipient
        # is a cross-realm bot, so scope the check there.
        notification_bot = self.notification_bot(realm)
        notify_group = get_or_create_direct_message_group(
            id_list=[welcome_bot.id, notification_bot.id]
        )
        self.assertFalse(
            Message.objects.filter(
                realm=notification_bot.realm, recipient=notify_group.recipient
            ).exists()
        )

    def test_editing_failed_send_scheduled_message(self) -> None:
        expected_failure_message = "Message could not be sent at the scheduled time."
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        too_late_to_send_message_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES + 1
        )

        with time_machine.travel(too_late_to_send_message_datetime, tick=False):
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

            # After verifying the scheduled message failed to be sent:
            # Confirm not updating the scheduled delivery timestamp for
            # the scheduled message with that ID returns an error.
            payload_without_timestamp = {"topic": "Failed to send"}
            response = self.client_patch(
                f"/json/scheduled_messages/{scheduled_message.id}", payload_without_timestamp
            )
            self.assert_json_error(response, "Scheduled delivery time must be in the future.")

        # Editing the scheduled message with that ID for a future time is
        # successful and resets the `failed` and `failure_message` fields.
        new_delivery_datetime = timezone_now() + timedelta(minutes=60)
        new_delivery_timestamp = int(new_delivery_datetime.timestamp())
        scheduled_message_id = scheduled_message.id
        payload_with_timestamp = {
            "scheduled_delivery_timestamp": new_delivery_timestamp,
        }
        response = self.client_patch(
            f"/json/scheduled_messages/{scheduled_message.id}", payload_with_timestamp
        )
        self.assert_json_success(response)

        scheduled_message = self.last_scheduled_message()
        self.assertEqual(scheduled_message.id, scheduled_message_id)
        self.assertFalse(scheduled_message.failed)
        self.assertIsNone(scheduled_message.failure_message)

    def test_scheduling_in_past(self) -> None:
        # Scheduling a message in past should fail.
        content = "Test message"
        verona_stream_id = self.get_stream_id("Verona")
        scheduled_delivery_timestamp = int(time.time() - 86400)

        result = self.do_schedule_message(
            "channel", verona_stream_id, f"{content} 1", scheduled_delivery_timestamp
        )
        self.assert_json_error(result, "Scheduled delivery time must be in the future.")

    def test_edit_checks_owner_before_recipient_shape(self) -> None:
        owner = self.example_user("hamlet")
        other = self.example_user("othello")
        response = self.do_schedule_message(
            "channel", self.get_stream_id("Verona"), "Original", int(time.time()) + 86400
        )
        job_id = self.assert_json_success(response)["scheduled_message_id"]
        endpoint = f"/api/v1/scheduled_messages/{job_id}"
        original = ScheduledMessage.objects.get(id=job_id).to_dict()

        with self.capture_send_event_calls(expected_num_events=0):
            self.assert_json_error(
                self.api_patch(other, endpoint, {"to": "[true]"}),
                'to["int"] is not an integer',
            )
            self.assert_json_error(
                self.api_patch(other, endpoint, {"to": "[]"}),
                "Scheduled message does not exist",
                status_code=404,
            )
            self.assert_json_error(
                self.api_patch(owner, endpoint, {"to": "[]"}),
                "Invalid data type for channel ID",
            )
            self.assert_json_error(
                self.api_patch(owner, endpoint, {"type": "private", "to": "[]"}),
                "Message must have recipients",
            )
        self.assertEqual(ScheduledMessage.objects.get(id=job_id).to_dict(), original)

    def test_edit_schedule_message(self) -> None:
        content = "Original test message"
        scheduled_delivery_timestamp = int(time.time() + 86400)
        verona_stream_id = self.get_stream_id("Verona")

        # Scheduling a message to a stream you are subscribed is successful.
        result = self.do_schedule_message(
            "channel", verona_stream_id, content, scheduled_delivery_timestamp
        )
        scheduled_message = self.last_scheduled_message()
        self.assert_json_success(result)
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.content, "Original test message")
        self.assertEqual(scheduled_message.topic_name(), "Test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )
        scheduled_message_id = scheduled_message.id
        payload: dict[str, Any]

        # Edit message with other stream message type ("stream") and no other changes
        # results in no changes to the scheduled message.
        payload = {
            "type": "stream",
            "to": orjson.dumps(verona_stream_id).decode(),
            "topic": "Test topic",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.stream_id, verona_stream_id)
        self.assertEqual(scheduled_message.content, "Original test message")
        self.assertEqual(scheduled_message.topic_name(), "Test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )

        # Sending request with only scheduled message ID returns an error
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}")
        self.assert_json_error(result, "Nothing to change")

        # Trying to edit only message `type` returns an error
        payload = {
            "type": "direct",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_error(
            result, "Recipient required when updating type of scheduled message."
        )

        # Edit message `type` with valid `to` parameter succeeds
        othello = self.example_user("othello")
        to = [othello.id]
        payload = {"type": "direct", "to": orjson.dumps(to).decode()}
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertNotEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.recipient.type, Recipient.DIRECT_MESSAGE_GROUP)
        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.topic_name(), Message.DM_TOPIC)

        # Trying to edit `topic` for direct message is ignored
        payload = {
            "topic": "Direct message topic",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.topic_name(), Message.DM_TOPIC)

        # Trying to edit `type` to stream message type without a `topic` returns an error
        payload = {
            "type": "channel",
            "to": orjson.dumps(verona_stream_id).decode(),
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_error(
            result, "Topic required when updating scheduled message type to channel."
        )

        # Edit message `type` to stream with valid `to` and `topic` succeeds
        payload = {
            "type": "channel",
            "to": orjson.dumps(verona_stream_id).decode(),
            "topic": "New test topic",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.topic_name(), "New test topic")

        # Trying to edit with timestamp in the past returns an error
        new_scheduled_delivery_timestamp = int(time.time() - 86400)
        payload = {
            "scheduled_delivery_timestamp": new_scheduled_delivery_timestamp,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_error(result, "Scheduled delivery time must be in the future.")

        # Edit content and time of scheduled message succeeds
        edited_content = "Edited test message"
        new_scheduled_delivery_timestamp = scheduled_delivery_timestamp + int(
            time.time() + (3 * 86400)
        )
        payload = {
            "content": edited_content,
            "scheduled_delivery_timestamp": new_scheduled_delivery_timestamp,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.content, edited_content)
        self.assertEqual(scheduled_message.topic_name(), "New test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(new_scheduled_delivery_timestamp),
        )

        # Edit topic and content of scheduled stream message
        edited_content = "Final content edit for test"
        payload = {
            "topic": "Another topic for test",
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(scheduled_message.content, edited_content)
        self.assertEqual(scheduled_message.topic_name(), "Another topic for test")

        # Edit only topic of scheduled stream message
        payload = {
            "topic": "Final topic for test",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.content, edited_content)
        self.assertEqual(scheduled_message.topic_name(), "Final topic for test")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(new_scheduled_delivery_timestamp),
        )

    def test_fetch_scheduled_messages(self) -> None:
        self.login("hamlet")
        # No scheduled message
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        self.assert_length(orjson.loads(result.content)["scheduled_messages"], 0)

        verona_stream_id = self.get_stream_id("Verona")
        content = "Test message"
        scheduled_delivery_timestamp = int(time.time() + 86400)
        self.do_schedule_message("channel", verona_stream_id, content, scheduled_delivery_timestamp)

        # Single scheduled message
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        scheduled_messages = orjson.loads(result.content)["scheduled_messages"]

        self.assert_length(scheduled_messages, 1)
        self.assertEqual(
            scheduled_messages[0]["scheduled_message_id"], self.last_scheduled_message().id
        )
        self.assertEqual(scheduled_messages[0]["content"], content)
        self.assertEqual(scheduled_messages[0]["to"], verona_stream_id)
        self.assertEqual(scheduled_messages[0]["type"], "stream")
        self.assertEqual(scheduled_messages[0]["topic"], "Test topic")
        self.assertEqual(
            scheduled_messages[0]["scheduled_delivery_timestamp"], scheduled_delivery_timestamp
        )

        othello = self.example_user("othello")
        result = self.do_schedule_message(
            "direct", [othello.id], f"{content} 3", scheduled_delivery_timestamp
        )

        # Multiple scheduled messages
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        self.assert_length(orjson.loads(result.content)["scheduled_messages"], 2)

        # Check if another user can access these scheduled messages.
        self.logout()
        self.login("othello")
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        self.assert_length(orjson.loads(result.content)["scheduled_messages"], 0)

    def test_delete_scheduled_messages(self) -> None:
        self.login("hamlet")

        content = "Test message"
        verona_stream_id = self.get_stream_id("Verona")
        scheduled_delivery_timestamp = int(time.time() + 86400)

        self.do_schedule_message("channel", verona_stream_id, content, scheduled_delivery_timestamp)
        scheduled_message = self.last_scheduled_message()
        self.logout()

        # Other user cannot delete it.
        othello = self.example_user("othello")
        result = self.api_delete(othello, f"/api/v1/scheduled_messages/{scheduled_message.id}")
        self.assert_json_error(result, "Scheduled message does not exist", 404)

        self.login("hamlet")
        result = self.client_delete(f"/json/scheduled_messages/{scheduled_message.id}")
        self.assert_json_success(result)

        # Already deleted.
        result = self.client_delete(f"/json/scheduled_messages/{scheduled_message.id}")
        self.assert_json_error(result, "Scheduled message does not exist", 404)

    def test_scheduled_attachment_keeps_existing_visibility_and_other_jobs(self) -> None:
        owner = self.example_user("hamlet")
        other = self.example_user("othello")
        stream = get_stream("Verona", owner.realm)
        path_id = self.create_attachment_helper(owner)
        attachment = Attachment.objects.get(path_id=path_id.removeprefix("/user_uploads/"))
        payload = {
            "type": "channel",
            "to": str(stream.id),
            "topic": "Scheduled attachment",
            "content": f"[Upload](http://{owner.realm.host}{path_id})",
            "scheduled_delivery_timestamp": int(time.time()) + 86400,
        }
        ids = [
            self.assert_json_success(self.api_post(owner, "/api/v1/scheduled_messages", payload))[
                "scheduled_message_id"
            ]
            for _ in range(2)
        ]
        unclaimed = self.assert_json_success(
            self.api_post(other, "/api/v1/scheduled_messages", payload)
        )["scheduled_message_id"]

        self.assertTrue(ScheduledMessage.objects.get(id=ids[0]).has_attachment)
        self.assertFalse(ScheduledMessage.objects.get(id=unclaimed).has_attachment)
        attachment.refresh_from_db()
        self.assertFalse(attachment.is_realm_public)
        self.assertFalse(attachment.is_web_public)
        self.assertCountEqual(attachment.scheduled_messages.values_list("id", flat=True), ids)

        response = self.api_patch(
            owner, f"/api/v1/scheduled_messages/{ids[0]}", {"content": "No upload"}
        )
        self.assert_json_success(response)
        self.assertFalse(ScheduledMessage.objects.get(id=ids[0]).has_attachment)
        self.assertEqual(list(attachment.scheduled_messages.values_list("id", flat=True)), ids[1:])

    def test_attachment_handling(self) -> None:
        self.login("hamlet")
        hamlet = self.example_user("hamlet")
        verona_stream_id = self.get_stream_id("Verona")

        attachment_file1 = StringIO("zulip!")
        attachment_file1.name = "dummy_1.txt"
        result = self.client_post("/json/user_uploads", {"file": attachment_file1})
        path_id1 = re.sub(r"/user_uploads/", "", result.json()["url"])
        attachment_object1 = Attachment.objects.get(path_id=path_id1)

        attachment_file2 = StringIO("zulip!")
        attachment_file2.name = "dummy_1.txt"
        result = self.client_post("/json/user_uploads", {"file": attachment_file2})
        path_id2 = re.sub(r"/user_uploads/", "", result.json()["url"])
        attachment_object2 = Attachment.objects.get(path_id=path_id2)

        content = f"Test [zulip.txt](http://{hamlet.realm.host}/user_uploads/{path_id1})"
        scheduled_delivery_timestamp = int(time.time() + 86400)

        # Test sending with attachment
        self.do_schedule_message("channel", verona_stream_id, content, scheduled_delivery_timestamp)
        scheduled_message = self.last_scheduled_message()
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)),
            [scheduled_message.id],
        )
        self.assertEqual(scheduled_message.has_attachment, True)

        # Test editing to change attachmment
        edited_content = f"Test [zulip.txt](http://{hamlet.realm.host}/user_uploads/{path_id2})"
        payload = {
            "content": edited_content,
        }
        with (
            self.capture_send_event_calls(expected_num_events=0),
            mock.patch.object(
                ScheduledMessage, "to_dict", side_effect=ValueError("Serialization failed")
            ),
            self.assertRaisesRegex(ValueError, "Serialization failed"),
        ):
            edit_scheduled_message(
                hamlet,
                get_client("website"),
                scheduled_message.id,
                None,
                None,
                None,
                edited_content,
                None,
                hamlet.realm,
            )
        scheduled_message.refresh_from_db()
        self.assertEqual(scheduled_message.content, content)
        self.assertTrue(
            attachment_object1.scheduled_messages.filter(id=scheduled_message.id).exists()
        )
        self.assertFalse(attachment_object2.scheduled_messages.exists())

        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(
            list(attachment_object2.scheduled_messages.all().values_list("id", flat=True)),
            [scheduled_message.id],
        )
        self.assertEqual(scheduled_message.has_attachment, True)

        # Test editing to no longer reference any attachments
        edited_content = "No more attachments"
        payload = {
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(
            list(attachment_object2.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(scheduled_message.has_attachment, False)

        # Test editing to now have an attachment again
        edited_content = (
            f"Attachment is back! [zulip.txt](http://{hamlet.realm.host}/user_uploads/{path_id2})"
        )
        payload = {
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(
            list(attachment_object2.scheduled_messages.all().values_list("id", flat=True)),
            [scheduled_message.id],
        )
        self.assertEqual(scheduled_message.has_attachment, True)


@skipUnless(os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"), "RabbitMQ test vhost is unavailable")
class ScheduledJobDeleteHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_reminder_delete_parity(self) -> None:
        self.check_delete_parity(
            endpoint="reminders",
            id_field="reminder_id",
            target_type=ScheduledMessage.REMIND,
            other_type=ScheduledMessage.SEND_LATER,
        )

    def test_scheduled_message_delete_parity(self) -> None:
        self.check_delete_parity(
            endpoint="scheduled_messages",
            id_field="scheduled_message_id",
            target_type=ScheduledMessage.SEND_LATER,
            other_type=ScheduledMessage.REMIND,
        )

    def check_delete_parity(
        self, *, endpoint: str, id_field: str, target_type: int, other_type: int
    ) -> None:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(settings.RABBITMQ_USERNAME, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        owner = self.example_user("hamlet")
        other = self.example_user("othello")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None

        def basic(user: UserProfile) -> dict[str, str]:
            return {
                "Authorization": "Basic "
                + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
            }

        session = SessionStore()
        session.update(
            {
                "_auth_user_id": str(owner.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": owner.get_session_auth_hash(),
            }
        )
        session.create()
        token = "a" * 32
        cookie = (
            f"{settings.SESSION_COOKIE_NAME}={session.session_key}; "
            f"{settings.CSRF_COOKIE_NAME}={token}"
        )
        attachment = Attachment.objects.create(
            owner=owner,
            realm=owner.realm,
            file_name="scheduled-job.txt",
            path_id="scheduled-job-parity",
            size=8,
        )
        scheduled_job = ScheduledMessage.objects.filter(
            sender__realm=owner.realm, delivery_type=ScheduledMessage.SEND_LATER
        ).first()
        assert scheduled_job is not None
        fields = dict(
            sender=owner,
            recipient=scheduled_job.recipient,
            realm=owner.realm,
            content="Please remind me",
            rendered_content="<p>Please remind me</p>",
            sending_client=get_client("website"),
            scheduled_timestamp=timezone_now() + timedelta(days=1),
            read_by_sender=False,
        )
        victim_id = 2**31 + 1
        scheduled = ScheduledMessage.objects.create(delivery_type=other_type, **fields)
        attachment.scheduled_messages.add(scheduled)
        try:
            with (
                self.settings(
                    USING_RABBITMQ=True,
                    RABBITMQ_VHOST=vhost,
                    TORNADO_PROCESSES=1,
                    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
                ),
                pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
                TemporaryDirectory() as directory,
            ):
                channel = broker.channel()
                for name in ("notify_tornado", "user_activity"):
                    channel.queue_declare(name, durable=True)
                    channel.queue_purge(name)
                secret = Path(directory) / "secret"
                secret.write_text(settings.SECRET_KEY)
                environment = {
                    (
                        "ZULIP_RUST_REMINDER_DELETES"
                        if target_type == ScheduledMessage.REMIND
                        else "ZULIP_RUST_SCHEDULED_MESSAGE_DELETES"
                    ): "1",
                    "ZULIP_RUST_AMQP_URL": amqp_url,
                    "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                    "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                        settings.AUTHENTICATION_BACKENDS
                    ).decode(),
                }

                def create_victim() -> None:
                    job = ScheduledMessage.objects.create(
                        id=victim_id,
                        delivery_type=target_type,
                        failed=True,
                        delivered=target_type == ScheduledMessage.SEND_LATER,
                        has_attachment=True,
                        **fields,
                    )
                    attachment.scheduled_messages.add(job)

                event = {"type": endpoint, "op": "remove", id_field: victim_id}

                def check(
                    port: int,
                    prefix: str,
                    auth: dict[str, str],
                    csrf_failure: dict[str, str] | None = None,
                ) -> list[tuple[int, bytes]]:
                    create_victim()
                    cases = [
                        (scheduled.id, auth, 404, ""),
                        (2**63, auth, 404, ""),
                    ]
                    if prefix.startswith("/api/"):
                        cases.append((victim_id, basic(other), 404, ""))
                    if csrf_failure is not None:
                        cases.append((victim_id, csrf_failure, 403, ""))
                    cases += [
                        (victim_id, auth, 200, f"?unused=yes&{id_field}={scheduled.id}"),
                        (victim_id, auth, 404, ""),
                    ]
                    outcomes = []
                    pending = True
                    for job_id, headers, expected, query in cases:
                        status, body, _, _ = request_test_http(
                            port, "DELETE", owner.realm.host, headers, f"{prefix}/{job_id}{query}"
                        )
                        self.assertEqual(status, expected, body)
                        outcomes.append((status, body))
                        _, _, notification = channel.basic_get("notify_tornado", auto_ack=True)
                        if status == 200:
                            pending = False
                            assert notification is not None
                            self.assertEqual(
                                orjson.loads(notification), {"event": event, "users": [owner.id]}
                            )
                            self.assertEqual(
                                orjson.loads(body)["ignored_parameters_unsupported"],
                                [id_field, "unused"],
                            )
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                        else:
                            self.assertIsNone(notification)
                        self.assertEqual(
                            ScheduledMessage.objects.filter(id=victim_id).exists(), pending
                        )
                        self.assertTrue(Attachment.objects.filter(id=attachment.id).exists())
                        self.assertCountEqual(
                            attachment.scheduled_messages.values_list("id", flat=True),
                            [scheduled.id, victim_id] if pending else [scheduled.id],
                        )
                    return outcomes

                for scheme in ("http", "https"):
                    web_auth = {"Cookie": cookie, "X-CSRFToken": token}
                    if scheme == "https":
                        web_auth.update(
                            {
                                "X-Forwarded-Proto": "https",
                                "Referer": f"https://{owner.realm.host}/",
                            }
                        )
                    else:
                        web_auth["Origin"] = f"http://{owner.realm.host}"
                    no_token = {
                        key: value for key, value in web_auth.items() if key != "X-CSRFToken"
                    }
                    routes: list[tuple[str, dict[str, str], dict[str, str] | None]] = [
                        (f"/json/{endpoint}", web_auth, no_token)
                    ]
                    if scheme == "http":
                        routes.append((f"/api/v1/{endpoint}", basic(owner), None))
                    service = (
                        rust_http_service({**environment, "ZULIP_RUST_WEB_POST_SCHEME": scheme})
                        if os.environ.get("ZULIP_RUST_HTTP_BIN")
                        else nullcontext(None)
                    )
                    with service as rust_port:
                        for prefix, auth, csrf_failure in routes:
                            with self.subTest(scheme=scheme, path=prefix):
                                baseline = check(django_port, prefix, auth, csrf_failure)
                                if rust_port is not None:
                                    self.assertEqual(
                                        check(rust_port, prefix, auth, csrf_failure), baseline
                                    )
                        if rust_port is not None and scheme == "http":
                            create_victim()
                            with (
                                rust_http_service(environment) as second_port,
                                ThreadPoolExecutor(max_workers=2) as requests,
                            ):
                                pending = [
                                    requests.submit(
                                        request_test_http,
                                        port,
                                        "DELETE",
                                        owner.realm.host,
                                        basic(owner),
                                        f"/api/v1/{endpoint}/{victim_id}",
                                    )
                                    for port in (rust_port, second_port)
                                ]
                                self.assertCountEqual(
                                    [request.result()[0] for request in pending], [200, 404]
                                )
                            self.assertFalse(ScheduledMessage.objects.filter(id=victim_id).exists())
                            self.assertEqual(
                                list(attachment.scheduled_messages.values_list("id", flat=True)),
                                [scheduled.id],
                            )
                            notification = channel.basic_get("notify_tornado", auto_ack=True)[2]
                            assert notification is not None
                            self.assertEqual(
                                orjson.loads(notification), {"event": event, "users": [owner.id]}
                            )
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
        finally:
            attachment.delete()
            ScheduledMessage.objects.filter(id__in=[victim_id, scheduled.id]).delete()
            session.delete()

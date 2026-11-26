from unittest.mock import patch

from django.test import SimpleTestCase

from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib import stream_color
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Subscription


class ChannelStockColorContractTest(SimpleTestCase):
    def test_stock_color_order_duplicate_keys_and_original_values(self) -> None:
        key = int("10001")
        duplicate = int("10001")
        custom = "#123abc"
        used = set(stream_color.STREAM_ASSIGNMENT_COLORS[2:])
        explicit = {key: custom}
        recipient_ids = [key, 10003, 10002, duplicate, 10002]
        reference = stream_color._pick_colors_python(used, explicit, recipient_ids)
        actual = stream_color.pick_colors(used, explicit, recipient_ids)
        backend = stream_color._rust_stock_colors
        if getattr(backend, "CHANNEL_STOCK_COLORS_API", None) == 1:
            self.assertEqual(
                backend.pick_exact_stock_channel_colors(
                    used, explicit, recipient_ids, stream_color.STREAM_ASSIGNMENT_COLORS
                ),
                actual,
            )
        self.assertEqual(list(actual.items()), list(reference.items()))
        self.assertIs(next(iter(actual)), key)
        self.assertIs(actual[key], custom)
        for assigned in actual.values():
            if assigned != custom:
                self.assertIs(
                    assigned,
                    next(
                        color
                        for color in stream_color.STREAM_ASSIGNMENT_COLORS
                        if color == assigned
                    ),
                )

    def test_nonstandard_palette_and_arbitrary_int_stay_compatible(self) -> None:
        with patch.object(stream_color, "STREAM_ASSIGNMENT_COLORS", ["local-color"]):
            self.assertEqual(
                stream_color.pick_colors(set(), {}, [2**80, -2]),
                {-2: "local-color", 2**80: "local-color"},
            )
        self.assertEqual(
            stream_color.pick_colors(set(), {}, [2**80, -2]),
            stream_color._pick_colors_python(set(), {}, [2**80, -2]),
        )


class ChannelStockColorSubscriptionTest(ZulipTestCase):
    def test_new_subscription_persists_the_same_automatic_color(self) -> None:
        user = self.example_user("hamlet")
        stream = self.make_stream("synthetic-automatic-channel-color", realm=user.realm)
        with patch("zerver.actions.streams.pick_colors", wraps=stream_color.pick_colors) as choose:
            bulk_add_subscriptions(user.realm, [stream], [user], acting_user=None)
        used, explicit, ids = choose.call_args.args
        expected = stream_color._pick_colors_python(used, explicit, ids)
        saved = Subscription.objects.get(user_profile=user, recipient=stream.recipient)
        self.assertEqual(saved.color, expected[stream.recipient_id])

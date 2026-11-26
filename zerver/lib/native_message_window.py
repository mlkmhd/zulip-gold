"""Native window assembly for Django-selected, already sorted visible rows."""

from collections.abc import Sequence
from typing import cast

from zerver.lib.native_backend import load_native_contract

_backend = load_native_contract("ZULIP_RUST_MESSAGE_WINDOW", "VISIBLE_MESSAGE_WINDOW_API")


def plan(
    visible_rows: Sequence[Sequence[object]],
    anchor: int,
    before: int,
    after: int,
    anchored_to_right: bool,
) -> tuple[int, int, int, int] | None:
    native = getattr(_backend, "plan_sorted_message_window", None)
    if not callable(native) or before < 0 or after < 0:
        return None
    # Bound counts before crossing usize; Django still uses the original
    # request values when reporting completeness.
    length = len(visible_rows)
    return cast(
        "tuple[int, int, int, int] | None",
        native(visible_rows, anchor, min(before, length), min(after, length), anchored_to_right),
    )

"""Optional window assembly for Django-selected, already sorted visible rows."""

import os
from collections.abc import Sequence
from importlib import import_module
from typing import cast

try:
    _backend = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_MESSAGE_WINDOW") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


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

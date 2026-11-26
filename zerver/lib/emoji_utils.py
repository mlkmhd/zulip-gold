# This file doesn't import from django so that we can use it in `build_emoji`.
from importlib import import_module
from os import environ
from typing import cast

from zerver.lib.native_backend import load_native_contract

_rust_emoji_selector = load_native_contract(
    "ZULIP_RUST_EMOJI_PRESENTATION_SELECTOR", "EMOJI_PRESENTATION_SELECTOR_API"
)
_rust_emoji_encoder = load_native_contract("ZULIP_RUST_EMOJI_HEX_LABEL", "EMOJI_HEX_LABEL_API")

try:
    _backend = (
        import_module("_zulip_backend")
        if environ.get("ZULIP_RUST_EMOJI_CODEPOINTS") == "1"
        else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _backend = None


def _convert(name: str, text: str) -> str | None:
    if _backend is None:
        return None
    native = getattr(_backend, name, None)
    return cast("str | None", native(text)) if callable(native) else None


def unqualify_emoji(emoji: str) -> str:
    # Starting from version 4.0.0, `emoji_datasource` package has started to
    # add an emoji presentation variation selector for certain emojis which
    # have defined variation sequences. The emoji presentation selector
    # "qualifies" an emoji, and an "unqualified" version of an emoji does
    # not have an emoji presentation selector.
    #
    # Since in informal environments(like texting and chat), it is more
    # appropriate for an emoji to have a colorful display so until emoji
    # characters have a text presentation selector, it should have a
    # colorful display. Hence we can continue using emoji characters
    # without appending emoji presentation selector.
    # (http://unicode.org/reports/tr51/index.html#Presentation_Style)
    unqualify = getattr(_rust_emoji_selector, "unqualify_exact_emoji_text", None)
    if unqualify is not None and type(emoji) is str:
        result = unqualify(emoji)
        if result is not None:
            return cast(str, result)
    native = _convert("unqualify_emoji_text", emoji)
    return emoji.replace("\ufe0f", "") if native is None else native


def emoji_to_hex_codepoint(emoji: str) -> str:
    encode = getattr(_rust_emoji_encoder, "encode_exact_emoji_codepoints", None)
    if encode is not None and type(emoji) is str:
        result = encode(emoji)
        if result is not None:
            return cast(str, result)
    native = _convert("encode_emoji_codepoints", emoji)
    return "-".join(f"{ord(c):04x}" for c in emoji) if native is None else native


def hex_codepoint_to_emoji(hex: str) -> str:
    native = _convert("decode_emoji_codepoints", hex)
    return "".join(chr(int(h, 16)) for h in hex.split("-")) if native is None else native

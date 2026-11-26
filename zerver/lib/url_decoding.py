# See the Zulip URL spec at https://zulip.com/api/zulip-urls

from typing import cast
from urllib.parse import unquote, urlsplit

from django.conf import settings

from zerver.lib.narrow_helpers import NarrowTerm
from zerver.lib.native_backend import load_native_contract
from zerver.lib.topic import DB_TOPIC_NAME

_rust_narrow_recipient_slug = load_native_contract(
    "ZULIP_RUST_NARROW_RECIPIENT_SLUG", "NARROW_RECIPIENT_SLUG_API"
)
_rust_narrow_fragment_text = load_native_contract(
    "ZULIP_RUST_NARROW_FRAGMENT_TEXT", "NARROW_FRAGMENT_TEXT_API"
)
_rust_narrow_term_grammar = load_native_contract(
    "ZULIP_RUST_NARROW_TERM_GRAMMAR", "NARROW_TERM_GRAMMAR_API"
)


def is_same_server_message_link(url: str) -> bool:
    split_result = urlsplit(url)
    hostname = split_result.hostname
    fragment = split_result.fragment

    if hostname not in {None, settings.EXTERNAL_HOST_WITHOUT_PORT}:
        return False

    # A message link always has category `narrow`, section `channel`
    # or `dm`, and ends with `/near/<message_id>`, where <message_id>
    # is a sequence of digits. The URL fragment of a message link has
    # at least 5 parts. e.g. '#narrow/dm/9,15-dm/near/43'
    fragment_parts = fragment.split("/")
    if len(fragment_parts) < 5:
        return False

    category = fragment_parts[0]
    section = fragment_parts[1]
    ends_with_near_message_id = fragment_parts[-2] == "near" and fragment_parts[-1].isdigit()

    return category == "narrow" and section in {"channel", "dm"} and ends_with_near_message_id


CHANNEL_SYNONYMS = {"stream": "channel", "streams": "channels"}

OPERATOR_SYNONYMS = {
    **CHANNEL_SYNONYMS,
    # "pm-with:" was renamed to "dm:"
    "pm-with": "dm",
    # "group-pm-with:" was replaced with "dm-including:"
    "group-pm-with": "dm-including",
    "from": "sender",
    DB_TOPIC_NAME: "topic",
}


def canonicalize_operator_synonyms(text: str) -> str:
    text = text.lower()
    if text in OPERATOR_SYNONYMS.values():
        return text
    if text in OPERATOR_SYNONYMS:
        return OPERATOR_SYNONYMS[text]
    return text


def parse_recipient_slug(slug: str) -> tuple[int | list[int], str] | None:
    """
    Parses operands formatted in slug containing object ID or IDs.
    Typical of "channel" or private message operands.

    Doesn't parse the legacy pre-2018 stream slug format, which would
    require using data for what channels exist for a proper parse.
        e.g. "stream-name"

    Returns a tuple of parsed ids and the recipient info (channel name,
    DM'ed users name, etc) or only `None` if the operand is invalid.
        e.g.
        - "12,13,14-group" -> ([12, 13, 14], "group")
        - "89-Markl" -> (89, "Markl")
        - "stream-name" -> None
    """
    native = getattr(_rust_narrow_recipient_slug, "parse_narrow_recipient_slug", None)
    if callable(native):
        parsed = cast("tuple[list[int], str] | None", native(slug))
        if parsed is not None:
            ids, suffix = parsed
            return (ids if len(ids) > 1 else ids[0], suffix)

    try:
        ids_string, suffix = slug.split("-", maxsplit=1)
        ids = [int(id) for id in ids_string.split(",")]
        return (ids if len(ids) > 1 else ids[0], suffix)
    except ValueError:
        # We expect this to happen both for invalid URLs and legacy
        # pre-2018 channel link URLs that don't have a channel ID in
        # the slug.
        return None


def decode_hash_component(string: str) -> str:
    # This matches the web app's implementation of decodeHashComponent.
    native = getattr(_rust_narrow_fragment_text, "decode_narrow_hash_component", None)
    if callable(native):
        decoded = cast("str | None", native(string))
        if decoded is not None:
            return decoded
    return unquote(string.replace(".", "%"))


def decode_narrow_operand(operator: str, operand: str) -> str | int | list[int]:
    # These have the similar slug formatting for their operands which
    # contain object ID(s).
    if operator in ["dm-including", "dm", "mentions", "sender", "channel"]:
        result = parse_recipient_slug(operand)
        return result[0] if isinstance(result, tuple) else ""

    if operator == "near":
        return int(operand) if operand.isdigit() else ""

    operand = decode_hash_component(operand).strip()

    return operand


def parse_narrow_url(
    narrow_url: str,
) -> list[NarrowTerm] | None:
    """This server implementation is intended to match the algorithm
    for the web app's `parse_narrow` in `hash_util.ts`. It largely
    behaves the same way and has the same purpose: to parse a narrow
    URL into a list of `NarrowTerm`.

    The key difference from the web app implementation is that this
    does not validate the referenced objects (users and channels).
    """
    split_result = urlsplit(narrow_url)
    fragment = split_result.fragment
    native = getattr(_rust_narrow_term_grammar, "parse_prepared_narrow_terms", None)
    if callable(native):
        prepared = cast(
            "list[tuple[str, str | int | list[int], bool]] | None",
            native(fragment, OPERATOR_SYNONYMS),
        )
        if prepared is not None:
            return [
                NarrowTerm(operator, operand, negated) for operator, operand, negated in prepared
            ]

    fragment_parts = fragment.split("/")

    terms: list[NarrowTerm] = []

    for i in range(1, len(fragment_parts), 2):
        raw_operator = decode_hash_component(fragment_parts[i]).strip()

        if not raw_operator:
            return None

        negated = False
        if raw_operator.startswith("-"):
            negated = True
            raw_operator = raw_operator[1:]
        operator = canonicalize_operator_synonyms(raw_operator)

        try:
            raw_operand = fragment_parts[i + 1]
        except IndexError:
            raw_operand = ""
        operand = decode_narrow_operand(operator, raw_operand)

        if operand == "" and operator != "topic":
            # The empty string is a valid topic (realm_empty_topic_display_name).
            #
            # Other empty string operands are invalid.
            return None

        terms.append(NarrowTerm(operator, operand, negated))
    return terms

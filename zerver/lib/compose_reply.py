# The web app composes the same quote-and-reply format in
# web/src/compose_reply.ts; keep the two in sync.
from dataclasses import dataclass
from importlib import import_module
from os import environ
from typing import cast

from django.conf import settings
from django.utils.translation import gettext_lazy

from zerver.lib.markdown.fenced_code import close_unclosed_fences, get_unused_fence
from zerver.lib.message import truncate_content
from zerver.lib.native_backend import load_native_contract

CHANNEL_MESSAGE_QUOTE_CONTEXT = gettext_lazy(
    "{user_silent_mention} [said]({conversation_url}) in {topic_pretty_link}:"
)
DIRECT_MESSAGE_QUOTE_CONTEXT = gettext_lazy(
    "{user_silent_mention} [said]({conversation_url}) to {list_of_recipient_mentions}:"
)
MESSAGE_QUOTE_BODY = "{fence}quote\n{content}\n{fence}"
QUOTE_AND_REPLY_TEMPLATE = "{quote_context}\n{quote_body}\n{reply_content}"
QUOTE_CONTENT_MINIMUM_LENGTH = 1000
TRUNCATION_MESSAGE = "\n[message truncated]"

_python_close_unclosed_fences = close_unclosed_fences
_rust_exact_compose_quote = load_native_contract(
    "ZULIP_RUST_COMPOSE_QUOTE_BUDGET", "COMPOSE_QUOTE_BUDGET_API"
)
try:
    _rust_compose_quote = (
        import_module("_zulip_backend") if environ.get("ZULIP_RUST_COMPOSE_QUOTE") == "1" else None
    )
except ModuleNotFoundError as error:
    if error.name != "_zulip_backend":
        raise
    _rust_compose_quote = None


@dataclass
class MaxContentLengths:
    reply_content: int
    quoted_content: int


def get_measurements_for_quote_and_reply_content(
    reply_content_length: int,
    quote_content_length: int,
    template_syntax_length: int,
) -> MaxContentLengths:
    """Splits the message length limit between a reply and the message it
    quotes. The quote is guaranteed QUOTE_CONTENT_MINIMUM_LENGTH characters
    when it at least has that much to say, and the reply gets whatever room
    is left.
    """
    message_limit = settings.MAX_MESSAGE_LENGTH
    backend = _rust_exact_compose_quote or _rust_compose_quote
    if (
        backend is not None
        and type(message_limit) is int
        and type(QUOTE_CONTENT_MINIMUM_LENGTH) is int
        and QUOTE_CONTENT_MINIMUM_LENGTH == 1000
    ):
        allocate_native = getattr(backend, "allocate_compose_quote_lengths", None)
        if callable(allocate_native):
            lengths = cast(
                "tuple[int, int] | None",
                allocate_native(
                    reply_content_length,
                    quote_content_length,
                    template_syntax_length,
                    message_limit,
                ),
            )
            if lengths is not None:
                reply_length, quote_length = lengths
                return MaxContentLengths(reply_content=reply_length, quoted_content=quote_length)

    available_length = max(message_limit - template_syntax_length, 0)
    minimum_for_quote = min(QUOTE_CONTENT_MINIMUM_LENGTH, available_length)

    quoted_content = min(
        quote_content_length,
        max(available_length - reply_content_length, minimum_for_quote),
    )
    return MaxContentLengths(
        reply_content=min(reply_content_length, available_length - quoted_content),
        quoted_content=quoted_content,
    )


def truncate_quote_content(content: str, max_length: int) -> str:
    """Truncates content like truncate_content does, but closes any fenced
    block the cut leaves open, before the truncation message. The closing
    fences need room too, and cutting shorter to make it can leave a
    different set of blocks open, so this cuts until the result fits.
    """
    if (
        close_unclosed_fences is _python_close_unclosed_fences
        and type(max_length) is int
        and type(TRUNCATION_MESSAGE) is str
        and TRUNCATION_MESSAGE == "\n[message truncated]"
    ):
        truncate_native = getattr(
            _rust_exact_compose_quote, "truncate_exact_compose_quote_text", None
        )
        if not callable(truncate_native):
            truncate_native = getattr(_rust_compose_quote, "truncate_compose_quote_text", None)
        if callable(truncate_native):
            result = cast(
                "str | None",
                truncate_native(content, max_length, _python_close_unclosed_fences),
            )
            if result is not None:
                return result

    if len(content) <= max_length:
        return content

    truncation_length = max_length - len(TRUNCATION_MESSAGE)
    while truncation_length > 0:
        truncated_content = close_unclosed_fences(content[:truncation_length])
        overflow = len(truncated_content) + len(TRUNCATION_MESSAGE) - max_length
        if overflow <= 0:
            return truncated_content + TRUNCATION_MESSAGE
        truncation_length -= overflow
    return TRUNCATION_MESSAGE


def compose_quote_and_reply_for_channel_message(
    reply_content: str,
    quote_content: str,
    quote_message_sender_silent_mention: str,
    quote_message_url: str,
    quote_message_topic_pretty_link: str,
) -> str:
    if not quote_content.strip():
        # With nothing to quote, the context line would read as if it
        # introduced the reply as the quoted message.
        return truncate_content(reply_content, settings.MAX_MESSAGE_LENGTH, TRUNCATION_MESSAGE)

    fence = get_unused_fence(quote_content)
    quote_content = close_unclosed_fences(quote_content)
    quote_context = CHANNEL_MESSAGE_QUOTE_CONTEXT.format(
        user_silent_mention=quote_message_sender_silent_mention,
        conversation_url=quote_message_url,
        topic_pretty_link=quote_message_topic_pretty_link,
    )
    max_length_for = get_measurements_for_quote_and_reply_content(
        reply_content_length=len(reply_content),
        quote_content_length=len(quote_content),
        # Measured with the same template the result is built from, so that
        # every character of syntax between the two parts is accounted for.
        template_syntax_length=len(
            QUOTE_AND_REPLY_TEMPLATE.format(
                quote_context=quote_context,
                quote_body=MESSAGE_QUOTE_BODY.format(fence=fence, content=""),
                reply_content="",
            )
        ),
    )
    return QUOTE_AND_REPLY_TEMPLATE.format(
        quote_context=quote_context,
        quote_body=MESSAGE_QUOTE_BODY.format(
            fence=fence,
            content=truncate_quote_content(quote_content, max_length_for.quoted_content),
        ),
        reply_content=truncate_content(
            reply_content, max_length_for.reply_content, TRUNCATION_MESSAGE
        ),
    )

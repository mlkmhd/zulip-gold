import re
from functools import cache
from typing import TYPE_CHECKING, TypeAlias
from urllib import parse as urlparse

if TYPE_CHECKING:
    from zerver.lib.markdown import (
        StreamPattern,
        StreamTopicMessagePattern,
        StreamTopicPattern,
        ZulipMarkdown,
    )

ChannelInputs: TypeAlias = tuple[
    dict[str, tuple[int, bool]], dict[tuple[str, str], int], str, tuple[str, ...]
]

_SURROGATE_RE = re.compile("[\ud800-\udfff]")


@cache
def decimal_digits() -> tuple[str, ...]:
    return tuple(chr(point) for point in range(128, 0x110000) if chr(point).isdecimal())


def standard_pattern(
    pattern: "StreamPattern | StreamTopicPattern | StreamTopicMessagePattern",
    renderer: "ZulipMarkdown",
) -> bool:
    from zerver.lib import markdown, url_encoding
    from zerver.lib.markdown import (
        StreamTopicPattern,
        ZulipMarkdown,
        _native_channel_callbacks,
        _native_channel_topic_callbacks,
        _native_channel_topic_type,
        _native_channel_url_callbacks,
        _native_channel_url_replacements,
        channel_links,
    )

    return (
        type(renderer) is ZulipMarkdown
        and pattern.zmd is renderer
        and pattern.md is renderer
        and getattr(pattern.find_stream_id, "__func__", None) is _native_channel_callbacks[0]
        and getattr(pattern.is_empty_topic_only_channel, "__func__", None)
        is _native_channel_callbacks[1]
        and (
            type(pattern) is not StreamTopicPattern
            or getattr(pattern.get_with_operand, "__func__", None) is _native_channel_callbacks[2]
        )
        and vars(markdown).get("encode_channel") is _native_channel_callbacks[4]
        and vars(markdown).get("encode_hash_component") is _native_channel_callbacks[5]
        and url_encoding.encode_hash_component is _native_channel_callbacks[5]
        and channel_links.channel_link is _native_channel_callbacks[6]
        and vars(markdown).get("ChannelTopicInfo") is _native_channel_topic_type
        and tuple(
            getattr(_native_channel_topic_type, name) for name in ("__init__", "__hash__", "__eq__")
        )
        == _native_channel_topic_callbacks
        and urlparse.quote is _native_channel_url_callbacks[0]
        and urlparse.quote_from_bytes is _native_channel_url_callbacks[1]
        and getattr(url_encoding._rust_url_fragments, "encode_narrow_hash_component", None)
        is _native_channel_url_callbacks[2]
        and getattr(channel_links._rust_channel_spans, "markdown_channel_link_fields", None)
        is _native_channel_url_callbacks[3]
        and type(url_encoding.hash_replacements) is dict
        and all(
            type(key) is str and type(value) is str
            for key, value in url_encoding.hash_replacements.items()
        )
        and url_encoding.hash_replacements == _native_channel_url_replacements
    )


def snapshot(renderer: "ZulipMarkdown") -> ChannelInputs | None:
    from zerver.lib.markdown import (
        DbData,
        _native_channel_callbacks,
        _native_channel_realm_topics_policy,
    )
    from zerver.lib.mention import ChannelInfo, ChannelTopicInfo, MentionBackend, MentionData
    from zerver.models import Message, Realm
    from zerver.models.streams import StreamTopicsPolicyEnum

    fallback = Message.EMPTY_TOPIC_FALLBACK_NAME
    if type(fallback) is not str or _SURROGATE_RE.search(fallback) or len(fallback) > 1_000_000:
        return None
    data = renderer.zulip_db_data
    if data is None:
        return {}, {}, fallback, decimal_digits()
    if (
        type(data) is not DbData
        or type(data.stream_names) is not dict
        or type(data.topic_info) is not dict
    ):
        return None
    if len(data.stream_names) + len(data.topic_info) > 100_000:
        return None
    names: dict[str, tuple[int, bool]] = {}
    size = len(fallback)
    if data.stream_names:
        mentions = data.mention_data
        if (
            type(mentions) is not MentionData
            or getattr(mentions.get_channel_info, "__func__", None)
            is not _native_channel_callbacks[3]
            or type(mentions.mention_backend) is not MentionBackend
            or type(mentions.mention_backend.stream_cache) is not dict
        ):
            return None
        for name, channel_id in data.stream_names.items():
            if (
                type(name) is not str
                or _SURROGATE_RE.search(name)
                or type(channel_id) is not int
                or not -(2**63) <= channel_id < 2**63
            ):
                return None
            size += len(name)
            if size > 1_000_000:
                return None
            info = mentions.mention_backend.stream_cache.get(name)
            empty_only = False
            if info is not None:
                if type(info) is not ChannelInfo or type(info.topics_policy) is not int:
                    return None
                policy = info.topics_policy
                if policy == StreamTopicsPolicyEnum.inherit.value:
                    realm = renderer.zulip_realm
                    if (
                        type(realm) is not Realm
                        or vars(Realm).get("topics_policy")
                        is not _native_channel_realm_topics_policy
                    ):
                        return None
                    inherited_policy = vars(realm).get("topics_policy")
                    if type(inherited_policy) is not int:
                        return None
                    policy = inherited_policy
                empty_only = policy == StreamTopicsPolicyEnum.empty_topic_only.value
            names[name] = channel_id, empty_only
    topics: dict[tuple[str, str], int] = {}
    for key, operand in data.topic_info.items():
        if (
            type(key) is not ChannelTopicInfo
            or any(
                type(value) is not str or _SURROGATE_RE.search(value)
                for value in (key.channel_name, key.topic_name)
            )
            or (
                operand is not None
                and (type(operand) is not int or not -(2**63) <= operand < 2**63)
            )
        ):
            return None
        size += len(key.channel_name) + len(key.topic_name)
        if size > 1_000_000:
            return None
        if operand is not None:
            topics[key.channel_name, key.topic_name] = operand
    return names, topics, fallback, decimal_digits()

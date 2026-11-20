import re
import sys
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, TypeAlias, cast

from markdown import Markdown

from zerver.lib.markdown.native_channels import decimal_digits

if TYPE_CHECKING:
    from zerver.lib.markdown import UserGroupMentionPattern, UserMentionPattern, ZulipMarkdown
    from zerver.lib.mention import FullNameInfo

User: TypeAlias = tuple[int, str, bool]
Group: TypeAlias = tuple[int, str, bool]
MentionInputs: TypeAlias = tuple[
    tuple[dict[str, User | None], dict[int, User], dict[str, Group | None]] | None,
    dict[str, int],
    int,
]

_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_DIGIT_LIMIT: Callable[[], int] | None = getattr(sys, "get_int_max_str_digits", None)


@cache
def decimal_values() -> dict[str, int]:
    return {digit: int(digit) for digit in decimal_digits()}


def standard_pattern(
    pattern: "UserMentionPattern | UserGroupMentionPattern",
    renderer: Markdown,
    expression: object,
) -> bool:
    from zerver.lib import markdown, mention
    from zerver.lib.markdown import (
        UserMentionPattern,
        ZulipMarkdown,
        _native_mention_callbacks,
        _native_mention_field_callbacks,
        _native_mention_patterns,
        _native_mention_re_match,
        _native_mention_system_names,
        _rust_markdown_inline_mentions,
    )
    from zerver.models.groups import SystemGroups

    index = 0 if type(pattern) is UserMentionPattern else 1
    return (
        type(renderer) is ZulipMarkdown
        and pattern.zmd is renderer
        and pattern.md is renderer
        and expression is _native_mention_patterns[index]
        and mention.MENTIONS_RE is _native_mention_patterns[0]
        and mention.USER_GROUP_MENTIONS_RE is _native_mention_patterns[1]
        and mention.user_mention_matches_topic_wildcard is _native_mention_callbacks[3]
        and mention.user_mention_matches_stream_wildcard is _native_mention_callbacks[4]
        and vars(markdown).get("get_user_group_mention_display_name")
        is _native_mention_callbacks[5]
        and type(mention.topic_wildcards) is frozenset
        and mention.topic_wildcards == frozenset({"topic"})
        and type(mention.stream_wildcards) is frozenset
        and mention.stream_wildcards == frozenset({"all", "everyone", "stream", "channel"})
        and markdown.build_mention_span is _native_mention_field_callbacks[0]
        and getattr(_rust_markdown_inline_mentions, "user_markdown_mention_fields", None)
        is _native_mention_field_callbacks[1]
        and getattr(_rust_markdown_inline_mentions, "group_markdown_mention_fields", None)
        is _native_mention_field_callbacks[2]
        and type(SystemGroups.GROUP_DISPLAY_NAME_MAP) is dict
        and SystemGroups.GROUP_DISPLAY_NAME_MAP.keys() == _native_mention_system_names.keys()
        and all(
            value is _native_mention_system_names[key]
            for key, value in SystemGroups.GROUP_DISPLAY_NAME_MAP.items()
        )
        and re.match is _native_mention_re_match
        and getattr(sys, "get_int_max_str_digits", None) is _DIGIT_LIMIT
    )


def snapshot(renderer: "ZulipMarkdown", texts: list[str]) -> MentionInputs | None:
    from zerver.lib.markdown import (
        DbData,
        MessageRenderingResult,
        _native_mention_callbacks,
        _native_mention_patterns,
    )
    from zerver.lib.mention import FullNameInfo, MentionData
    from zerver.models import NamedUserGroup
    from zerver.models.groups import SystemGroups

    limit = 0 if _DIGIT_LIMIT is None else _DIGIT_LIMIT()
    data = renderer.zulip_db_data
    if data is None:
        return None, decimal_values(), limit
    rendering: object = getattr(renderer, "zulip_rendering_result", None)
    if (
        type(rendering) is not MessageRenderingResult
        or type(rendering.mentions_user_ids) is not set
        or type(rendering.mentions_user_group_ids) is not set
        or any(
            type(value) is not int
            for values in (rendering.mentions_user_ids, rendering.mentions_user_group_ids)
            for value in values
        )
        or type(cast(object, rendering.mentions_stream_wildcard)) is not bool
        or type(cast(object, rendering.mentions_topic_wildcard)) is not bool
    ):
        return None
    if type(data) is not DbData or type(data.mention_data) is not MentionData:
        return None
    mentions = data.mention_data
    if any(
        getattr(getattr(mentions, name), "__func__", None) is not callback
        for name, callback in zip(
            ("get_user_by_name", "get_user_by_id", "get_user_group"),
            _native_mention_callbacks[:3],
            strict=True,
        )
    ):
        return None
    if any(
        type(getattr(mentions, name, None)) is not dict
        for name in ("full_name_info", "user_id_info", "user_group_name_info")
    ):
        return None
    if (
        sum(
            len(values)
            for values in (
                mentions.full_name_info,
                mentions.user_id_info,
                mentions.user_group_name_info,
            )
        )
        > 100_000
    ):
        return None
    size = 0

    def valid_text(value: object) -> bool:
        nonlocal size
        if type(value) is not str or _SURROGATE_RE.search(value):
            return False
        size += len(value)
        return size <= 1_000_000

    def valid_user(user: "FullNameInfo") -> bool:
        return (
            type(user) is FullNameInfo
            and type(user.id) is int
            and -(2**31) <= user.id < 2**31
            and valid_text(user.full_name)
            and type(user.is_active) is bool
        )

    if any(
        not valid_text(name) or not valid_user(user)
        for name, user in mentions.full_name_info.items()
    ) or any(
        type(id) is not int or not -(2**31) <= id < 2**31 or not valid_user(user)
        for id, user in mentions.user_id_info.items()
    ):
        return None
    group_fields: dict[str, Group] = {}
    for name, group in mentions.user_group_name_info.items():
        if not valid_text(name) or type(group) is not NamedUserGroup:
            return None
        fields = vars(group)
        if (
            type(fields.get("id")) is not int
            or not -(2**31) <= fields["id"] < 2**31
            or type(fields.get("deactivated")) is not bool
            or type(fields.get("is_system_group")) is not bool
            or not valid_text(fields.get("name"))
        ):
            return None
        if fields["is_system_group"] and fields["name"] not in SystemGroups.GROUP_DISPLAY_NAME_MAP:
            return None
        display_name = str(
            cast(Callable[[NamedUserGroup], object], _native_mention_callbacks[5])(group)
        )
        if not valid_text(display_name):
            return None
        group_fields[name] = fields["id"], display_name, fields["deactivated"]
    names: dict[str, User | None] = {}
    groups: dict[str, Group | None] = {}
    for text in texts:
        for matched in _native_mention_patterns[0].finditer(text):
            name = matched.group("match")
            if name in names:
                continue
            user = mentions.full_name_info.get(name.lower())
            if not valid_text(name) or (user is not None and not valid_text(user.full_name)):
                return None
            names[name] = None if user is None else (user.id, user.full_name, user.is_active)
        for matched in _native_mention_patterns[1].finditer(text):
            name = matched.group("match")
            if name in groups:
                continue
            resolved_group = group_fields.get(name.lower())
            if not valid_text(name) or (
                resolved_group is not None and not valid_text(resolved_group[1])
            ):
                return None
            groups[name] = resolved_group
    if len(names) + len(groups) > 100_000:
        return None
    ids = {
        id: (user.id, user.full_name, user.is_active) for id, user in mentions.user_id_info.items()
    }
    return (names, ids, groups), decimal_values(), limit

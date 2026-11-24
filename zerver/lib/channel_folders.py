import os
from dataclasses import dataclass
from importlib import import_module
from typing import Literal

from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.markdown import markdown_convert
from zerver.lib.streams import get_web_public_streams_queryset
from zerver.lib.string_validation import check_string_is_printable
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import ChannelFolder, Realm, Stream, UserProfile

try:
    _rust_channel_folder_display = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_CHANNEL_FOLDER_DISPLAY") == "1"
        else None
    )
except ImportError:
    _rust_channel_folder_display = None

_rust_plan_channel_folder_display_order = getattr(
    _rust_channel_folder_display, "plan_channel_folder_display_order", None
)


@dataclass
class ChannelFolderData:
    id: int
    name: str
    description: str
    rendered_description: str
    order: int
    creator_id: int | None
    date_created: int
    is_archived: bool


def check_channel_folder_name(name: str, realm: Realm) -> None:
    if name.strip() == "":
        raise JsonableError(_("Channel folder name can't be empty."))

    invalid_character_pos = check_string_is_printable(name)
    if invalid_character_pos is not None:
        raise JsonableError(
            _("Invalid character in channel folder name, at position {position}.").format(
                position=invalid_character_pos
            )
        )

    if ChannelFolder.objects.filter(name__iexact=name, realm=realm, is_archived=False).exists():
        raise JsonableError(_("Channel folder name already in use"))


def render_channel_folder_description(text: str, realm: Realm, *, acting_user: UserProfile) -> str:
    return markdown_convert(
        text, message_realm=realm, no_previews=True, acting_user=acting_user
    ).rendered_content


def get_channel_folder_data(channel_folder: ChannelFolder) -> ChannelFolderData:
    date_created = datetime_to_timestamp(channel_folder.date_created)
    return ChannelFolderData(
        id=channel_folder.id,
        name=channel_folder.name,
        description=channel_folder.description,
        rendered_description=channel_folder.rendered_description,
        order=channel_folder.order,
        date_created=date_created,
        creator_id=channel_folder.creator_id,
        is_archived=channel_folder.is_archived,
    )


def _native_channel_folder_order_for_display(
    channel_folders: list[ChannelFolderData], key: Literal["order", "id"]
) -> list[ChannelFolderData] | None:
    if not callable(_rust_plan_channel_folder_display_order) or any(
        type(folder) is not ChannelFolderData for folder in channel_folders
    ):
        return None
    keys = [getattr(folder, key) for folder in channel_folders]
    if any(type(value) is not int for value in keys):
        return None
    positions = _rust_plan_channel_folder_display_order(keys)
    if type(positions) is not list or len(positions) != len(channel_folders):
        return None

    ordered = []
    previous: tuple[int, int] | None = None
    for position in positions:
        if type(position) is not int or not 0 <= position < len(channel_folders):
            return None
        current = (keys[position], position)
        if previous is not None and current <= previous:
            return None
        previous = current
        ordered.append(channel_folders[position])
    return ordered


def get_channel_folders_in_realm(
    realm: Realm, include_archived: bool = False
) -> list[ChannelFolderData]:
    folders = ChannelFolder.objects.filter(realm=realm)
    if not include_archived:
        folders = folders.exclude(is_archived=True)

    channel_folders = [get_channel_folder_data(channel_folder) for channel_folder in folders]
    projected = _native_channel_folder_order_for_display(channel_folders, "order")
    if projected is not None:
        return projected
    return sorted(channel_folders, key=lambda folder: folder.order)


def get_channel_folder_by_id(channel_folder_id: int, realm: Realm) -> ChannelFolder:
    try:
        channel_folder = ChannelFolder.objects.get(id=channel_folder_id, realm=realm)
        return channel_folder
    except ChannelFolder.DoesNotExist:
        raise JsonableError(_("Invalid channel folder ID"))


def get_channel_folders_for_spectators(realm: Realm) -> list[ChannelFolderData]:
    folder_ids_for_web_public_streams = set(
        get_web_public_streams_queryset(realm).values_list("folder_id", flat=True)
    )
    folders = ChannelFolder.objects.filter(id__in=folder_ids_for_web_public_streams)
    channel_folders = [get_channel_folder_data(channel_folder) for channel_folder in folders]
    projected = _native_channel_folder_order_for_display(channel_folders, "id")
    if projected is not None:
        return projected
    return sorted(channel_folders, key=lambda folder: folder.id)


def check_channel_folder_in_use(channel_folder: ChannelFolder) -> bool:
    if Stream.objects.filter(folder=channel_folder).exists():
        return True
    return False

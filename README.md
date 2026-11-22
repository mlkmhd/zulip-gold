# Zulip Gold

<img src="https://raw.githubusercontent.com/mlkmhd/zulip-gold/main/static/images/logo/zulip-icon-128x128.png" alt="Zulip Gold logo" width="64" height="64">

A fork of [Zulip](https://github.com/zulip/zulip) with a redesigned web interface, more control over regional display settings, and new options for channel pictures and reaction notifications.

[![The modern Zulip web interface in dark mode, with a custom channel picture, incoming and outgoing message bubbles, and emoji reactions](https://raw.githubusercontent.com/mlkmhd/zulip-gold/main/docs/_static/zulip-fork-dark.png)](https://github.com/mlkmhd/zulip-gold/blob/main/docs/_static/zulip-fork-dark.png)

_The Modern interface in dark mode, showing a sample launch-planning conversation._

## What this fork adds

- **Classic and Modern interfaces**. Switch layouts under **Personal settings → Preferences → General → Web interface**. Zulip saves your choice to your account.
- **Redesigned conversations**. In Modern, scan incoming and outgoing message bubbles, see animated typing indicators, keep the latest message beside the composer in short conversations, and browse older direct message conversations through **more conversations**. Users without a profile photo use Zulip's default avatars.
- **Sidebar shortcuts and pictures**. Direct messages appear above channels. In Modern, use **Personal settings → Preferences → Left sidebar** to show or hide profile and channel pictures.
- **Pinned conversations and Saved Messages**. Pin direct messages in the sidebar and open Saved Messages from **Views** to keep notes and files.
- **Custom channel pictures**. Channel administrators can upload, crop, replace, or remove a picture in channel settings. The picture appears beside the channel name in the sidebar.
- **Emoji reactions and notifications**. Find recently used emoji in the picker. Eligible reactions can notify the message author on desktop or mobile, and organization administrators can disable reaction notifications.
- **Calendar, time zone, and fonts**. Choose a Jalali, Gregorian, Islamic civil, or Hebrew calendar. Set a display time zone or follow your device, and pick a Persian font such as Vazir, Sahel, or Samim.
- **Drafts saved before reload**. Keep in-progress messages when the web app reloads to apply an update.

## Backend migration: Python and Rust

Zulip Gold is migrating its backend from Python/Django to Rust in stages. Django remains the default and the reference for compatibility. Django can call Rust through a Python extension for selected tasks while still handling those requests. An opt-in Rust HTTP service can handle some operations directly.

| Verified standalone Rust HTTP coverage |        Operations |
| -------------------------------------- | ----------------: |
| OpenAPI inventory                      | 42 of 171 (24.6%) |
| Explicit web operation inventory       | 41 of 190 (21.6%) |

These inventories overlap, so the counts should not be added. They exclude Rust code called from Django and do not measure how much of the whole backend has migrated.

In isolated tests of read-only routes including saved snippets and user groups, disabling Rust routing and reloading nginx restored Django. Requests did not automatically fall back while the Rust service was unavailable. See the [migration handoff](https://github.com/mlkmhd/zulip-gold/blob/main/rust-backend/MIGRATION_HANDOFF.md) for verification details and remaining work.

## Getting started

- **Use this fork**. Start with the [fork’s releases](https://github.com/mlkmhd/zulip-gold/releases).
- **Develop locally**. Follow Zulip’s [development setup guide](https://zulip.readthedocs.io/en/latest/development/overview.html) from a checkout of this fork.
- **Learn about upstream Zulip**. See the [official repository](https://github.com/zulip/zulip) and [self-hosting documentation](https://zulip.com/self-hosting/).

Zulip and this fork are distributed under the [Apache 2.0 license](https://github.com/mlkmhd/zulip-gold/blob/main/LICENSE).

pub mod alert_words;
pub mod bot_storage;
pub mod direct_message_group;
pub mod draft_text;
pub mod draft_timestamp;
pub mod stream_color;
pub mod user_status;

#[cfg(feature = "python")]
mod attachment_access;

#[cfg(any(feature = "python", test))]
mod client_defaults;

#[cfg(any(feature = "http", feature = "python"))]
mod draft_fields;

#[cfg(any(feature = "http", feature = "python"))]
mod draft_input;

#[cfg(any(feature = "http", feature = "python"))]
mod draft_recipients;

#[cfg(feature = "http")]
pub mod http;

#[cfg(any(feature = "http", feature = "python"))]
mod own_profile_payload;

#[cfg(feature = "python")]
mod python_own_profile;

#[cfg(feature = "python")]
mod markdown_alerts;

#[cfg(feature = "python")]
mod markdown_blocks;

#[cfg(feature = "python")]
mod markdown_blockquotes;

#[cfg(any(feature = "python", test))]
mod markdown_channel_links;

#[cfg(feature = "native-markdown")]
pub mod markdown_channel_spans;

#[cfg(feature = "native-markdown")]
pub mod markdown_channel_syntax;

#[cfg(feature = "native-markdown")]
mod markdown_code;

#[cfg(feature = "python")]
mod markdown_code_blocks;

#[cfg(feature = "native-markdown")]
mod markdown_emphasis;

#[cfg(feature = "native-markdown")]
mod markdown_emoji_data;

#[cfg(feature = "native-markdown")]
pub mod markdown_unicode_emoji;

#[cfg(feature = "python")]
mod markdown_fences;

#[cfg(feature = "python")]
mod markdown_headings;

#[cfg(feature = "python")]
mod markdown_horizontal_rules;

#[cfg(feature = "native-markdown")]
pub mod markdown_inline_mentions;

#[cfg(feature = "python")]
mod markdown_inline_runtime;

#[cfg(feature = "native-markdown")]
mod markdown_inline_stash;

#[cfg(feature = "native-markdown")]
pub mod markdown_link_syntax;

#[cfg(feature = "native-markdown")]
pub mod markdown_links;

#[cfg(feature = "native-markdown")]
pub mod markdown_autolinks;

#[cfg(feature = "native-markdown")]
mod markdown_autolink_data;

#[cfg(feature = "native-markdown")]
pub mod markdown_media;

#[cfg(feature = "native-markdown")]
pub mod markdown_native_inline;

#[cfg(feature = "python")]
mod markdown_preview_urls;

#[cfg(feature = "python")]
mod markdown_reply_links;

#[cfg(feature = "python")]
mod markdown_topic_links;

#[cfg(feature = "native-markdown")]
pub mod markdown_timestamp;

#[cfg(feature = "native-markdown")]
pub mod markdown_tex;

#[cfg(feature = "native-markdown")]
pub mod markdown_tex_preflight;

#[cfg(feature = "python")]
mod markdown_tex_handlers;

#[cfg(feature = "native-markdown")]
pub mod markdown_tex_syntax;

#[cfg(feature = "native-markdown")]
pub mod markdown_code_header;

#[cfg(feature = "python")]
mod markdown_list_blocks;

#[cfg(feature = "python")]
mod markdown_list_indent;

#[cfg(feature = "python")]
mod markdown_list_indent_layout;

#[cfg(feature = "python")]
mod markdown_list_layout;

#[cfg(feature = "python")]
mod markdown_lists;

#[cfg(any(feature = "python", test))]
mod markdown_mentions;

#[cfg(any(feature = "native-markdown", test))]
pub mod markdown_mention_syntax;

#[cfg(feature = "python")]
mod markdown_paragraphs;

#[cfg(feature = "python")]
mod markdown_postprocessing;

#[cfg(feature = "python")]
mod markdown_prettify;

#[cfg(feature = "python")]
mod markdown_serialization;

#[cfg(feature = "python")]
mod markdown_tables;

#[cfg(feature = "python")]
mod markdown_unescape;

#[cfg(any(feature = "python", test))]
mod markdown_upload;

#[cfg(any(feature = "python", test))]
mod markdown_whitespace;

#[cfg(any(feature = "http", feature = "python"))]
mod memcached;

#[cfg(feature = "python")]
mod mention_data;

#[cfg(any(feature = "python", test))]
mod message_topic;

#[cfg(feature = "python")]
mod python;

#[cfg(feature = "python")]
mod python_media;

#[cfg(feature = "python")]
mod python_highlight;

#[cfg(any(feature = "python", test))]
mod presence;

#[cfg(feature = "python")]
mod python_message_payload;

#[cfg(feature = "python")]
mod python_message_window;

#[cfg(feature = "python")]
mod python_topic_history;

#[cfg(feature = "python")]
mod python_user_status_batch;

#[cfg(feature = "python")]
mod python_channel_folder_order;
#[cfg(feature = "python")]
mod python_dm_pin;
#[cfg(feature = "python")]
mod python_user_status_persistence;

#[cfg(any(feature = "http", feature = "python", test))]
mod topic_history;

#[cfg(feature = "python")]
mod python_presence;

#[cfg(feature = "python")]
mod python_recent_conversations;

#[cfg(feature = "python")]
mod python_register_state;

#[cfg(feature = "python")]
mod python_channel_payload;
#[cfg(feature = "python")]
mod python_folder_replay;
#[cfg(feature = "python")]
mod python_personal_replay;
#[cfg(feature = "python")]
mod python_schedule_replay;
#[cfg(feature = "python")]
mod python_search_highlights;
#[cfg(feature = "python")]
mod python_thumbnail_status;
#[cfg(feature = "python")]
mod python_user_directory;

#[cfg(feature = "python")]
mod python_settings_payload;

#[cfg(feature = "python")]
mod python_event_queue;

#[cfg(feature = "python")]
mod python_group_payload;

#[cfg(any(feature = "python", feature = "http", test))]
mod group_membership;

#[cfg(feature = "http")]
#[doc(hidden)]
pub mod group_listing;

#[cfg(any(feature = "python", feature = "http", test))]
mod channel_traffic;

#[cfg(feature = "python")]
mod python_tex;
#[cfg(feature = "python")]
mod python_unread;
#[cfg(feature = "python")]
mod python_unread_replay;

#[cfg(feature = "python")]
mod python_inline_checkpoint;

#[cfg(feature = "python")]
mod python_tex_checkpoint;

#[cfg(feature = "python")]
mod reminder_access;

#[cfg(any(feature = "python", test))]
mod reminder_context;

#[cfg(any(feature = "python", test))]
mod reminder_quote;

#[cfg(any(feature = "python", test))]
mod role_permissions;

#[cfg(feature = "python")]
mod scheduled_attachments;

#[cfg(any(feature = "http", feature = "python"))]
mod scheduled_fields;

#[cfg(feature = "python")]
mod scheduled_input;

#[cfg(any(feature = "python", test))]
mod scheduled_validation;

#[cfg(feature = "python")]
mod send_permissions;

#[cfg(any(feature = "native-markdown", test))]
mod url_fragments;

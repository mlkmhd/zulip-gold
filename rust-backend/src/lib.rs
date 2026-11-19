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

#[cfg(feature = "python")]
mod markdown_alerts;

#[cfg(feature = "python")]
mod markdown_blocks;

#[cfg(feature = "python")]
mod markdown_blockquotes;

#[cfg(any(feature = "python", test))]
mod markdown_channel_links;

#[cfg(feature = "python")]
mod markdown_channel_spans;

#[cfg(feature = "python")]
mod markdown_code;

#[cfg(feature = "python")]
mod markdown_code_blocks;

#[cfg(feature = "python")]
mod markdown_emphasis;

#[cfg(feature = "python")]
mod markdown_fences;

#[cfg(feature = "python")]
mod markdown_headings;

#[cfg(feature = "python")]
mod markdown_horizontal_rules;

#[cfg(feature = "python")]
mod markdown_inline_mentions;

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

#[cfg(feature = "python")]
mod markdown_paragraphs;

#[cfg(feature = "python")]
mod markdown_tables;

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

#[cfg(any(feature = "python", test))]
mod url_fragments;

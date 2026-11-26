use std::collections::{HashMap, HashSet};
use std::{io, net::SocketAddr};

use jiter::NumberInt;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyConnectionError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PyString, PyTuple};

use crate::draft_fields::{DraftFields, Recipients};
use crate::draft_input::{self, DraftInput, Location, ParseError, Timestamp};
use crate::draft_recipients::{DirectRecipient, DirectRecipientError, validate_direct_recipients};
use crate::python_text_bytes::{python_string_bytes, python_string_from_bytes};
use crate::scheduled_fields::{MessageFields, Recipient, ScheduledJob};
use crate::scheduled_input::{self, ArgumentKind, ScheduledArgument};
use crate::scheduled_validation::{self, RecipientError, RecipientKind};
use crate::{
    alert_words, attachment_access, bot_storage, client_defaults, direct_message_group, draft_text,
    draft_timestamp, memcached, mention_data, message_topic, role_permissions, stream_color,
    url_fragments, user_status,
};
use crate::{
    markdown_alerts, markdown_blockquotes, markdown_blocks, markdown_channel_links,
    markdown_channel_spans, markdown_code, markdown_code_blocks, markdown_emphasis,
    markdown_fences, markdown_headings, markdown_horizontal_rules, markdown_inline_mentions,
    markdown_inline_stash, markdown_list_blocks, markdown_list_indent, markdown_list_indent_layout,
    markdown_list_layout, markdown_lists, markdown_mentions, markdown_native_inline,
    markdown_paragraphs, markdown_postprocessing, markdown_prettify, markdown_serialization,
    markdown_tables, markdown_unescape, markdown_upload, markdown_whitespace, reminder_access,
    reminder_context, reminder_quote, scheduled_attachments, send_permissions,
};

#[pyclass]
struct MarkdownInlineStashScanner {
    scanner: markdown_inline_stash::Scanner,
}

#[pymethods]
impl MarkdownInlineStashScanner {
    fn next(&mut self, accepted: bool) -> Option<(String, Option<String>, Option<String>)> {
        self.scanner
            .next(accepted)
            .map(|step| (step.text, step.rejected_text, step.identifier))
    }
}

type InlineCodeMatch = (
    usize,
    usize,
    Option<String>,
    Option<String>,
    Option<String>,
    String,
);

type ChannelLinkFields = (
    Vec<(&'static str, String)>,
    String,
    bool,
    Option<&'static str>,
);

type EmphasisMatch = (String, &'static str, String, Option<&'static str>, String);

type SerializationNode = (
    u8,
    String,
    Option<String>,
    Option<String>,
    Vec<(String, String)>,
    usize,
);

type NativeInlineText = (String, bool);
type NativeInlineNode = (
    String,
    String,
    Option<NativeInlineText>,
    Option<NativeInlineText>,
    Vec<usize>,
);
pub(crate) type NativeInlinePlannedNode = (
    String,
    String,
    Option<NativeInlineText>,
    Option<NativeInlineText>,
    Vec<usize>,
    Vec<(String, String)>,
);
type NativeEmojiCatalog = (HashMap<String, String>, HashMap<String, String>);
type NativeEmoticonCatalog = (Vec<(String, String)>, HashMap<String, String>);
type NativeUnicodeEmojiCatalog = (String, HashMap<String, String>);
type NativeAutolinkSpec = (String, Vec<String>, Vec<String>, bool);
type NativeLinkPolicy = (Vec<String>, Vec<String>, bool, Option<String>);
type NativeChannelInputs = (
    HashMap<String, (i64, bool)>,
    HashMap<(String, String), i64>,
    String,
    Vec<char>,
);
type NativeMentionUser = (i32, String, bool);
type NativeMentionGroup = (i32, String, bool);
type NativeMentionData = (
    HashMap<String, Option<NativeMentionUser>>,
    HashMap<i32, NativeMentionUser>,
    HashMap<String, Option<NativeMentionGroup>>,
);
type NativeMentionInputs = (Option<NativeMentionData>, HashMap<char, u8>, usize);
type NativeMentionEffects = (Vec<i32>, Vec<i32>, bool, bool);
type NativeInlineStash = (Option<usize>, Option<NativeInlineText>);
pub(crate) type NativeInlineResult = (
    Vec<NativeInlinePlannedNode>,
    Vec<(usize, usize)>,
    Vec<NativeInlineStash>,
    Vec<String>,
    bool,
    NativeMentionEffects,
);

type PrettifyNode = (String, Option<String>, Option<String>, bool, usize);

type PrettifyChange = (usize, bool, bool, String);

type DraftParseResult<T> = (Option<T>, Option<(&'static str, Vec<Location>)>);

type MentionSpanFields = (
    Option<Vec<(&'static str, String)>>,
    Option<String>,
    Option<&'static str>,
    Option<i32>,
);

fn mention_span_fields(span: Option<markdown_inline_mentions::MentionSpan>) -> MentionSpanFields {
    use markdown_inline_mentions::MentionNotification;

    let Some(span) = span else {
        return (None, None, None, None);
    };
    let (kind, id) = match span.notification {
        Some(MentionNotification::User(id)) => (Some("user"), Some(id)),
        Some(MentionNotification::Group(id)) => (Some("group"), Some(id)),
        Some(MentionNotification::Channel) => (Some("stream"), None),
        Some(MentionNotification::Topic) => (Some("topic"), None),
        None => (None, None),
    };
    (Some(span.attributes), Some(span.text), kind, id)
}

struct PythonDraftInteger(NumberInt);

impl<'py> IntoPyObject<'py> for markdown_blocks::BlockStep<'_> {
    type Target = PyTuple;
    type Output = Bound<'py, PyTuple>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        let (kind, text, language, header, default_language) = match self {
            Self::Line(line) => ("line", Some(line), None, None, None),
            Self::Open {
                fence,
                language,
                header,
                default_language,
            } => ("open", Some(fence), language, header, default_language),
            Self::Close => ("close", None, None, None, None),
        };
        (kind, text, language, header, default_language).into_pyobject(py)
    }
}

impl<'py> IntoPyObject<'py> for PythonDraftInteger {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self.0 {
            NumberInt::Int(value) => Ok(value.into_pyobject(py)?.into_any()),
            NumberInt::BigInt(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

impl<'py> IntoPyObject<'py> for Timestamp {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self {
            Self::Integer(value) => PythonDraftInteger(value).into_pyobject(py),
            Self::Float(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

impl<'py> IntoPyObject<'py> for DraftInput {
    type Target = PyDict;
    type Output = Bound<'py, PyDict>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        let draft = PyDict::new(py);
        draft.set_item("type", self.r#type)?;
        draft.set_item(
            "to",
            PyList::new(py, self.to.into_iter().map(PythonDraftInteger))?,
        )?;
        draft.set_item("topic", self.topic)?;
        draft.set_item("content", self.content)?;
        draft.set_item("timestamp", self.timestamp)?;
        Ok(draft)
    }
}

impl<'py> IntoPyObject<'py> for Location {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self {
            Self::Index(value) => Ok(value.into_pyobject(py)?.into_any()),
            Self::Field(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

fn draft_parse_result<T>(
    value: &Bound<'_, PyString>,
    parse: impl FnOnce(&str) -> Result<T, ParseError>,
) -> DraftParseResult<T> {
    match value
        .to_str()
        .map_err(|_| ParseError::Unsupported)
        .and_then(parse)
    {
        Ok(draft) => (Some(draft), None),
        Err(ParseError::Schema(kind, path)) => (None, Some((kind, path))),
        Err(ParseError::Unsupported) => (None, None),
    }
}

pub(crate) type NativeInlinePlan = (NativeInlineResult, Vec<String>, bool, Vec<String>);

pub(crate) fn serialize_inline_plan(result: markdown_native_inline::State) -> NativeInlinePlan {
    use markdown_native_inline::{Stashed, Text};
    let text = |text: Text| (text.value, text.atomic);
    let planned = (
        result
            .nodes
            .into_iter()
            .map(|node| {
                (
                    node.tag,
                    node.lower_tag,
                    node.text.map(text),
                    node.tail.map(text),
                    node.children,
                    node.attributes,
                )
            })
            .collect(),
        result
            .parent_order
            .into_iter()
            .map(|child| (child, result.parents[child].unwrap()))
            .collect(),
        result
            .stash
            .into_iter()
            .map(|stashed| match stashed {
                Stashed::Node(node) => (Some(node), None),
                Stashed::Text(value) => (None, Some(text(value))),
            })
            .collect(),
        result.ancestors,
        result.registry_used,
        (
            result.mentions.users.into_iter().collect(),
            result.mentions.groups.into_iter().collect(),
            result.mentions.channel,
            result.mentions.topic,
        ),
    );
    (
        planned,
        result.attachment_paths,
        result.has_image,
        result.html_blocks,
    )
}

#[pymodule]
mod _zulip_backend {
    use super::*;

    #[pymodule_init]
    fn export_capabilities(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add("WIDGET_PARSER_API", 1)?;
        module.add("VISIBLE_TOPIC_HISTORY_API", 1)?;
        module.add("USER_STATUS_READ_API", 1)?;
        module.add("TOPIC_TITLE_API", 1)?;
        module.add("MESSAGE_CONTENT_FINALIZER_API", 1)?;
        module.add("DISPLAY_MENTION_API", 1)?;
        module.add("EMAIL_PLAIN_PREFIX_API", 1)?;
        module.add("ONBOARDING_TEXT_API", 1)?;
        module.add("LOCALE_CATALOG_API", 1)?;
        module.add("EMAIL_PLAINTEXT_SURROGATES", 1)?;
        module.add("DIGEST_TOPIC_RANK_API", 1)?;
        module.add("ANALYTICS_CLIENT_SERIES_API", 1)?;
        module.add("ANALYTICS_CHART_ORDER_API", 1)?;
        module.add("ANALYTICS_SUBGROUP_PROJECTION_API", 1)?;
        module.add("ANALYTICS_TIME_BUCKETS_API", 1)?;
        module.add("ANALYTICS_CHART_TIMESTAMP_API", 1)?;
        module.add("SAVED_SNIPPET_DISPLAY_API", 1)?;
        module.add("NAVIGATION_VIEW_DISPLAY_API", 1)?;
        module.add("DRAFT_GET_DISPLAY_API", 1)?;
        module.add("CHANNEL_FOLDER_DISPLAY_ORDER_API", 1)?;
        module.add("DOCUMENTATION_DISPLAY_TITLE_API", 1)?;
        module.add("GONG_DISPLAY_API", 1)?;
        module.add("NEWRELIC_DISPLAY_LIST_API", 1)?;
        module.add("REDMINE_DISPLAY_API", 1)?;
        module.add("GIT_DISPLAY_PEOPLE_API", 1)?;
        module.add("UPDOWN_DURATION_DISPLAY_API", 1)?;
        module.add("SONARQUBE_DISPLAY_API", 1)?;
        module.add("ACTIVITY_DATE_DISPLAY_API", 1)?;
        module.add("GIT_COMMIT_SUMMARY_API", 1)?;
        module.add("JOTFORM_VALUE_DISPLAY_API", 1)?;
        module.add("OPENPROJECT_DURATION_DISPLAY_API", 1)?;
        module.add("MARKDOWN_WHITESPACE_API", 1)?;
        module.add("API_TYPE_DISPLAY_API", 1)?;
        module.add("API_DOC_TYPE_TREE_API", 1)?;
        module.add("API_ENUM_DISPLAY_API", 1)?;
        module.add("API_RETURN_INDENT_API", 1)?;
        module.add("URL_FRAGMENTS_API", 1)?;
        module.add("TIMEZONE_ALIAS_SOURCE_API", 1)?;
        module.add("TIME_DISPLAY_API", 1)?;
        module.add("DESCRIPTION_TEXT_API", 1)?;
        module.add("LOG_DISPLAY_API", 1)?;
        module.add("MIGRATION_DISPLAY_API", 1)?;
        module.add("TABBED_TEXT_API", 1)?;
        module.add("PLAIN_DISPLAY_LIST_API", 1)?;
        module.add("SLACK_IMPORT_PIECES_API", 1)?;
        module.add("SLACK_FIELD_DISPLAY_API", 1)?;
        module.add("COMMIT_DISPLAY_API", 1)?;
        module.add("ROCKETCHAT_DISPLAY_NAME_API", 1)?;
        module.add("INTEGRATION_CATALOG_DISPLAY_API", 1)?;
        module.add("MOBILE_REPLY_LINKS_API", 1)?;
        module.add("MOBILE_PUSH_TEXT_API", 1)?;
        module.add("MOBILE_ORDERED_LIST_API", 1)?;
        module.add("SEARCH_HIGHLIGHTS_API", 1)?;
        module.add("FENCED_CODE_HEADER_API", 1)?;
        module.add("FENCED_CODE_ESCAPE_API", 1)?;
        module.add("FENCED_QUOTE_DELIMITER_API", 1)?;
        module.add("FENCED_QUOTE_TEXT_API", 1)?;
        module.add("EMOJI_PRESENTATION_SELECTOR_API", 1)?;
        module.add("EMOJI_HEX_LABEL_API", 1)?;
        module.add("EMOJI_CANONICAL_HEX_DECODE_API", 1)?;
        module.add("IMPORT_BODY_TEXT_API", 1)?;
        module.add("CHANNEL_STOCK_COLORS_API", 1)?;
        module.add("COMPOSE_QUOTE_BUDGET_API", 1)?;
        module.add("THUMBNAIL_DISPLAY_RANK_API", 1)?;
        module.add("THUMBNAIL_STORED_VARIANT_API", 1)?;
        module.add("REMINDER_QUOTE_TEXT_API", 1)?;
        module.add("PRESENCE_MODERN_TIMESTAMPS_API", 1)?;
        module.add("PRESENCE_LEGACY_DISPLAY_API", 1)?;
        module.add("PRESENCE_SINGLE_USER_RESPONSE_API", 1)?;
        module.add("PREPARED_PRESENCE_ROWS_API", 1)?;
        module.add("REMINDER_NOTE_TEXT_API", 1)?;
        module.add("REMINDER_WIDGET_LABEL_API", 1)?;
        module.add("REMINDER_TOPIC_CAPTION_API", 1)?;
        module.add("REMINDER_RECIPIENT_LABELS_API", 1)?;
        module.add("DRAFT_TOPIC_TEXT_API", 1)?;
        module.add("DRAFT_BODY_TEXT_API", 1)?;
        module.add("INLINE_CODE_API", 1)?;
        module.add("MARKDOWN_HEADINGS_API", 1)?;
        module.add("MARKDOWN_HORIZONTAL_RULES_API", 1)?;
        module.add("MARKDOWN_PARAGRAPHS_API", 1)?;
        module.add("MARKDOWN_BLOCKQUOTES_API", 1)?;
        module.add("MARKDOWN_CODE_BLOCKS_API", 1)?;
        module.add("MARKDOWN_EMPHASIS_API", 1)?;
        module.add("MARKDOWN_LISTS_API", 1)?;
        module.add("MARKDOWN_UNESCAPE_API", 1)?;
        module.add("MARKDOWN_LIST_INDENT_LAYOUT_API", 1)?;
        module.add("MARKDOWN_LIST_LAYOUT_API", 1)?;
        module.add("MARKDOWN_TABLES_API", 1)?;
        module.add("MARKDOWN_TABLE_LAYOUT_API", 1)?;
        module.add("MARKDOWN_PRETTIFY_API", 1)?;
        module.add("MARKDOWN_SERIALIZATION_API", 1)?;
        module.add("MARKDOWN_POSTPROCESSING_API", 1)?;
        module.add("MARKDOWN_TEX_SYNTAX_API", 1)?;
        module.add("KATEX_SUCCESS_BYTES_API", 1)?;
        module.add("RAW_LATEX_NOTIFICATION_API", 1)?;
        module.add("DISPLAY_RECIPIENT_ORDER_API", 1)
    }

    #[pymodule_export]
    use crate::python_inline_checkpoint::MarkdownInlineCheckpoint;

    #[pymodule_export]
    use crate::python_presence::{
        finish_single_user_presence, format_modern_presence_timestamps,
        format_presence_information, format_presence_rows,
    };

    #[pymodule_export]
    use crate::python_unread::{aggregate_unread_payload, format_unread_details};

    #[pymodule_export]
    use crate::python_unread_replay::{
        move_unread_messages, plan_private_unread_arrival, remove_unread_messages,
        restore_unread_messages,
    };

    #[pymodule_export]
    use crate::python_group_payload::{
        append_and_sort_register_group_ids, assemble_group_membership,
        assemble_realm_group_payload, subtract_register_group_ids,
    };

    #[pymodule_export]
    use crate::python_event_queue::{
        classify_queued_event, fold_queued_flag_event, merge_queued_events,
    };

    #[pymodule_export]
    use crate::python_settings_payload::{
        format_settings_emoji_choices, format_settings_notification_sounds,
        snapshot_settings_properties,
    };

    #[pymodule_export]
    use crate::python_channel_payload::{
        assemble_channel_subscription, assemble_own_subscription_batch,
        calculate_weekly_channel_traffic,
    };

    #[pymodule_export]
    use crate::python_register_state::{
        fill_legacy_register_notifications, fill_legacy_register_topics, partition_register_users,
        prune_register_custom_profile_fields,
    };

    #[pymodule_export]
    use crate::python_own_profile::assemble_own_profile;

    #[pymodule_export]
    use crate::python_thumbnail_display_rank::rank_thumbnail_display;

    #[pymodule_export]
    use crate::python_thumbnail_status::{
        thumbnail_has_stored_variant, thumbnail_missing_indexes, thumbnail_status_complete,
        thumbnail_transcode_indexes,
    };

    #[pymodule_export]
    use crate::python_search_highlights::assemble_search_highlights;

    #[pymodule_export]
    use crate::python_user_directory::assemble_user_directory;

    #[pymodule_export]
    use crate::python_schedule_replay::apply_scheduled_list_event;

    #[pymodule_export]
    use crate::python_personal_replay::apply_personal_list_event;

    #[pymodule_export]
    use crate::python_folder_replay::apply_channel_folder_event;

    #[pymodule_export]
    use crate::python_recent_conversations::{
        assemble_recent_conversations, finalize_recent_conversations,
        replay_recent_conversation_message, replay_recent_conversation_pin,
    };

    #[pymodule_export]
    use crate::python_message_payload::{
        adapt_mobile_message_events, assemble_cached_message_frame, assemble_message_reactions,
        assemble_message_submessages, finalize_message_payload_content, label_user_message_flags,
        plan_new_user_message_masks, plan_user_message_flag_deltas,
    };

    #[pymodule_export]
    use crate::python_message_window::plan_sorted_message_window;

    #[pymodule_export]
    use crate::python_emoji_codepoints::{
        decode_emoji_codepoints, decode_exact_emoji_codepoints, encode_emoji_codepoints,
        encode_exact_emoji_codepoints, unqualify_emoji_text, unqualify_exact_emoji_text,
    };

    #[pymodule_export]
    use crate::python_import_body::{
        normalize_exact_imported_message_body, normalize_imported_message_body,
    };

    #[pymodule_export]
    use crate::python_digest_rank::rank_eligible_digest_topic_indices;

    #[pymodule_export]
    use crate::python_topic_text::{split_resolved_topic_title, truncate_topic_title};

    #[pymodule_export]
    use crate::python_compose_quote::{
        allocate_compose_quote_lengths, truncate_compose_quote_text,
        truncate_exact_compose_quote_text,
    };

    #[pymodule_export]
    use crate::python_email_plain_prefix::{
        prepend_prepared_email_plaintext_sender, quote_prepared_email_plaintext,
    };

    #[pymodule_export]
    use crate::python_onboarding_text::{
        flatten_prepared_onboarding_newlines, wrap_prepared_welcome_quote,
    };

    #[pymodule_export]
    use crate::python_display_mentions::plan_display_mentions;

    #[pymodule_export]
    use crate::python_widget_text::{
        classify_widget_subclass, extract_poll_widget_text, extract_todo_widget_text,
        parse_widget_shorthand,
    };

    #[pymodule_export]
    use crate::python_locale_catalog::{
        lookup_catalog_display_name, project_catalog_language_codes,
    };

    #[pymodule_export]
    use crate::python_analytics_client_series::rewrite_analytics_client_series;

    #[pymodule_export]
    use crate::python_analytics_chart_order::{
        order_analytics_chart_clients, order_analytics_chart_totals,
    };

    #[pymodule_export]
    use crate::python_analytics_time_buckets::plan_analytics_time_buckets;

    #[pymodule_export]
    use crate::python_timezone_aliases::{lookup_timezone_alias, parse_timezone_alias_lines};

    #[pymodule_export]
    use crate::python_stream_color::pick_exact_stock_channel_colors;

    #[pymodule_export]
    use crate::python_description_text::normalize_description_text;

    #[pymodule_export]
    use crate::python_slack_import_pieces::join_slack_import_pieces;

    #[pymodule_export]
    use crate::python_plain_display_list::format_plain_display_list;

    #[pymodule_export]
    use crate::python_duration_text::format_prepared_duration;

    #[pymodule_export]
    use crate::python_time_display::{format_gmt_display_name, wrap_global_time_display};

    #[pymodule_export]
    use crate::python_log_display::{log_level_display_prefix, standard_log_display_format};

    #[pymodule_export]
    use crate::python_migration_display::{
        format_migration_display_row, strip_migration_display_sgr,
    };

    #[pymodule_export]
    use crate::python_api_type_display::{
        append_doc_nullable_label, join_prepared_doc_type_labels, render_api_doc_type_tree,
        wrap_prepared_doc_array_label,
    };

    #[pymodule_export]
    use crate::python_analytics_subgroup_projection::project_analytics_subgroup_series;

    #[pymodule_export]
    use crate::python_tabbed_text::{join_prepared_tabbed_blocks, normalize_prepared_tabbed_lines};

    #[pymodule_export]
    use crate::python_commit_display::{project_commit_display_row, rank_commit_display_names};

    #[pymodule_export]
    use crate::python_api_return_indent::indent_prepared_api_return_description;

    #[pymodule_export]
    use crate::python_api_enum_display::join_prepared_api_enum_examples;

    #[pymodule_export]
    use crate::python_slack_field_display::{
        assemble_prepared_slack_section_table, join_prepared_slack_attachment_fields,
    };

    #[pymodule_export]
    use crate::python_git_display_people::{
        format_git_display_committers, join_git_display_assignees,
    };

    #[pymodule_export]
    use crate::python_git_commit_summary_text::finish_git_commit_summary_text;

    #[pymodule_export]
    use crate::python_updown_duration_display::format_updown_duration_display;

    #[pymodule_export]
    use crate::python_display_recipient_order::plan_display_recipient_order;

    #[pymodule_export]
    use crate::python_sonarqube_display::{
        format_sonarqube_metric_label, join_sonarqube_condition_lines,
    };

    #[pymodule_export]
    use crate::python_chart_timestamp_display::truncate_chart_timestamp_display;

    #[pymodule_export]
    use crate::python_saved_snippet_display::render_saved_snippet_display_rows;

    #[pymodule_export]
    use crate::python_navigation_view_display::render_navigation_view_display_rows;

    #[pymodule_export]
    use crate::python_draft_display::render_prepared_draft;

    #[pymodule_export]
    use crate::python_gong_display::{format_gong_duration_display, join_gong_topic_display};

    #[pymodule_export]
    use crate::python_channel_folder_display::plan_channel_folder_display_order;

    #[pymodule_export]
    use crate::python_jotform_value_display::join_jotform_display_values;

    #[pymodule_export]
    use crate::python_rocketchat_display_name::{
        prefix_rocketchat_team_display_name, truncate_rocketchat_display_name,
    };

    #[pymodule_export]
    use crate::python_openproject_duration_display::{
        format_openproject_duration_part, join_openproject_duration_parts,
    };

    #[pymodule_export]
    use crate::python_integration_catalog_display::{
        project_integration_display_categories, round_integration_display_count,
    };

    #[pymodule_export]
    use crate::python_activity_date_display::{
        format_activity_display_day, format_activity_display_minute,
    };

    #[pymodule_export]
    use crate::python_mattermost_display_name::plan_mattermost_display_name;

    #[pymodule_export]
    use crate::python_redmine_display::{
        format_redmine_display_issue_label, join_redmine_display_name,
    };

    #[pymodule_export]
    use crate::python_newrelic_display_list::join_newrelic_display_list;

    #[pymodule_export]
    use crate::python_documentation_display_title::{
        format_documentation_display_title, format_integration_documentation_title,
    };

    #[pymodule_export]
    use crate::python_push_text::{
        format_mobile_ordered_list, quote_mobile_push_text, truncate_mobile_push_text,
    };

    #[pymodule_export]
    use crate::python_topic_history::format_visible_topic_history;

    #[pymodule_export]
    use crate::python_user_status_batch::{
        apply_user_status_row_event, format_visible_user_statuses,
    };

    #[pymodule_export]
    use crate::python_user_status_persistence::update_existing_user_status;

    #[pymodule_export]
    use crate::python_channel_folder_order::update_channel_folder_order;

    #[pymodule_export]
    use crate::python_dm_pin::set_existing_dm_pin;

    #[pymodule_export]
    use crate::markdown_reply_links::{
        mobile_client_needs_plain_reply_links, mobile_plain_reply_link_fields,
        render_mobile_plain_reply_links,
    };

    #[pymodule_export]
    use crate::python_tex_checkpoint::{MarkdownTexCheckpoint, preflight_markdown_tex_checkpoint};

    #[pymodule_export]
    use crate::python_highlight::{
        create_fenced_block_handler, dispatch_fenced_input_lines, dispatch_new_fenced_block,
        escape_exact_fenced_code, escape_fenced_code, find_invalid_fenced_curl_line,
        finish_fenced_block, finish_fenced_highlight, finish_fenced_input, format_fenced_spoiler,
        handle_fenced_block_line, handle_nested_fenced_block_line, normalize_fenced_code_language,
        parse_fenced_code_header, parse_fenced_highlight_lines, prepare_fenced_highlight,
    };
    #[pymodule_export]
    use crate::python_tex::{
        change_katex_to_raw_latex, decode_katex_success_text, encode_katex_input,
        find_markdown_tex, finish_katex_cli, katex_native_cli_allowed, locate_katex_cli,
        render_katex_response, request_katex_service, run_katex_cli, select_katex_encoding,
    };

    #[pymodule_export]
    use crate::markdown_tex_handlers::{render_markdown_display_tex, render_markdown_inline_tex};

    #[pymodule_export]
    use crate::markdown_inline_runtime::{
        apply_markdown_inline, handle_markdown_inline, run_markdown_inline,
        run_markdown_inline_engine,
    };

    #[pyfunction]
    fn markdown_preview_url_value(
        url: &Bound<'_, PyAny>,
        operation: &str,
    ) -> Option<(Option<String>,)> {
        use crate::markdown_preview_urls as previews;

        let url = url.cast_exact::<PyString>().ok()?.to_str().ok()?;
        let result = match operation {
            "youtube" => previews::youtube_id(url),
            "vimeo" => previews::vimeo_id(url),
            "github_image" => previews::github_image_url(url),
            "corrected_image" => previews::corrected_image_source(url),
            _ => return None,
        };
        Some((result.ok()?,))
    }

    fn extract_topic_link_candidates(
        candidates: &Bound<'_, PyList>,
    ) -> Option<Vec<crate::markdown_topic_links::WireCandidate>> {
        if candidates.len() > crate::markdown_topic_links::MAX_CANDIDATES {
            return None;
        }
        candidates
            .iter()
            .map(|value| value.extract().ok())
            .collect()
    }

    #[pyfunction]
    fn plan_markdown_topic_links(
        topic: &Bound<'_, PyAny>,
        candidates: &Bound<'_, PyList>,
        spec: NativeAutolinkSpec,
    ) -> Option<Vec<crate::markdown_topic_links::Payload>> {
        use crate::{markdown_autolinks as autolinks, markdown_topic_links as topics};

        let topic = topic.cast_exact::<PyString>().ok()?.to_str().ok()?;
        let (version, tlds, schemes, enable_file_links) = spec;
        if version != autolinks::UNICODE_VERSION {
            return None;
        }
        let mut candidates = extract_topic_link_candidates(candidates)?;
        let matcher = autolinks::Matcher::cached(autolinks::Spec {
            tlds,
            schemes,
            enable_file_links,
        })?;
        candidates.extend(topics::raw_candidates(&matcher, topic)?);
        topics::select(candidates)
    }

    #[pyfunction]
    fn select_markdown_topic_links(
        candidates: &Bound<'_, PyList>,
    ) -> Option<Vec<crate::markdown_topic_links::Payload>> {
        crate::markdown_topic_links::select(extract_topic_link_candidates(candidates)?)
    }

    type DropboxPreview = (bool, String, Option<String>);

    #[pyfunction]
    fn markdown_dropbox_preview(url: &Bound<'_, PyAny>) -> Option<(Option<DropboxPreview>,)> {
        let url = url.cast_exact::<PyString>().ok()?.to_str().ok()?;
        let result = crate::markdown_preview_urls::dropbox_plan(url).ok()?;
        Some((result.map(|plan| (plan.is_album, plan.path, plan.raw_url)),))
    }

    #[pyfunction]
    #[pyo3(signature = (nodes, patterns, ancestors, guards, emoji_catalogs, emoticon_catalog=None, unicode_emoji_catalog=None, link_policy=None, channel_inputs=None, mention_inputs=None))]
    #[allow(clippy::too_many_arguments)]
    fn plan_markdown_native_inline(
        nodes: Vec<NativeInlineNode>,
        patterns: Vec<(u8, Vec<String>)>,
        ancestors: Vec<String>,
        guards: Vec<(Bound<'_, PyAny>, Option<Bound<'_, PyAny>>)>,
        emoji_catalogs: Vec<Option<NativeEmojiCatalog>>,
        emoticon_catalog: Option<NativeEmoticonCatalog>,
        unicode_emoji_catalog: Option<NativeUnicodeEmojiCatalog>,
        link_policy: Option<NativeLinkPolicy>,
        channel_inputs: Option<NativeChannelInputs>,
        mention_inputs: Option<NativeMentionInputs>,
    ) -> Option<NativeInlineResult> {
        plan_native_inline(
            nodes,
            patterns,
            ancestors,
            guards,
            emoji_catalogs,
            emoticon_catalog,
            unicode_emoji_catalog,
            link_policy,
            channel_inputs,
            mention_inputs,
            None,
            None,
            None,
        )
        .map(|(result, _, _, _)| result)
    }

    #[pyfunction]
    #[pyo3(signature = (nodes, patterns, ancestors, guards, emoji_catalogs, emoticon_catalog=None, unicode_emoji_catalog=None, link_policy=None, channel_inputs=None, mention_inputs=None, media_inputs=None))]
    #[allow(clippy::too_many_arguments)]
    fn plan_markdown_native_inline_media(
        nodes: Vec<NativeInlineNode>,
        patterns: Vec<(u8, Vec<String>)>,
        ancestors: Vec<String>,
        guards: Vec<(Bound<'_, PyAny>, Option<Bound<'_, PyAny>>)>,
        emoji_catalogs: Vec<Option<NativeEmojiCatalog>>,
        emoticon_catalog: Option<NativeEmoticonCatalog>,
        unicode_emoji_catalog: Option<NativeUnicodeEmojiCatalog>,
        link_policy: Option<NativeLinkPolicy>,
        channel_inputs: Option<NativeChannelInputs>,
        mention_inputs: Option<NativeMentionInputs>,
        media_inputs: Option<crate::python_media::MediaInputs>,
    ) -> Option<(NativeInlineResult, Vec<String>, bool)> {
        plan_native_inline(
            nodes,
            patterns,
            ancestors,
            guards,
            emoji_catalogs,
            emoticon_catalog,
            unicode_emoji_catalog,
            link_policy,
            channel_inputs,
            mention_inputs,
            media_inputs,
            None,
            None,
        )
        .map(|(plan, paths, image, _)| (plan, paths, image))
    }

    #[pyfunction]
    #[pyo3(signature = (nodes, patterns, ancestors, guards, emoji_catalogs, emoticon_catalog=None, unicode_emoji_catalog=None, link_policy=None, channel_inputs=None, mention_inputs=None, media_inputs=None, html_counter=None))]
    #[allow(clippy::too_many_arguments)]
    fn plan_markdown_native_inline_html(
        nodes: Vec<NativeInlineNode>,
        patterns: Vec<(u8, Vec<String>)>,
        ancestors: Vec<String>,
        guards: Vec<(Bound<'_, PyAny>, Option<Bound<'_, PyAny>>)>,
        emoji_catalogs: Vec<Option<NativeEmojiCatalog>>,
        emoticon_catalog: Option<NativeEmoticonCatalog>,
        unicode_emoji_catalog: Option<NativeUnicodeEmojiCatalog>,
        link_policy: Option<NativeLinkPolicy>,
        channel_inputs: Option<NativeChannelInputs>,
        mention_inputs: Option<NativeMentionInputs>,
        media_inputs: Option<crate::python_media::MediaInputs>,
        html_counter: Option<usize>,
    ) -> Option<(NativeInlineResult, Vec<String>, bool, Vec<String>)> {
        plan_native_inline(
            nodes,
            patterns,
            ancestors,
            guards,
            emoji_catalogs,
            emoticon_catalog,
            unicode_emoji_catalog,
            link_policy,
            channel_inputs,
            mention_inputs,
            media_inputs,
            html_counter,
            None,
        )
    }

    #[pyfunction]
    #[pyo3(signature = (nodes, patterns, ancestors, guards, emoji_catalogs, emoticon_catalog=None, unicode_emoji_catalog=None, link_policy=None, channel_inputs=None, mention_inputs=None, media_inputs=None, html_counter=None, autolink_spec=None))]
    #[allow(clippy::too_many_arguments)]
    fn plan_markdown_native_inline_autolinks(
        nodes: Vec<NativeInlineNode>,
        patterns: Vec<(u8, Vec<String>)>,
        ancestors: Vec<String>,
        guards: Vec<(Bound<'_, PyAny>, Option<Bound<'_, PyAny>>)>,
        emoji_catalogs: Vec<Option<NativeEmojiCatalog>>,
        emoticon_catalog: Option<NativeEmoticonCatalog>,
        unicode_emoji_catalog: Option<NativeUnicodeEmojiCatalog>,
        link_policy: Option<NativeLinkPolicy>,
        channel_inputs: Option<NativeChannelInputs>,
        mention_inputs: Option<NativeMentionInputs>,
        media_inputs: Option<crate::python_media::MediaInputs>,
        html_counter: Option<usize>,
        autolink_spec: Option<NativeAutolinkSpec>,
    ) -> Option<(NativeInlineResult, Vec<String>, bool, Vec<String>)> {
        plan_native_inline(
            nodes,
            patterns,
            ancestors,
            guards,
            emoji_catalogs,
            emoticon_catalog,
            unicode_emoji_catalog,
            link_policy,
            channel_inputs,
            mention_inputs,
            media_inputs,
            html_counter,
            autolink_spec,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn plan_native_inline(
        nodes: Vec<NativeInlineNode>,
        patterns: Vec<(u8, Vec<String>)>,
        ancestors: Vec<String>,
        guards: Vec<(Bound<'_, PyAny>, Option<Bound<'_, PyAny>>)>,
        emoji_catalogs: Vec<Option<NativeEmojiCatalog>>,
        emoticon_catalog: Option<NativeEmoticonCatalog>,
        unicode_emoji_catalog: Option<NativeUnicodeEmojiCatalog>,
        link_policy: Option<NativeLinkPolicy>,
        channel_inputs: Option<NativeChannelInputs>,
        mention_inputs: Option<NativeMentionInputs>,
        media_inputs: Option<crate::python_media::MediaInputs>,
        html_counter: Option<usize>,
        autolink_spec: Option<NativeAutolinkSpec>,
    ) -> Option<(NativeInlineResult, Vec<String>, bool, Vec<String>)> {
        use markdown_native_inline::{Emoticons, NamedEmoji, Node, Pattern, Text};
        if (html_counter.is_none() && patterns.iter().any(|(kind, _)| *kind == 20))
            || (autolink_spec.is_none() && patterns.iter().any(|(kind, _)| *kind == 21))
            || patterns.len() != guards.len()
            || patterns.len() != emoji_catalogs.len()
        {
            return None;
        }
        let autolinks = match autolink_spec {
            None => None,
            Some((version, tlds, schemes, enable_file_links)) => {
                if version != crate::markdown_autolinks::UNICODE_VERSION {
                    return None;
                }
                Some(crate::markdown_autolinks::Matcher::cached(
                    crate::markdown_autolinks::Spec {
                        tlds,
                        schemes,
                        enable_file_links,
                    },
                )?)
            }
        };
        let text = |(value, atomic)| Text { value, atomic };
        let nodes = nodes
            .into_iter()
            .map(|(tag, lower_tag, content, tail, children)| Node {
                tag,
                lower_tag,
                text: content.map(text),
                tail: tail.map(text),
                children,
                attributes: Vec::new(),
            })
            .collect();
        let patterns = patterns
            .into_iter()
            .zip(emoji_catalogs)
            .map(|((kind, excludes), catalog)| Pattern {
                kind,
                excludes,
                emoji: catalog.map(|(unicode, realm)| NamedEmoji { unicode, realm }),
                emoticons: if kind == 9 {
                    emoticon_catalog
                        .as_ref()
                        .map(|(conversions, unicode)| Emoticons {
                            conversions: conversions.clone(),
                            unicode: unicode.clone(),
                        })
                } else {
                    None
                },
                unicode_emoji: if kind == 10 {
                    unicode_emoji_catalog
                        .as_ref()
                        .map(
                            |(regex_version, names)| crate::markdown_unicode_emoji::UnicodeEmoji {
                                regex_version: regex_version.clone(),
                                names: names.clone(),
                            },
                        )
                } else {
                    None
                },
                links: if matches!(kind, 11..=13 | 21) {
                    link_policy.as_ref().map(|(allowed, netloc, files, realm)| {
                        crate::markdown_links::Policy {
                            allowed_schemes: allowed.clone(),
                            netloc_schemes: netloc.clone(),
                            enable_file_links: *files,
                            realm_url: realm.clone(),
                        }
                    })
                } else {
                    None
                },
            })
            .collect::<Vec<_>>();
        let channels =
            channel_inputs.map(|(names, topic_with, empty_topic_name, unicode_decimal)| {
                markdown_native_inline::Channels {
                    names: names
                        .into_iter()
                        .map(|(name, (id, empty_topic_only))| {
                            (
                                name,
                                markdown_native_inline::Channel {
                                    id,
                                    empty_topic_only,
                                },
                            )
                        })
                        .collect(),
                    topic_with,
                    empty_topic_name,
                    unicode_decimal: unicode_decimal.into_iter().collect(),
                }
            });
        let mentions = mention_inputs.map(|(data, unicode_decimal, max_id_digits)| {
            let user = |(id, full_name, is_active)| markdown_native_inline::MentionUserSnapshot {
                id,
                full_name,
                is_active,
            };
            markdown_native_inline::Mentions {
                data: data.map(|(names, ids, groups)| markdown_native_inline::MentionData {
                    users_by_name: names
                        .into_iter()
                        .map(|(name, value)| (name, value.map(user)))
                        .collect(),
                    users_by_id: ids
                        .into_iter()
                        .map(|(id, value)| (id, user(value)))
                        .collect(),
                    groups_by_name: groups
                        .into_iter()
                        .map(|(name, value)| {
                            (
                                name,
                                value.map(|(id, display_name, deactivated)| {
                                    markdown_native_inline::MentionGroupSnapshot {
                                        id,
                                        display_name,
                                        deactivated,
                                    }
                                }),
                            )
                        })
                        .collect(),
                }),
                user_policy: markdown_inline_mentions::UserPolicy {
                    unicode_decimal,
                    max_id_digits,
                },
            }
        });
        let media = media_inputs.map(crate::python_media::into_media);
        let result = markdown_native_inline::run_with_inputs(
            nodes,
            &patterns,
            ancestors,
            markdown_native_inline::Inputs {
                media: media.as_ref(),
                channels: channels.as_ref(),
                mentions: mentions.as_ref(),
                html_counter,
                autolinks: autolinks.as_deref(),
            },
            |index, data| {
                let (compiled, text_presentation) = &guards[index];
                if let Some(text_presentation) = text_presentation {
                    for matched in compiled
                        .call_method1("finditer", (data,))
                        .ok()?
                        .try_iter()
                        .ok()?
                    {
                        let syntax = matched.ok()?.call_method1("group", ("syntax",)).ok()?;
                        if text_presentation
                            .call_method1("fullmatch", (syntax,))
                            .ok()?
                            .is_none()
                        {
                            return None;
                        }
                    }
                    Some(())
                } else {
                    compiled
                        .call_method1("search", (data,))
                        .ok()?
                        .is_none()
                        .then_some(())
                }
            },
        )?;
        Some(serialize_inline_plan(result))
    }

    #[pyfunction]
    fn scan_markdown_inline_stash(
        data: &Bound<'_, PyString>,
    ) -> Option<MarkdownInlineStashScanner> {
        Some(MarkdownInlineStashScanner {
            scanner: markdown_inline_stash::Scanner::new(data.to_str().ok()?.to_owned()),
        })
    }

    #[pyfunction]
    fn prettify_markdown_tree(nodes: &Bound<'_, PyAny>) -> Option<Vec<PrettifyChange>> {
        let nodes = nodes.extract::<Vec<PrettifyNode>>().ok()?;
        let nodes = nodes
            .into_iter()
            .map(
                |(tag, text, tail, block, children)| markdown_prettify::Node {
                    tag,
                    text,
                    tail,
                    block,
                    children,
                },
            )
            .collect();
        Some(
            markdown_prettify::prettify(nodes)?
                .into_iter()
                .map(|change| (change.node, change.text, change.atomic, change.value))
                .collect(),
        )
    }

    #[pyfunction]
    fn unescape_markdown_text<'py>(text: &Bound<'py, PyString>) -> Option<Bound<'py, PyString>> {
        let original = text.to_str().ok()?;
        let rendered = markdown_unescape::unescape(original)?;
        if text.is_exact_instance_of::<PyString>() && rendered == original {
            return Some(text.clone());
        }
        Some(PyString::new(text.py(), &rendered))
    }

    #[pyfunction]
    fn restore_markdown_html_stash(
        text: &Bound<'_, PyString>,
        stash: Vec<(Bound<'_, PyString>, bool)>,
    ) -> Option<String> {
        let stash = stash
            .iter()
            .map(|(html, block)| Some((html.to_str().ok()?, *block)))
            .collect::<Option<Vec<_>>>()?;
        markdown_postprocessing::restore_html(text.to_str().ok()?, &stash)
    }

    #[pyfunction]
    fn restore_markdown_ampersands<'py>(
        text: &Bound<'py, PyString>,
    ) -> Option<Bound<'py, PyString>> {
        let original = text.to_str().ok()?;
        let restored = markdown_postprocessing::restore_ampersands(original);
        if text.is_exact_instance_of::<PyString>() && restored == original {
            return Some(text.clone());
        }
        Some(PyString::new(text.py(), &restored))
    }

    #[pyfunction]
    fn serialize_markdown_html(
        nodes: &Bound<'_, PyAny>,
        empty_tags: Vec<String>,
    ) -> Option<String> {
        let nodes = nodes.extract::<Vec<SerializationNode>>().ok()?;
        let nodes = nodes
            .into_iter()
            .map(|(kind, tag, text, tail, attributes, children)| {
                Some(markdown_serialization::Node {
                    tag: match kind {
                        0 => markdown_serialization::Tag::Element(tag),
                        1 => markdown_serialization::Tag::Container,
                        2 => markdown_serialization::Tag::Comment,
                        3 => markdown_serialization::Tag::ProcessingInstruction,
                        _ => return None,
                    },
                    text,
                    tail,
                    attributes,
                    children,
                })
            })
            .collect::<Option<Vec<_>>>()?;
        markdown_serialization::serialize(&nodes, &empty_tags)
    }

    #[pyfunction]
    fn possible_markdown_channel_references(
        content: &Bound<'_, PyString>,
    ) -> Option<markdown_channel_links::ChannelLinkReferences> {
        Some(markdown_channel_links::possible_references(
            content.to_str().ok()?,
        ))
    }

    fn normalize_fenced_default<'a>(
        language: Option<&'a Bound<'_, PyString>>,
    ) -> Option<Option<(&'a str, String)>> {
        let Some(language) = language else {
            return Some(None);
        };
        if !language.is_exact_instance_of::<PyString>() {
            return None;
        }
        let original = language.to_str().ok()?;
        let lowered = if original.is_ascii() {
            original.to_ascii_lowercase()
        } else {
            // Ask Python once for Unicode lowercasing, including expansion,
            // context-sensitive sigma and its runtime Unicode version.
            let lower = language.call_method0("lower").ok()?;
            lower.cast::<PyString>().ok()?.to_str().ok()?.to_owned()
        };
        Some(Some((original, lowered)))
    }

    #[pyfunction]
    #[pyo3(signature = (content, default_language=None))]
    fn close_unclosed_markdown_fences(
        content: &Bound<'_, PyString>,
        default_language: Option<&Bound<'_, PyString>>,
    ) -> Option<String> {
        let content = content.to_str().ok()?;
        if content.len() > 8 * 1024 * 1024 {
            return None;
        }
        let default_language = normalize_fenced_default(default_language)?;
        Some(markdown_blocks::close_unclosed_fences(
            content,
            default_language
                .as_ref()
                .map(|(original, lower)| (*original, lower.as_str())),
        ))
    }

    #[pyfunction]
    fn get_unused_markdown_fence(content: &Bound<'_, PyString>) -> Option<String> {
        let content = content.to_str().ok()?;
        if content.len() > 8 * 1024 * 1024 {
            return None;
        }
        Some("`".repeat(reminder_quote::unused_fence_length(content.as_bytes())))
    }

    #[pyfunction]
    fn choose_fenced_quote_delimiter(content: &Bound<'_, PyAny>) -> Option<String> {
        let content = content.cast_exact::<PyString>().ok()?;
        let content = python_string_bytes(content)?;
        Some("`".repeat(reminder_quote::unused_fence_length(&content)))
    }

    #[pyfunction]
    fn parse_markdown_fence(
        line: &Bound<'_, PyString>,
    ) -> Option<(Option<String>, Option<String>, Option<String>)> {
        let line = line.to_str().ok()?;
        let parsed = markdown_fences::parse_fence_line(line);
        Some(match parsed {
            Some(parsed) => (
                Some(parsed.fence.to_owned()),
                parsed.language.map(str::to_owned),
                parsed.header.map(str::to_owned),
            ),
            None => (None, None, None),
        })
    }

    #[pyfunction]
    fn markdown_list_separator_positions(lines: Vec<Bound<'_, PyString>>) -> Option<Vec<usize>> {
        let lines = lines
            .iter()
            .map(|line| line.to_str().ok())
            .collect::<Option<Vec<_>>>()?;
        markdown_lists::separator_positions(&lines)
    }

    #[pyfunction]
    fn markdown_list_indent_parent(
        block: &Bound<'_, PyString>,
        tight_list: bool,
        descendants: Vec<Bound<'_, PyString>>,
    ) -> Option<(usize, usize)> {
        let descendants = descendants
            .iter()
            .map(|tag| tag.to_str().ok())
            .collect::<Option<Vec<_>>>()?;
        Some(markdown_list_indent::indent_parent(
            block.to_str().ok()?,
            tight_list,
            &descendants,
        ))
    }

    #[pyfunction]
    fn plan_markdown_list_indentation(
        block: &Bound<'_, PyString>,
        tight_list: bool,
        nodes: Vec<(Bound<'_, PyString>, bool)>,
    ) -> Option<(usize, &'static str, bool, String)> {
        let nodes = nodes
            .iter()
            .map(|(tag, has_text)| Some((tag.to_str().ok()?, *has_text)))
            .collect::<Option<Vec<_>>>()?;
        markdown_list_indent_layout::plan_list_indentation(block.to_str().ok()?, tight_list, &nodes)
    }

    #[pyfunction]
    fn detab_markdown_list(
        block: &Bound<'_, PyString>,
        level: &Bound<'_, PyInt>,
    ) -> Option<String> {
        markdown_list_indent::loose_detab(block.to_str().ok()?, level.extract().ok()?)
    }

    #[pyfunction]
    fn starts_markdown_list(block: &Bound<'_, PyString>, ordered: bool) -> Option<bool> {
        markdown_list_blocks::starts_list(block.to_str().ok()?, ordered)
    }

    #[pyfunction]
    fn split_markdown_list_items(
        block: &Bound<'_, PyString>,
        ordered: bool,
    ) -> Option<(Vec<String>, Option<String>)> {
        markdown_list_blocks::split_items(block.to_str().ok()?, ordered)
    }

    #[pyfunction]
    fn split_markdown_blockquote(block: &Bound<'_, PyString>) -> Option<(bool, String, String)> {
        Some(
            match markdown_blockquotes::split_quote(block.to_str().ok()?) {
                Some((before, quote)) => (true, before.to_owned(), quote.to_owned()),
                None => (false, String::new(), String::new()),
            },
        )
    }

    #[pyfunction]
    fn strip_markdown_blockquote_marker(line: &Bound<'_, PyString>) -> Option<String> {
        Some(markdown_blockquotes::strip_quote_marker(line.to_str().ok()?).to_owned())
    }

    #[pyfunction]
    fn format_markdown_fenced_quote(text: &Bound<'_, PyString>) -> Option<String> {
        if !text.is_exact_instance_of::<PyString>() {
            return None;
        }
        markdown_blockquotes::format_fenced_quote(text.to_str().ok()?)
    }

    #[pyfunction]
    fn prefix_exact_fenced_quote_lines<'py>(
        text: &Bound<'py, PyAny>,
    ) -> Option<Bound<'py, PyString>> {
        let text = text.cast_exact::<PyString>().ok()?;
        let bytes = python_string_bytes(text)?;
        let quoted = markdown_blockquotes::prefix_fenced_quote_lines(&bytes, isize::MAX as usize)?;
        python_string_from_bytes(text.py(), &quoted)
    }

    #[pyfunction]
    fn parse_markdown_heading(
        block: &Bound<'_, PyString>,
    ) -> Option<(Option<usize>, String, String, String)> {
        Some(
            match markdown_headings::parse_heading(block.to_str().ok()?) {
                Some(heading) => (
                    Some(heading.level),
                    heading.text.to_owned(),
                    heading.before.to_owned(),
                    heading.after.to_owned(),
                ),
                None => (None, String::new(), String::new(), String::new()),
            },
        )
    }

    #[pyfunction]
    fn parse_markdown_horizontal_rule(
        block: &Bound<'_, PyString>,
    ) -> Option<(bool, String, String)> {
        Some(
            match markdown_horizontal_rules::parse_horizontal_rule(block.to_str().ok()?) {
                Some(rule) => (true, rule.before.to_owned(), rule.after.to_owned()),
                None => (false, String::new(), String::new()),
            },
        )
    }

    #[pyfunction]
    fn split_markdown_table_row(row: &Bound<'_, PyString>, border: bool) -> Option<Vec<String>> {
        Some(markdown_tables::split_row(row.to_str().ok()?, border))
    }

    #[pyfunction]
    fn check_markdown_table(block: &Bound<'_, PyString>) -> Option<markdown_tables::TableCheck> {
        Some(markdown_tables::check_table(block.to_str().ok()?))
    }

    #[pyfunction]
    fn plan_markdown_table(
        block: &Bound<'_, PyString>,
        border: bool,
        separator: Vec<Bound<'_, PyString>>,
    ) -> Option<markdown_tables::TablePlan> {
        let separator = separator
            .iter()
            .map(|cell| cell.to_str().ok())
            .collect::<Option<Vec<_>>>()?;
        Some(markdown_tables::plan_table(
            block.to_str().ok()?,
            border,
            &separator,
        ))
    }

    #[pyfunction]
    fn plan_markdown_list(
        block: &Bound<'_, PyString>,
        ordered: bool,
        lazy: bool,
        parent_tag: &Bound<'_, PyString>,
        sibling_tag: Option<Bound<'_, PyString>>,
        previous_text: Option<Bound<'_, PyString>>,
        previous_tail: Option<Bound<'_, PyString>>,
    ) -> Option<markdown_list_layout::ListPlan> {
        markdown_list_layout::plan_list(
            block.to_str().ok()?,
            markdown_list_layout::ListContext {
                ordered,
                lazy,
                parent_tag: parent_tag.to_str().ok()?,
                sibling_tag: sibling_tag
                    .as_ref()
                    .map(|tag| tag.to_str())
                    .transpose()
                    .ok()?,
                previous_text: previous_text
                    .as_ref()
                    .map(|text| text.to_str())
                    .transpose()
                    .ok()?,
                previous_tail: previous_tail
                    .as_ref()
                    .map(|tail| tail.to_str())
                    .transpose()
                    .ok()?,
            },
        )
    }

    #[pyfunction]
    fn plan_markdown_code_block(
        block: &Bound<'_, PyString>,
        indentation: &Bound<'_, PyInt>,
        continuing: bool,
        previous_text: Option<Bound<'_, PyString>>,
    ) -> Option<(String, String)> {
        let previous_text = match previous_text.as_ref() {
            Some(text) => Some(text.to_str().ok()?),
            None => None,
        };
        Some(markdown_code_blocks::plan_code_block(
            block.to_str().ok()?,
            indentation.extract().ok()?,
            continuing,
            previous_text,
        ))
    }

    #[pyfunction]
    fn plan_markdown_empty_block(
        block: &Bound<'_, PyString>,
        continuing: bool,
        previous_text: Option<Bound<'_, PyString>>,
    ) -> Option<(Option<String>, String)> {
        let previous_text = match previous_text.as_ref() {
            Some(text) => Some(text.to_str().ok()?),
            None => None,
        };
        let (text, remainder) =
            markdown_code_blocks::plan_empty_block(block.to_str().ok()?, continuing, previous_text);
        Some((text, remainder.to_owned()))
    }

    #[pyfunction]
    fn find_markdown_inline_code(
        data: &Bound<'_, PyString>,
        position: &Bound<'_, PyInt>,
    ) -> Option<(Option<InlineCodeMatch>,)> {
        let data = data.to_str().ok()?;
        let position = position.extract::<isize>().ok()?.max(0) as usize;
        Some((
            markdown_code::find_inline_code(data, position).map(|matched| {
                (
                    matched.start,
                    matched.end,
                    matched.backslashes.map(str::to_owned),
                    matched.delimiter.map(str::to_owned),
                    matched.code.map(str::to_owned),
                    matched.rendered_text(),
                )
            }),
        ))
    }

    #[pyfunction]
    #[pyo3(signature = (channel_id, channel_name, topic_name, with_operand, message_id))]
    fn markdown_channel_link_fields(
        channel_id: &Bound<'_, PyInt>,
        channel_name: &Bound<'_, PyString>,
        topic_name: Option<Bound<'_, PyString>>,
        with_operand: Option<Bound<'_, PyInt>>,
        message_id: Option<Bound<'_, PyString>>,
    ) -> Option<ChannelLinkFields> {
        let topic_name = match topic_name.as_ref() {
            Some(topic) => Some(topic.to_str().ok()?),
            None => None,
        };
        let with_operand = match with_operand.as_ref() {
            Some(operand) => Some(operand.extract::<i64>().ok()?),
            None => None,
        };
        let message_id = match message_id.as_ref() {
            Some(id) => Some(id.to_str().ok()?),
            None => None,
        };
        let link = markdown_channel_spans::channel_link(
            channel_id.extract().ok()?,
            channel_name.to_str().ok()?,
            topic_name,
            with_operand,
            message_id,
        );
        Some((link.attributes, link.text, link.empty_topic, link.tail))
    }

    #[pyfunction]
    fn find_markdown_emphasis(
        data: &Bound<'_, PyString>,
        kind: &str,
    ) -> Option<(Option<EmphasisMatch>,)> {
        Some((
            markdown_emphasis::find_emphasis(data.to_str().ok()?, kind).map(|matched| {
                (
                    matched.before.to_owned(),
                    matched.marker,
                    matched.text.to_owned(),
                    matched.closing,
                    matched.after.to_owned(),
                )
            }),
        ))
    }

    #[pyfunction]
    fn plan_markdown_paragraph(
        block: &Bound<'_, PyString>,
        tight_list: bool,
        has_sibling: bool,
        previous_text: Option<Bound<'_, PyString>>,
    ) -> Option<(&'static str, String)> {
        let previous_text = match previous_text.as_ref() {
            Some(text) => Some(text.to_str().ok()?),
            None => None,
        };
        Some(markdown_paragraphs::plan_paragraph(
            block.to_str().ok()?,
            tight_list,
            has_sibling,
            previous_text,
        ))
    }

    #[pyfunction]
    fn normalize_markdown_whitespace<'py>(
        py: Python<'py>,
        lines: &Bound<'py, PyAny>,
        tab_length: i32,
    ) -> Option<Bound<'py, PyList>> {
        let lines = lines.cast_exact::<PyList>().ok()?;
        let strings = lines
            .iter()
            .map(|line| line.cast_into_exact::<PyString>().ok())
            .collect::<Option<Vec<_>>>()?;
        let encoded = strings
            .iter()
            .map(python_string_bytes)
            .collect::<Option<Vec<_>>>()?;
        let slices = encoded.iter().map(AsRef::as_ref).collect::<Vec<&[u8]>>();
        let joined = slices.join(&b'\n');
        let expanded = markdown_whitespace::normalize_joined_whitespace(&joined, tab_length);
        let result = markdown_whitespace::normalized_lines(&expanded)
            .map(|line| python_string_from_bytes(py, line))
            .collect::<Option<Vec<_>>>()?;
        PyList::new(py, result).ok()
    }

    #[pyfunction]
    #[pyo3(signature = (lines, default_language=None))]
    fn plan_markdown_blocks<'py>(
        py: Python<'py>,
        lines: Vec<Bound<'py, PyString>>,
        default_language: Option<Bound<'py, PyString>>,
    ) -> PyResult<Option<Bound<'py, PyList>>> {
        let Some(lines) = lines
            .iter()
            .map(|line| line.to_str().ok())
            .collect::<Option<Vec<_>>>()
        else {
            return Ok(None);
        };
        let Some(default_language) = normalize_fenced_default(default_language.as_ref()) else {
            return Ok(None);
        };
        Ok(Some(PyList::new(
            py,
            markdown_blocks::plan_lines(
                &lines,
                default_language
                    .as_ref()
                    .map(|(original, lower)| (*original, lower.as_str())),
            ),
        )?))
    }

    #[pyfunction]
    fn replay_markdown_block_plan<'py>(
        processor: &Bound<'py, PyAny>,
        output: &Bound<'py, PyList>,
        lines: Vec<Bound<'py, PyAny>>,
        default_language: Option<Bound<'py, PyAny>>,
        context: &Bound<'py, PyDict>,
        list_handlers: &Bound<'py, PyTuple>,
    ) -> PyResult<bool> {
        // Decline unsupported inputs before invoking any replay callback.
        if !output.is_exact_instance_of::<PyList>() {
            return Ok(false);
        }
        let Some(lines) = lines
            .iter()
            .map(|line| line.cast_exact::<PyString>().ok()?.to_str().ok())
            .collect::<Option<Vec<_>>>()
        else {
            return Ok(false);
        };
        let default_language = match default_language.as_ref() {
            Some(language) => match language.cast_exact::<PyString>() {
                Ok(language) => Some(language),
                Err(_) => return Ok(false),
            },
            None => None,
        };
        let Some(default_language) = normalize_fenced_default(default_language) else {
            return Ok(false);
        };
        let steps = markdown_blocks::plan_lines(
            &lines,
            default_language
                .as_ref()
                .map(|(original, lower)| (*original, lower.as_str())),
        );
        crate::python_highlight::apply_fenced_block_steps(
            processor,
            output,
            steps,
            context,
            list_handlers,
        )?;
        Ok(true)
    }

    #[pyfunction]
    fn possible_markdown_user_mentions(
        content: &Bound<'_, PyString>,
    ) -> Option<(HashSet<String>, bool, bool)> {
        Some(markdown_mentions::possible_user_mentions(
            content.to_str().ok()?,
        ))
    }

    #[pyfunction]
    fn possible_markdown_group_mentions(
        content: &Bound<'_, PyString>,
    ) -> Option<HashMap<String, &'static str>> {
        Some(markdown_mentions::possible_group_mentions(
            content.to_str().ok()?,
        ))
    }

    #[pyfunction]
    fn silence_quoted_markdown_mentions(content: &Bound<'_, PyString>) -> Option<String> {
        Some(markdown_mentions::silence_quoted_mentions(
            content.to_str().ok()?,
        ))
    }

    #[pyfunction]
    fn user_markdown_mention_fields(
        name: &Bound<'_, PyString>,
        silent: bool,
        user: Option<(i32, Bound<'_, PyString>, bool)>,
    ) -> Option<MentionSpanFields> {
        let user = match user.as_ref() {
            Some((id, name, is_active)) => Some(markdown_inline_mentions::MentionUser {
                id: *id,
                full_name: name.to_str().ok()?,
                is_active: *is_active,
            }),
            None => None,
        };
        Some(mention_span_fields(
            markdown_inline_mentions::user_mention_span(name.to_str().ok()?, silent, user),
        ))
    }

    #[pyfunction]
    fn group_markdown_mention_fields(
        group_id: i32,
        display_name: &Bound<'_, PyString>,
        silent: bool,
        deactivated: bool,
    ) -> Option<MentionSpanFields> {
        Some(mention_span_fields(Some(
            markdown_inline_mentions::group_mention_span(
                group_id,
                display_name.to_str().ok()?,
                silent,
                deactivated,
            ),
        )))
    }

    #[pyfunction]
    fn latest_markdown_topic_message(
        cursor: &Bound<'_, PyAny>,
        realm_id: i32,
        user_id: Option<i32>,
        recipient_id: i32,
        topic: &str,
        history_public_to_subscribers: bool,
    ) -> PyResult<Option<i64>> {
        if history_public_to_subscribers {
            cursor.call_method1(
                "execute",
                (
                    mention_data::PUBLIC_TOPIC_ANCHOR_SQL,
                    (realm_id, recipient_id, topic),
                ),
            )?;
        } else if let Some(user_id) = user_id {
            cursor.call_method1(
                "execute",
                (
                    mention_data::PROTECTED_TOPIC_ANCHOR_SQL,
                    (user_id, recipient_id, topic),
                ),
            )?;
        } else {
            return Ok(None);
        }
        let row: Option<(i64,)> = cursor.call_method0("fetchone")?.extract()?;
        Ok(row.map(|(id,)| id))
    }

    #[pyfunction]
    fn markdown_group_members(
        cursor: &Bound<'_, PyAny>,
        group_ids: Vec<i32>,
        realm_id: i32,
    ) -> PyResult<Vec<(i32, i32)>> {
        cursor.call_method1(
            "execute",
            (mention_data::GROUP_MEMBERS_SQL, (group_ids, realm_id)),
        )?;
        cursor.call_method0("fetchall")?.extract()
    }

    #[pyclass(frozen)]
    struct MarkdownAlertMatcher {
        matcher: markdown_alerts::AlertWordMatcher,
    }

    #[pymethods]
    impl MarkdownAlertMatcher {
        #[new]
        fn new(words_by_user: Vec<(i32, Vec<Vec<u8>>)>) -> PyResult<Self> {
            let matcher = markdown_alerts::AlertWordMatcher::new(words_by_user)
                .map_err(|error| PyValueError::new_err(error.to_string()))?;
            Ok(Self { matcher })
        }

        fn match_user_ids(&self, content: &[u8]) -> HashSet<i32> {
            self.matcher.match_user_ids(content)
        }
    }

    #[pyfunction]
    fn default_read_by_sender(client: &Bound<'_, PyString>, test_suite: bool) -> bool {
        client
            .to_str()
            .is_ok_and(|name| client_defaults::default_read_by_sender(name, test_suite))
    }

    #[pyfunction]
    fn parse_draft(value: &Bound<'_, PyString>) -> DraftParseResult<DraftInput> {
        draft_parse_result(value, draft_input::parse_draft)
    }

    #[pyfunction]
    fn parse_draft_batch(value: &Bound<'_, PyString>) -> DraftParseResult<Vec<DraftInput>> {
        draft_parse_result(value, draft_input::parse_draft_batch)
    }

    #[pyfunction]
    fn draft_channel_recipient(
        cursor: &Bound<'_, PyAny>,
        stream_id: i64,
        realm_id: i32,
        user_id: i32,
        is_guest: bool,
    ) -> PyResult<Option<(Option<i32>,)>> {
        // Drafts require channel content access, even for realm administrators.
        cursor.call_method1(
            "execute",
            (
                crate::draft_recipients::CHANNEL_RECIPIENT_SQL,
                (user_id, stream_id, realm_id, is_guest),
            ),
        )?;
        cursor.call_method0("fetchone")?.extract()
    }

    #[pyfunction]
    fn storage_size_difference(entries: Vec<(String, String, u64)>) -> i128 {
        bot_storage::storage_size_difference(&entries)
    }

    #[pyfunction]
    fn validate_draft_direct_recipients(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        realm_id: i32,
        email: &str,
        is_guest: bool,
        ids: Vec<i64>,
        cross_realm_bot_emails: HashSet<String>,
    ) -> PyResult<Option<(&'static str, String)>> {
        cursor.call_method1(
            "execute",
            (
                crate::draft_recipients::DIRECT_RECIPIENTS_SQL,
                (user_id, realm_id, is_guest, &ids),
            ),
        )?;
        type RecipientRow = (i32, i32, String, String, bool, bool);
        let rows: Vec<RecipientRow> = cursor.call_method0("fetchall")?.extract()?;
        let recipients: Vec<_> = rows
            .into_iter()
            .map(
                |(id, realm_id, delivery_email, email, deactivated, accessible)| DirectRecipient {
                    id,
                    realm_id,
                    delivery_email,
                    email,
                    deactivated,
                    accessible,
                },
            )
            .collect();
        Ok(validate_direct_recipients(
            user_id,
            realm_id,
            email,
            &ids,
            &cross_realm_bot_emails,
            &recipients,
        )
        .err()
        .map(|error| match error {
            DirectRecipientError::InvalidId(id) => ("invalid_id", id.to_string()),
            DirectRecipientError::Inaccessible => ("inaccessible", String::new()),
            DirectRecipientError::Deactivated(email) => ("deactivated", email.to_owned()),
            DirectRecipientError::CrossRealm => ("cross_realm", String::new()),
        }))
    }

    #[pyfunction]
    fn get_or_create_direct_message_group(
        cursor: &Bound<'_, PyAny>,
        member_ids: Vec<i32>,
        recipient_type: i16,
        default_color: &str,
    ) -> PyResult<(i64, Option<i32>)> {
        let member_hash = direct_message_group::member_hash(&member_ids);
        let group_size = i32::try_from(member_ids.len())
            .map_err(|_| PyValueError::new_err("Too many direct message group members"))?;
        let lookup = || -> PyResult<Option<(i64, Option<i32>)>> {
            cursor.call_method1(
                "execute",
                (
                    direct_message_group::FIND_GROUP_SQL,
                    (&member_hash, group_size),
                ),
            )?;
            cursor.call_method0("fetchone")?.extract()
        };
        if let Some(group) = lookup()? {
            return Ok(group);
        }

        cursor.call_method1(
            "execute",
            (
                direct_message_group::INSERT_GROUP_SQL,
                (&member_hash, group_size),
            ),
        )?;
        let inserted: Option<(i64,)> = cursor.call_method0("fetchone")?.extract()?;
        let Some((group_id,)) = inserted else {
            // PostgreSQL can observe the conflicting insert before it is
            // visible in this statement's snapshot. Read it in a new statement.
            return lookup()?
                .ok_or_else(|| PyValueError::new_err("Direct message group not found"));
        };

        cursor.call_method1(
            "execute",
            (
                direct_message_group::INITIALIZE_GROUP_SQL,
                (group_id, recipient_type, default_color, member_ids),
            ),
        )?;
        let (recipient_id,) = cursor.call_method0("fetchone")?.extract()?;
        Ok((group_id, Some(recipient_id)))
    }

    #[pyfunction]
    fn channel_permission_group_ids(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
    ) -> PyResult<HashSet<i32>> {
        cursor.call_method1(
            "execute",
            (send_permissions::RECURSIVE_GROUP_IDS_SQL, (user_id,)),
        )?;
        let rows: Vec<(i32,)> = cursor.call_method0("fetchall")?.extract()?;
        Ok(rows.into_iter().map(|(id,)| id).collect())
    }

    #[pyfunction]
    fn dm_group_has_active_member(
        cursor: &Bound<'_, PyAny>,
        group_id: i32,
        user_ids: Vec<i32>,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                send_permissions::HAS_ACTIVE_MEMBER_SQL,
                (group_id, user_ids),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn dm_has_inaccessible_recipients(
        cursor: &Bound<'_, PyAny>,
        target_user_ids: Vec<i32>,
        sender_id: i32,
        dm_group_type: i16,
        stream_type: i16,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                send_permissions::HAS_INACCESSIBLE_RECIPIENTS_SQL,
                (target_user_ids, sender_id, dm_group_type, stream_type),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn refresh_attachment_realm_public(
        cursor: &Bound<'_, PyAny>,
        attachment_id: i32,
        realm_id: i32,
    ) -> PyResult<()> {
        cursor.call_method1(
            "execute",
            (
                attachment_access::REFRESH_REALM_PUBLIC_SQL,
                (realm_id, attachment_id),
            ),
        )?;
        Ok(())
    }

    #[pyfunction]
    fn attachment_message_access(
        cursor: &Bound<'_, PyAny>,
        attachment_id: i32,
        user_id: i32,
        is_guest: bool,
        channel_type: i16,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                attachment_access::MESSAGE_ACCESS_SQL,
                (attachment_id, user_id, is_guest, channel_type),
            ),
        )?;
        let (allowed,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(allowed)
    }

    #[pyfunction]
    fn channel_has_active_subscribers(
        cursor: &Bound<'_, PyAny>,
        channel_id: i32,
        stream_type: i16,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                send_permissions::CHANNEL_HAS_ACTIVE_SUBSCRIBERS_SQL,
                (channel_id, stream_type),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn channel_topic_exists(
        cursor: &Bound<'_, PyAny>,
        realm_id: i32,
        recipient_id: i32,
        topic_name: &str,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                send_permissions::CHANNEL_TOPIC_EXISTS_SQL,
                (realm_id, recipient_id, topic_name),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn dm_conversation_exists(
        cursor: &Bound<'_, PyAny>,
        realm_id: i32,
        recipient_id: i32,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                send_permissions::CONVERSATION_EXISTS_SQL,
                (realm_id, recipient_id),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn normalize_draft_body(
        body: &str,
        max_length: usize,
    ) -> (Option<String>, Option<&'static str>) {
        match draft_text::normalize_body(body, max_length) {
            Ok(body) => (Some(body.into_owned()), None),
            Err(draft_text::BodyError::Empty) => (None, Some("empty")),
            Err(draft_text::BodyError::NullByte) => (None, Some("null_byte")),
        }
    }

    #[pyfunction]
    fn normalize_exact_draft_body<'py>(
        body: &Bound<'py, PyAny>,
        limit: &Bound<'py, PyAny>,
    ) -> Option<(Option<Bound<'py, PyString>>, Option<&'static str>)> {
        let body = body.cast_exact::<PyString>().ok()?;
        let limit = limit.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
        let bytes = python_string_bytes(body)?;
        Some(match draft_text::normalize_exact_body(&bytes, limit) {
            Ok(std::borrow::Cow::Borrowed(result)) if result.len() == bytes.len() => {
                (Some(body.clone()), None)
            }
            Ok(result) => (Some(python_string_from_bytes(body.py(), &result)?), None),
            Err(draft_text::BodyError::Empty) => (None, Some("empty")),
            Err(draft_text::BodyError::NullByte) => (None, Some("null_byte")),
        })
    }

    #[pyfunction]
    fn normalize_message_body(
        body: &Bound<'_, PyString>,
        max_length: usize,
    ) -> Option<(Option<String>, Option<&'static str>)> {
        Some(normalize_draft_body(body.to_str().ok()?, max_length))
    }

    #[pyfunction]
    fn has_system_group_permission(role: i16, provisional_member: bool, group: &str) -> bool {
        role_permissions::has_system_group_permission(role, provisional_member, group)
    }

    #[pyfunction]
    fn channel_topic_policy_violation(
        channel_policy: i16,
        realm_policy: i16,
        empty_topic: bool,
    ) -> Option<i16> {
        message_topic::channel_topic_policy_violation(channel_policy, realm_policy, empty_topic)
    }

    #[pyfunction]
    fn normalize_channel_message_topic(
        topic: &Bound<'_, PyString>,
        empty_topic_fallback: &str,
    ) -> Option<(Option<String>, Option<usize>)> {
        let topic = topic.to_str().ok()?;
        Some(
            match message_topic::normalize_channel_topic(topic, empty_topic_fallback) {
                Ok(topic) => (Some(topic.to_owned()), None),
                Err(index) => (None, Some(index)),
            },
        )
    }

    #[pyfunction]
    fn normalize_reminder_note<'py>(
        note: &'py Bound<'_, PyString>,
        max_length: usize,
    ) -> Option<(bool, &'py str)> {
        Some(
            match reminder_context::normalize_note(note.to_str().ok()?, max_length) {
                Some(note) => (true, note),
                None => (false, ""),
            },
        )
    }

    #[pyfunction]
    fn normalize_exact_reminder_note<'py>(
        note: &Bound<'py, PyAny>,
        max_length: &Bound<'py, PyAny>,
    ) -> Option<(bool, Bound<'py, PyString>)> {
        let note = note.cast_exact::<PyString>().ok()?;
        let max_length = max_length
            .cast_exact::<PyInt>()
            .ok()?
            .extract::<i64>()
            .ok()?;
        let bytes = python_string_bytes(note)?;
        match reminder_context::normalize_exact_note(&bytes, max_length) {
            Some(output) if output.len() == bytes.len() => Some((true, note.clone())),
            Some(output) => Some((true, python_string_from_bytes(note.py(), output)?)),
            None => Some((false, PyString::new(note.py(), ""))),
        }
    }

    #[pyfunction]
    fn reminder_widget_label(content_prefix: &Bound<'_, PyString>) -> Option<&'static str> {
        reminder_context::widget_label(content_prefix.to_str().ok()?)
    }

    #[pyfunction]
    fn reminder_channel_topic_link(
        channel: &Bound<'_, PyString>,
        topic: &Bound<'_, PyString>,
        url: &str,
    ) -> Option<String> {
        Some(reminder_context::channel_topic_link(
            channel.to_str().ok()?,
            topic.to_str().ok()?,
            url,
        ))
    }

    #[pyfunction]
    fn render_exact_reminder_topic_caption<'py>(
        channel: &Bound<'py, PyAny>,
        topic: &Bound<'py, PyAny>,
        url: &Bound<'py, PyAny>,
    ) -> Option<Bound<'py, PyString>> {
        let channel = channel.cast_exact::<PyString>().ok()?;
        let topic = topic.cast_exact::<PyString>().ok()?;
        let url = url.cast_exact::<PyString>().ok()?;
        let link = reminder_context::channel_topic_link_bytes(
            &python_string_bytes(channel)?,
            &python_string_bytes(topic)?,
            &python_string_bytes(url)?,
        );
        python_string_from_bytes(channel.py(), &link)
    }

    #[pyfunction]
    fn reminder_direct_recipient_mentions(
        sender_id: i32,
        recipients: Vec<(i32, Bound<'_, PyString>)>,
    ) -> Option<Vec<String>> {
        let recipients = recipients
            .iter()
            .map(|(id, name)| Some((*id, name.to_str().ok()?)))
            .collect::<Option<Vec<_>>>()?;
        Some(reminder_context::direct_recipient_mentions(
            sender_id, recipients,
        ))
    }

    #[pyfunction]
    fn exact_reminder_recipient_labels<'py>(
        sender: &Bound<'py, PyAny>,
        recipients: &Bound<'py, PyAny>,
    ) -> Option<Bound<'py, PyList>> {
        let signed =
            |value: &Bound<'py, PyAny>| value.cast_exact::<PyInt>().ok()?.extract::<i64>().ok();
        let sender = signed(sender)?;
        let recipients = recipients.cast_exact::<PyList>().ok()?;
        let mut labels = Vec::with_capacity(recipients.len());
        for recipient in recipients.iter() {
            let row = recipient.cast_exact::<PyDict>().ok()?;
            if row
                .iter()
                .any(|(key, _)| !key.is_exact_instance_of::<PyString>())
            {
                return None;
            }
            let id = signed(&row.get_item("id").ok()??)?;
            if id == sender {
                continue;
            }
            let name = row.get_item("full_name").ok()??;
            let name = python_string_bytes(name.cast_exact::<PyString>().ok()?)?;
            let mut label = b"@_**".to_vec();
            label.extend_from_slice(&name);
            label.push(b'|');
            label.extend_from_slice(id.to_string().as_bytes());
            label.extend_from_slice(b"**");
            labels.push(label);
        }
        // UTF-8 and surrogate-pass preserve code point order.
        labels.sort_unstable();
        let py = recipients.py();
        let output = labels
            .iter()
            .map(|label| python_string_from_bytes(py, label))
            .collect::<Option<Vec<_>>>()?;
        PyList::new(py, output).ok()
    }

    #[pyfunction]
    fn append_reminder_quote(
        prefix: &Bound<'_, PyString>,
        message: &Bound<'_, PyString>,
        max_length: usize,
    ) -> Option<String> {
        reminder_quote::append_quote(prefix.to_str().ok()?, message.to_str().ok()?, max_length)
    }

    #[pyfunction]
    fn append_exact_reminder_quote<'py>(
        prefix: &Bound<'py, PyAny>,
        message: &Bound<'py, PyAny>,
        limit: &Bound<'py, PyAny>,
    ) -> Option<Bound<'py, PyString>> {
        let prefix = prefix.cast_exact::<PyString>().ok()?;
        let message = message.cast_exact::<PyString>().ok()?;
        let limit = limit.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
        let content = reminder_quote::append_exact_quote(
            &python_string_bytes(prefix)?,
            &python_string_bytes(message)?,
            limit,
        )?;
        python_string_from_bytes(prefix.py(), &content)
    }

    #[pyfunction]
    fn normalize_draft_topic(topic: &str, max_length: usize) -> Option<String> {
        draft_text::normalize_topic(topic, max_length).map(std::borrow::Cow::into_owned)
    }

    #[pyfunction]
    fn normalize_exact_draft_topic<'py>(
        topic: &Bound<'py, PyAny>,
        limit: &Bound<'py, PyAny>,
    ) -> Option<(bool, Bound<'py, PyString>)> {
        let topic = topic.cast_exact::<PyString>().ok()?;
        let limit = i128::from(limit.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?);
        let text = python_string_bytes(topic)?;
        let lead = |byte: u8| byte & 0xc0 != 0x80;
        let length = text.iter().filter(|&&byte| lead(byte)).count() as i128;
        let suffix = b"...";
        let truncated = length > limit;
        let kept = if truncated {
            let stop = limit - suffix.len() as i128;
            let end = (if stop < 0 { length + stop } else { stop }).clamp(0, length) as usize;
            let byte = text
                .iter()
                .enumerate()
                .filter(|(_, byte)| lead(**byte))
                .nth(end)
                .map_or(text.len(), |(index, _)| index);
            &text[..byte]
        } else {
            &text
        };
        if kept.contains(&0) {
            return Some((false, PyString::new(topic.py(), "")));
        }
        if !truncated {
            return Some((true, topic.clone()));
        }
        let mut result = kept.to_vec();
        result.extend_from_slice(suffix);
        Some((true, python_string_from_bytes(topic.py(), &result)?))
    }

    #[pyfunction]
    fn normalize_draft_timestamp(timestamp: f64) -> (Option<i64>, bool) {
        match draft_timestamp::unix_microseconds(timestamp) {
            Ok(micros) => (Some(micros), false),
            Err(draft_timestamp::TimestampError::Negative) => (None, true),
            Err(draft_timestamp::TimestampError::OutOfRange) => (None, false),
        }
    }

    #[pyfunction(signature = (id, owner_id, channel_id, dm_members, topic, content, timestamp))]
    fn serialize_draft(
        id: i64,
        owner_id: i32,
        channel_id: Option<i32>,
        dm_members: Option<Vec<i32>>,
        topic: String,
        content: String,
        timestamp: i64,
    ) -> DraftFields {
        let recipients = match (channel_id, dm_members) {
            (Some(id), _) => Recipients::Channel(id),
            (None, Some(members)) => Recipients::Direct(members),
            (None, None) => Recipients::Unset,
        };
        DraftFields::new(id, owner_id, recipients, topic, content, timestamp)
    }

    #[pyfunction]
    fn insert_drafts(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        recipients: &Bound<'_, PyList>,
        topics: &Bound<'_, PyList>,
        contents: &Bound<'_, PyList>,
        edit_times: &Bound<'_, PyList>,
    ) -> PyResult<Vec<(i64,)>> {
        // Keep each ID paired with its input position regardless of RETURNING order.
        cursor.call_method1(
            "execute",
            (
                crate::draft_fields::INSERT_DRAFTS_SQL,
                (recipients, topics, contents, edit_times, user_id),
            ),
        )?;
        cursor.call_method0("fetchall")?.extract()
    }

    #[pyfunction]
    fn parse_scheduled_job_argument<'py>(
        py: Python<'py>,
        value: &Bound<'py, PyString>,
        kind: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let kind = match kind {
            "integer" => ArgumentKind::Integer,
            "recipients" => ArgumentKind::Recipients,
            "boolean" => ArgumentKind::Boolean,
            _ => return Err(PyValueError::new_err("Unknown scheduled job argument kind")),
        };
        let Some(argument) = value
            .to_str()
            .ok()
            .and_then(|value| scheduled_input::parse(value, kind))
        else {
            return Ok(None);
        };
        let value = match argument {
            ScheduledArgument::Integer(value) => value.into_bound_py_any(py)?,
            ScheduledArgument::Recipients(value) => value.into_bound_py_any(py)?,
            ScheduledArgument::Boolean(value) => value.into_bound_py_any(py)?,
        };
        Ok(Some(value))
    }

    #[pyfunction]
    fn scheduled_delivery_time_is_future(timestamp: &Bound<'_, PyAny>, now: i64) -> Option<bool> {
        scheduled_validation::delivery_time_is_future(timestamp.extract::<i64>().ok()?, now)
    }

    #[pyfunction]
    fn classify_scheduled_message_recipient(
        message_type: &str,
        list_length: Option<usize>,
    ) -> (Option<&'static str>, Option<&'static str>) {
        match scheduled_validation::classify_recipient(message_type, list_length) {
            Ok(RecipientKind::Channel) => (Some("stream"), None),
            Ok(RecipientKind::Direct) => (Some("private"), None),
            Err(RecipientError::ChannelList) => (None, Some("channel_list")),
            Err(RecipientError::DirectInteger) => (None, Some("direct_integer")),
            Err(RecipientError::EmptyDirectList) => (None, Some("empty_direct_list")),
            Err(RecipientError::InvalidType) => (None, Some("invalid_type")),
        }
    }

    #[pyfunction(signature = (id, to, topic, content, timestamp, failed, reminder_target_message_id=None))]
    fn serialize_scheduled_job(
        id: i64,
        to: Vec<i32>,
        topic: Option<String>,
        content: (String, String),
        timestamp: i64,
        failed: bool,
        reminder_target_message_id: Option<i32>,
    ) -> PyResult<ScheduledJob> {
        let recipient = if let Some(topic) = topic {
            let [channel_id] = to.as_slice() else {
                return Err(PyValueError::new_err(
                    "Expected exactly one scheduled channel ID",
                ));
            };
            Recipient::Channel {
                to: *channel_id,
                topic,
            }
        } else {
            Recipient::Direct { to }
        };
        let fields = MessageFields {
            recipient,
            content: content.0,
            rendered_content: content.1,
            scheduled_delivery_timestamp: timestamp,
            failed,
        };
        Ok(match reminder_target_message_id {
            Some(reminder_target_message_id) => ScheduledJob::Reminder {
                reminder_id: id,
                fields,
                reminder_target_message_id,
            },
            None => ScheduledJob::Message {
                scheduled_message_id: id,
                fields,
            },
        })
    }

    #[pyfunction]
    fn encode_narrow_hash_component(value: &Bound<'_, PyString>) -> Option<String> {
        Some(url_fragments::encode_hash_component(value.to_str().ok()?))
    }

    #[pyfunction]
    fn encode_narrow_user_ids(user_ids: &Bound<'_, PyAny>, with_operator: bool) -> Option<String> {
        let user_ids: Vec<i64> = user_ids
            .cast_exact::<PyList>()
            .ok()?
            .iter()
            .map(|id| id.cast_exact::<PyInt>().ok()?.extract().ok())
            .collect::<Option<_>>()?;
        Some(url_fragments::encode_user_ids(user_ids, with_operator))
    }

    #[pyfunction]
    fn local_markdown_upload_path<'py>(
        url: &'py Bound<'_, PyString>,
        realm_host: Option<&str>,
    ) -> Option<&'py str> {
        markdown_upload::local_upload_path(url.to_str().ok()?, realm_host)
    }

    #[pyfunction]
    fn reminder_user_message_exists(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        message_id: i64,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                reminder_access::USER_MESSAGE_EXISTS_SQL,
                (user_id, message_id),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn reminder_restricted_channel_access(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        is_guest: bool,
        recipient_id: i32,
        can_subscribe_group_id: i32,
        can_add_subscribers_group_id: i32,
        required_message_id: Option<i64>,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                reminder_access::RESTRICTED_CHANNEL_ACCESS_SQL,
                (
                    user_id,
                    is_guest,
                    required_message_id,
                    recipient_id,
                    can_subscribe_group_id,
                    can_add_subscribers_group_id,
                ),
            ),
        )?;
        let (exists,): (bool,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(exists)
    }

    #[pyfunction]
    fn link_scheduled_attachment(
        cursor: &Bound<'_, PyAny>,
        attachment_id: i64,
        scheduled_message_id: i64,
    ) -> PyResult<()> {
        cursor.call_method1(
            "execute",
            (
                scheduled_attachments::LINK_SQL,
                (attachment_id, scheduled_message_id),
            ),
        )?;
        Ok(())
    }

    #[pyfunction]
    fn unlink_scheduled_attachments(
        cursor: &Bound<'_, PyAny>,
        path_ids: Vec<String>,
        scheduled_message_id: i64,
    ) -> PyResult<()> {
        cursor.call_method1(
            "execute",
            (
                scheduled_attachments::UNLINK_SQL,
                (path_ids, scheduled_message_id),
            ),
        )?;
        Ok(())
    }

    #[pyfunction]
    fn insert_scheduled_jobs(
        cursor: &Bound<'_, PyAny>,
        columns: &Bound<'_, PyDict>,
    ) -> PyResult<Vec<(i64,)>> {
        cursor.call_method1(
            "execute",
            (
                "WITH jobs AS MATERIALIZED (
                    SELECT nextval(pg_get_serial_sequence('zulip.zerver_scheduledmessage', 'id')) AS id,
                           job.*
                    FROM unnest(
                        %(sender_id)s::integer[], %(recipient_id)s::integer[],
                        %(subject)s::text[], %(content)s::text[], %(rendered_content)s::text[],
                        %(sending_client_id)s::integer[], %(stream_id)s::bigint[],
                        %(realm_id)s::integer[], %(scheduled_timestamp)s::timestamptz[],
                        %(read_by_sender)s::boolean[], %(request_timestamp)s::timestamptz[],
                        %(delivery_type)s::smallint[], %(reminder_target_message_id)s::integer[],
                        %(reminder_note)s::text[]
                    ) WITH ORDINALITY AS job(
                        sender_id, recipient_id, subject, content, rendered_content, sending_client_id,
                        stream_id, realm_id, scheduled_timestamp, read_by_sender, request_timestamp,
                        delivery_type, reminder_target_message_id, reminder_note, position
                    )
                    ORDER BY position
                ), inserted AS (
                    INSERT INTO zulip.zerver_scheduledmessage (
                        id, sender_id, recipient_id, subject, content, rendered_content, sending_client_id,
                        stream_id, realm_id, scheduled_timestamp, read_by_sender, request_timestamp,
                        delivery_type, reminder_target_message_id, reminder_note,
                        delivered, has_attachment, failed
                    )
                    SELECT id, sender_id, recipient_id, subject, content, rendered_content, sending_client_id,
                           stream_id, realm_id, scheduled_timestamp, read_by_sender, request_timestamp,
                           delivery_type, reminder_target_message_id, reminder_note, false, false, false
                    FROM jobs
                    RETURNING id
                )
                SELECT inserted.id FROM inserted JOIN jobs USING (id)
                ORDER BY jobs.position",
                columns,
            ),
        )?;
        cursor.call_method0("fetchall")?.extract()
    }

    #[pyfunction]
    fn update_scheduled_message(
        cursor: &Bound<'_, PyAny>,
        fields: &Bound<'_, PyDict>,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                "UPDATE zulip.zerver_scheduledmessage
                 SET recipient_id = %(recipient_id)s, stream_id = %(stream_id)s,
                     subject = %(subject)s, content = %(content)s,
                     rendered_content = %(rendered_content)s, has_attachment = %(has_attachment)s,
                     scheduled_timestamp = %(scheduled_timestamp)s,
                     sending_client_id = %(sending_client_id)s,
                     failed = %(failed)s, failure_message = %(failure_message)s
                 WHERE id = %(id)s AND sender_id = %(sender_id)s
                       AND delivery_type = %(delivery_type)s AND NOT delivered",
                fields,
            ),
        )?;
        Ok(cursor.getattr("rowcount")?.extract::<i64>()? == 1)
    }

    #[pyfunction]
    fn lock_due_scheduled_job<'py>(
        queryset: &Bound<'py, PyAny>,
        now: &Bound<'py, PyAny>,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let rows = queryset.call_method1(
            "raw",
            (
                "SELECT * FROM zulip.zerver_scheduledmessage
                 WHERE scheduled_timestamp <= %s AND NOT delivered AND NOT failed
                 ORDER BY id LIMIT 1 FOR NO KEY UPDATE",
                (now,),
            ),
        )?;
        rows.try_iter()?.next().transpose()
    }

    #[pyfunction]
    fn mark_scheduled_job_delivered(
        cursor: &Bound<'_, PyAny>,
        id: i64,
        message_id: i32,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                "UPDATE zulip.zerver_scheduledmessage
                 SET delivered = true, delivered_message_id = %s WHERE id = %s",
                (message_id, id),
            ),
        )?;
        Ok(cursor.getattr("rowcount")?.extract::<i64>()? == 1)
    }

    #[pyfunction]
    fn mark_scheduled_job_failed(
        cursor: &Bound<'_, PyAny>,
        id: i64,
        failure_message: &str,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                "UPDATE zulip.zerver_scheduledmessage
                 SET failed = true, failure_message = %s WHERE id = %s",
                (failure_message, id),
            ),
        )?;
        Ok(cursor.getattr("rowcount")?.extract::<i64>()? == 1)
    }

    #[pyfunction]
    fn update_draft(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        draft_id: i64,
        recipient_id: Option<i32>,
        topic: &str,
        content: &str,
        last_edit_time: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                crate::draft_fields::UPDATE_DRAFT_SQL,
                (
                    recipient_id,
                    topic,
                    content,
                    last_edit_time,
                    user_id,
                    draft_id,
                ),
            ),
        )?;
        Ok(cursor.getattr("rowcount")?.extract::<i64>()? == 1)
    }

    #[pyfunction]
    fn format_user_status<'py>(
        py: Python<'py>,
        presence_enabled: bool,
        status_text: &str,
        emoji_name: &str,
        emoji_code: &str,
        reaction_type: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let status = user_status::UserStatus::from_row(
            presence_enabled,
            status_text,
            emoji_name,
            emoji_code,
            reaction_type,
        );
        let result = PyDict::new(py);
        if status.away {
            result.set_item("away", true)?;
        }
        if let Some(text) = status.text {
            result.set_item("status_text", text)?;
        }
        if let Some(emoji) = status.emoji {
            result.set_item("emoji_name", emoji.name)?;
            result.set_item("emoji_code", emoji.code)?;
            result.set_item("reaction_type", emoji.reaction_type)?;
        }
        Ok(result)
    }

    #[pyfunction]
    #[pyo3(signature = (away, status_text, emoji_name, emoji_code, emoji_type))]
    fn prepare_status_update<'a>(
        away: Option<bool>,
        status_text: Option<&str>,
        emoji_name: Option<&str>,
        emoji_code: Option<&'a str>,
        emoji_type: Option<&'a str>,
    ) -> (Option<&'a str>, Option<&'a str>, Option<&'static str>) {
        match user_status::prepare_status_update(
            away,
            status_text,
            emoji_name,
            emoji_code,
            emoji_type,
        ) {
            Ok((code, kind)) => (code, kind, None),
            Err(user_status::StatusUpdateError::NoValues) => (None, None, Some("no_values")),
            Err(user_status::StatusUpdateError::MissingEmojiName) => {
                (None, None, Some("missing_emoji_name"))
            }
        }
    }

    #[pyfunction]
    fn clean_alert_words(words: Vec<String>) -> Vec<String> {
        alert_words::clean_alert_words(&words)
            .map(str::to_owned)
            .collect()
    }

    #[pyfunction]
    fn select_new_alert_words<'py>(
        existing: Vec<Bound<'py, PyString>>,
        words: &Bound<'py, PyAny>,
    ) -> PyResult<Vec<Bound<'py, PyString>>> {
        // Use the running Python version's Unicode lowercase rules while Django
        // and Rust share this operation.
        let lowercase =
            |word: &Bound<'py, PyString>| word.call_method0("lower")?.extract::<String>();
        let existing = existing
            .iter()
            .map(lowercase)
            .collect::<PyResult<HashSet<_>>>()?;
        let words = words
            .try_iter()?
            .map(|word| {
                let word = word?.cast_into::<PyString>()?;
                Ok((lowercase(&word)?, word))
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(alert_words::select_new_alert_words(&existing, words))
    }

    #[pyfunction]
    fn delete_alert_words(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        lowercase_words: &Bound<'_, PyList>,
    ) -> PyResult<()> {
        cursor.call_method1(
            "execute",
            (
                "DELETE FROM zulip.zerver_alertword
                 WHERE user_profile_id = %s AND lower(word) = ANY(%s::text[])",
                (user_id, lowercase_words),
            ),
        )?;
        Ok(())
    }

    #[pyfunction]
    fn delete_memcached_keys(py: Python<'_>, address: &str, keys: Vec<String>) -> PyResult<()> {
        let address: SocketAddr = address
            .parse()
            .map_err(|_| PyValueError::new_err("Expected an IP address and Memcached port"))?;
        py.detach(|| memcached::delete_keys(address, &keys))
            .map_err(|error| match error.kind() {
                io::ErrorKind::InvalidInput => PyValueError::new_err(error.to_string()),
                _ => PyConnectionError::new_err(error.to_string()),
            })
    }

    #[pyfunction]
    fn pick_colors<'py>(
        py: Python<'py>,
        used_colors: HashSet<String>,
        color_map: HashMap<i64, String>,
        recipient_ids: Vec<i64>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let result = PyDict::new(py);
        for (recipient_id, color) in
            stream_color::pick_colors(&used_colors, &color_map, recipient_ids)
        {
            result.set_item(recipient_id, color)?;
        }
        Ok(result)
    }
}

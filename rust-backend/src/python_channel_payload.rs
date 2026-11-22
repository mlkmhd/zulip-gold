use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyString};

use crate::channel_traffic::average_weekly_messages;

fn plain_row<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    let row = value.cast_exact::<PyDict>().ok()?;
    row.iter()
        .all(|(key, _)| key.is_exact_instance_of::<PyString>())
        .then_some(row)
}

#[pyfunction]
pub(crate) fn assemble_channel_subscription<'py>(
    py: Python<'py>,
    subscription: &Bound<'py, PyAny>,
    stream: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let (Some(subscription), Some(stream)) = (plain_row(subscription), plain_row(stream)) else {
        return Ok(None);
    };
    let Some(muted) = subscription.get_item("is_muted")? else {
        return Ok(None);
    };
    let Ok(muted) = muted.cast_exact::<PyBool>() else {
        return Ok(None);
    };
    let muted = muted.extract::<bool>()?;
    let output = PyDict::new(py);
    for field in SUBSCRIPTION_FIELDS {
        let row = match field {
            "in_home_view" => {
                output.set_item(field, !muted)?;
                continue;
            }
            "audible_notifications"
            | "color"
            | "desktop_notifications"
            | "email_notifications"
            | "is_muted"
            | "pin_to_top"
            | "push_notifications"
            | "wildcard_mentions_notify" => subscription,
            _ => stream,
        };
        let Some(value) = row.get_item(field)? else {
            return Ok(None);
        };
        output.set_item(field, value)?;
    }
    Ok(Some(output))
}

#[pyfunction]
pub(crate) fn calculate_weekly_channel_traffic(
    age: &Bound<'_, PyAny>,
    traffic: &Bound<'_, PyAny>,
) -> Option<(Option<i128>,)> {
    let age = age.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    let traffic = traffic.cast_exact::<PyInt>().ok()?.extract::<i64>().ok()?;
    Some((average_weekly_messages(age, traffic),))
}

const SUBSCRIPTION_FIELDS: [&str; 41] = [
    "audible_notifications",
    "can_add_subscribers_group",
    "can_administer_channel_group",
    "can_create_topic_group",
    "can_delete_any_message_group",
    "can_delete_own_message_group",
    "can_move_messages_out_of_channel_group",
    "can_move_messages_within_channel_group",
    "can_send_message_group",
    "can_remove_subscribers_group",
    "can_resolve_topics_group",
    "can_subscribe_group",
    "color",
    "creator_id",
    "date_created",
    "default_push_notifications",
    "description",
    "desktop_notifications",
    "email_notifications",
    "first_message_id",
    "folder_id",
    "icon_url",
    "is_recently_active",
    "history_public_to_subscribers",
    "in_home_view",
    "invite_only",
    "is_announcement_only",
    "is_archived",
    "is_muted",
    "is_web_public",
    "message_retention_days",
    "name",
    "pin_to_top",
    "push_notifications",
    "rendered_description",
    "stream_id",
    "stream_post_policy",
    "stream_weekly_traffic",
    "subscriber_count",
    "topics_policy",
    "wildcard_mentions_notify",
];

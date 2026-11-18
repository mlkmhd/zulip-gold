#[cfg(feature = "python")]
use pyo3::{prelude::*, types::PyDict};

#[cfg_attr(feature = "http", derive(serde::Serialize), serde(tag = "type"))]
pub(crate) enum Recipient {
    #[cfg_attr(feature = "http", serde(rename = "stream"))]
    Channel { to: i32, topic: String },
    #[cfg_attr(feature = "http", serde(rename = "private"))]
    Direct { to: Vec<i32> },
}

#[cfg_attr(feature = "http", derive(serde::Serialize))]
pub(crate) struct MessageFields {
    #[cfg_attr(feature = "http", serde(flatten))]
    pub(crate) recipient: Recipient,
    pub(crate) content: String,
    pub(crate) rendered_content: String,
    pub(crate) scheduled_delivery_timestamp: i64,
    pub(crate) failed: bool,
}

#[cfg_attr(feature = "http", derive(serde::Serialize), serde(untagged))]
pub(crate) enum ScheduledJob {
    Message {
        scheduled_message_id: i64,
        #[cfg_attr(feature = "http", serde(flatten))]
        fields: MessageFields,
    },
    Reminder {
        reminder_id: i64,
        #[cfg_attr(feature = "http", serde(flatten))]
        fields: MessageFields,
        reminder_target_message_id: i32,
    },
}

#[cfg(feature = "python")]
impl<'py> IntoPyObject<'py> for ScheduledJob {
    type Target = PyDict;
    type Output = Bound<'py, PyDict>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        let output = PyDict::new(py);
        let (fields, reminder_target) = match self {
            Self::Message {
                scheduled_message_id,
                fields,
            } => {
                output.set_item("scheduled_message_id", scheduled_message_id)?;
                (fields, None)
            }
            Self::Reminder {
                reminder_id,
                fields,
                reminder_target_message_id,
            } => {
                output.set_item("reminder_id", reminder_id)?;
                (fields, Some(reminder_target_message_id))
            }
        };
        let topic = match fields.recipient {
            Recipient::Channel { to, topic } => {
                output.set_item("to", to)?;
                output.set_item("type", "stream")?;
                Some(topic)
            }
            Recipient::Direct { to } => {
                output.set_item("to", to)?;
                output.set_item("type", "private")?;
                None
            }
        };
        output.set_item("content", fields.content)?;
        output.set_item("rendered_content", fields.rendered_content)?;
        if let Some(topic) = topic {
            output.set_item("topic", topic)?;
        }
        output.set_item(
            "scheduled_delivery_timestamp",
            fields.scheduled_delivery_timestamp,
        )?;
        output.set_item("failed", fields.failed)?;
        if let Some(reminder_target) = reminder_target {
            output.set_item("reminder_target_message_id", reminder_target)?;
        }
        Ok(output)
    }
}

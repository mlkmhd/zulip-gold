use serde::Serialize;

#[derive(Serialize)]
#[serde(tag = "type")]
pub(crate) enum Recipient {
    #[serde(rename = "stream")]
    Channel { to: i32, topic: String },
    #[serde(rename = "private")]
    Direct { to: Vec<i32> },
}

#[derive(Serialize)]
pub(crate) struct MessageFields {
    #[serde(flatten)]
    pub(crate) recipient: Recipient,
    pub(crate) content: String,
    pub(crate) rendered_content: String,
    pub(crate) scheduled_delivery_timestamp: i64,
    pub(crate) failed: bool,
}

#[derive(Serialize)]
#[serde(untagged)]
pub(crate) enum ScheduledJob {
    Message {
        scheduled_message_id: i64,
        #[serde(flatten)]
        fields: MessageFields,
    },
    Reminder {
        reminder_id: i64,
        #[serde(flatten)]
        fields: MessageFields,
        reminder_target_message_id: i32,
    },
}

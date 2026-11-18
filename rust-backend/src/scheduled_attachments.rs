pub(crate) const LINK_SQL: &str = "
    INSERT INTO zulip.zerver_attachment_scheduled_messages
        (attachment_id, scheduledmessage_id)
    VALUES (%s, %s)
    ON CONFLICT DO NOTHING
";

// Keep the attachment row locks used by the ORM path before removing links.
pub(crate) const UNLINK_SQL: &str = "
    WITH locked_attachments AS (
        SELECT id FROM zulip.zerver_attachment
        WHERE path_id = ANY(%s::text[])
        FOR NO KEY UPDATE
    )
    DELETE FROM zulip.zerver_attachment_scheduled_messages AS link
    USING locked_attachments AS attachment
    WHERE link.attachment_id = attachment.id AND link.scheduledmessage_id = %s
";

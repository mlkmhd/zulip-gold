use std::{
    error::Error,
    fmt::Display,
    io,
    net::SocketAddr,
    path::Path as FsPath,
    sync::Arc,
    time::{Duration, SystemTime},
};

use axum::{
    Extension,
    extract::{ConnectInfo, Path, Request, State},
    http::{HeaderMap, HeaderValue, Method, StatusCode, Uri, header, uri::Authority},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use tokio_postgres::Client;

use super::{
    DraftWriteConfig, UserReadConfig, WebPostScheme,
    activity::{ActivityPublisher, client_name},
    alert_word_cache, alert_words, attachments, bot_storage, channel_details, channel_folders,
    channel_id,
    channel_members::{self, Outcome as ChannelMembers},
    channel_topics, custom_profile_fields,
    database::Database,
    draft_writes, drafts, export_consents,
    form::{Form, FormError},
    group_detail_request::{self, DetailKind, DetailReadError},
    group_listing_request::{self, ReadError as GroupReadError},
    linkifiers,
    locale::Locales,
    navigation_views::{self, NavigationViewUpdate, UpdateOutcome},
    rate_limit::{RateLimit, RateLimiter},
    read_receipts::{self, Outcome as ReadReceipts},
    realm_domains, realm_emoji,
    saved_snippets::{self, SnippetEvent},
    scheduled_messages::{self, JobKind},
    session::{SessionLookup, Sessions},
    subscribed_channels::{self, Outcome as SubscribedChannels},
    subscription_status::{self, Outcome as SubscriptionStatus},
    unicode_lowercase::UnicodeLowercase,
    user_status_read::{self, ReadOutcome as StatusReadOutcome},
};

use crate::draft_input::{self, ParseError};

pub(super) const ALERT_WORDS_PATH: &str = "/api/v1/users/me/alert_words";
pub(super) const ALERT_WORDS_SESSION_PATH: &str = "/json/users/me/alert_words";
const INCOMING_WEBHOOK_BOT: i16 = 2;
const DUPLICATE_NAVIGATION_VIEW: &str = "Navigation view already exists.";
const NEVER_CACHE: &str = "max-age=0, no-cache, no-store, must-revalidate, private";

#[derive(Clone, Copy)]
enum Endpoint {
    AlertWords,
    SavedSnippets,
    Drafts,
    Attachments,
    BotStorage,
    Linkifiers,
    RealmDomains,
    RealmEmoji,
    CustomProfileFields,
    NavigationViews,
    ChannelFolders,
    ChannelId,
    ChannelTopics(Option<i64>),
    UserStatus(Option<i32>),
    SubscriptionStatus(Option<i32>, Option<i64>),
    SubscribedChannels(Option<i32>),
    ChannelMembers(Option<i64>),
    ChannelDetails(Option<i64>),
    ChannelList,
    ReadReceipts(Option<i64>),
    ExportConsents,
    UserGroups,
    GroupDetail(DetailKind, Option<i64>),
    GroupMembership(Option<i64>, Option<i32>),
    ScheduledMessages,
    Reminders,
}

#[derive(Clone, Copy)]
enum WriteOperation<'a> {
    CreateSnippet,
    CreateDrafts,
    EditDraft(Option<i64>),
    CreateNavigationView,
    EditNavigationView(&'a str),
    DeleteNavigationView(&'a str),
    EditSnippet(Option<i64>),
    DeleteSnippet(Option<i64>),
    AddAlertWords,
    RemoveAlertWords,
    DeleteDraft(Option<i64>),
    DeleteScheduledJob(JobKind, Option<i64>),
    DeleteBotStorage,
    UpdateBotStorage,
}

impl WriteOperation<'_> {
    fn is_post(self) -> bool {
        matches!(
            self,
            Self::CreateSnippet
                | Self::CreateDrafts
                | Self::CreateNavigationView
                | Self::AddAlertWords
        )
    }
}

impl Endpoint {
    fn activity_name(self) -> &'static str {
        match self {
            Self::AlertWords => "list_alert_words",
            Self::SavedSnippets => "get_saved_snippets",
            Self::Drafts => "fetch_drafts",
            Self::Attachments => "list_by_user",
            Self::BotStorage => "get_storage",
            Self::Linkifiers => "list_linkifiers",
            Self::RealmDomains => "list_realm_domains",
            Self::RealmEmoji => "list_emoji",
            Self::CustomProfileFields => "list_realm_custom_profile_fields",
            Self::NavigationViews => "get_navigation_views",
            Self::ChannelFolders => "get_channel_folders",
            Self::ChannelId => "json_get_stream_id",
            Self::ChannelTopics(_) => "get_topics_backend",
            Self::UserStatus(_) => "get_status_backend",
            Self::SubscriptionStatus(..) => "get_subscription_backend",
            Self::SubscribedChannels(_) => "get_subscribed_channels_backend",
            Self::ChannelMembers(_) => "get_subscribers_backend",
            Self::ChannelDetails(_) => "get_stream_backend",
            Self::ChannelList => "get_streams_backend",
            Self::ReadReceipts(_) => "read_receipts",
            Self::ExportConsents => "get_users_export_consents",
            Self::UserGroups => "get_user_groups",
            Self::GroupDetail(DetailKind::Members, _) => "get_user_group_members",
            Self::GroupDetail(DetailKind::Subgroups, _) => "get_subgroups_of_user_group",
            Self::GroupMembership(..) => "get_is_user_group_member",
            Self::ScheduledMessages => "fetch_scheduled_messages",
            Self::Reminders => "fetch_reminders",
        }
    }

    async fn read(
        self,
        database: &Client,
        user_id: i32,
        realm_id: i32,
        query: Option<&str>,
        state: &UserReads,
    ) -> Result<String, ApiError> {
        let (field, serialized) = match self {
            Self::AlertWords => (
                "alert_words",
                serde_json::to_string(&alert_words::list(database, user_id).await?),
            ),
            Self::SavedSnippets => (
                "saved_snippets",
                serde_json::to_string(&saved_snippets::list(database, user_id).await?),
            ),
            Self::Linkifiers => (
                "linkifiers",
                serde_json::to_string(&linkifiers::list(database, realm_id).await?),
            ),
            Self::RealmDomains => (
                "domains",
                serde_json::to_string(&realm_domains::list(database, realm_id).await?),
            ),
            Self::RealmEmoji => (
                "emoji",
                serde_json::to_string(
                    &realm_emoji::list(
                        database,
                        realm_id,
                        state
                            .emoji_url_base
                            .as_deref()
                            .expect("emoji route configured"),
                    )
                    .await?,
                ),
            ),
            Self::CustomProfileFields => (
                "custom_fields",
                serde_json::to_string(&custom_profile_fields::list(database, realm_id).await?),
            ),
            Self::NavigationViews => (
                "navigation_views",
                serde_json::to_string(&navigation_views::list(database, user_id).await?),
            ),
            Self::UserGroups => {
                let listing =
                    group_listing_request::read_authenticated(database, user_id, realm_id, query)
                        .await
                        .map_err(|err| {
                            group_read_error(err, group_listing_request::INCLUDE_DEACTIVATED)
                        })?;
                let object =
                    serde_json::to_string(&listing).expect("group listing can serialize to JSON");
                // The common read response adds result/msg to these object fields.
                return Ok(object[1..object.len() - 1].to_owned());
            }
            Self::GroupDetail(kind, id) => {
                let listing = group_detail_request::read_authenticated_group_detail(
                    database, user_id, realm_id, id, kind, query,
                )
                .await
                .map_err(|error| group_detail_error(error, kind.direct_parameter()))?;
                let object =
                    serde_json::to_string(&listing).expect("group details can serialize to JSON");
                return Ok(object[1..object.len() - 1].to_owned());
            }
            Self::GroupMembership(group_id, target_user_id) => {
                let membership = group_detail_request::read_authenticated_group_membership(
                    database,
                    user_id,
                    realm_id,
                    group_id,
                    target_user_id,
                    query,
                )
                .await
                .map_err(|error| {
                    group_detail_error(error, group_detail_request::DIRECT_MEMBER_ONLY)
                })?;
                let object = serde_json::to_string(&membership)
                    .expect("group membership can serialize to JSON");
                return Ok(object[1..object.len() - 1].to_owned());
            }
            Self::ChannelId => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let name = form
                    .value(channel_id::STREAM)
                    .ok_or(ApiError::Form(FormError::Missing(channel_id::STREAM)))?;
                let stream_id = channel_id::find(database, user_id, realm_id, name)
                    .await?
                    .ok_or_else(|| ApiError::InvalidChannelName(name.to_owned()))?;
                let extra = ignored_parameters(&form, &[channel_id::STREAM, "client"]);
                return Ok(format!("\"stream_id\":{stream_id}{extra}"));
            }
            Self::ChannelTopics(channel_id) => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let allow_empty = form
                    .json_bool(channel_topics::ALLOW_EMPTY)
                    .map_err(ApiError::Form)?
                    .unwrap_or(false);
                let topics = channel_topics::read(
                    database,
                    user_id,
                    realm_id,
                    channel_id.ok_or(ApiError::InvalidChannelId)?,
                    allow_empty,
                    state
                        .channel_topics_lowercase
                        .as_deref()
                        .expect("topic route configured"),
                )
                .await?
                .ok_or(ApiError::InvalidChannelId)?;
                let topics = serde_json::to_string(&topics).expect("topics can serialize to JSON");
                let extra = ignored_parameters(&form, &[channel_topics::ALLOW_EMPTY, "client"]);
                return Ok(format!("\"topics\":{topics}{extra}"));
            }
            Self::UserStatus(target) => {
                let Some(target) = target else {
                    return Err(ApiError::MissingTargetUser);
                };
                match user_status_read::read(database, user_id, realm_id, target).await? {
                    StatusReadOutcome::NotFound => return Err(ApiError::MissingTargetUser),
                    StatusReadOutcome::Deactivated => return Err(ApiError::DeactivatedTargetUser),
                    StatusReadOutcome::Restricted => return Err(ApiError::RestrictedTargetUser),
                    StatusReadOutcome::Visible(status) => {
                        ("status", serde_json::to_string(&status))
                    }
                }
            }
            Self::SubscriptionStatus(target, channel) => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let subscribed =
                    match subscription_status::read(database, user_id, realm_id, target, channel)
                        .await?
                    {
                        SubscriptionStatus::NoSuchUser => return Err(ApiError::MissingTargetUser),
                        SubscriptionStatus::DeactivatedUser => {
                            return Err(ApiError::DeactivatedTargetUser);
                        }
                        SubscriptionStatus::RestrictedUser => {
                            return Err(ApiError::RestrictedTargetUser);
                        }
                        SubscriptionStatus::InvalidChannel => {
                            return Err(ApiError::InvalidChannelId);
                        }
                        SubscriptionStatus::Subscribed(subscribed) => subscribed,
                    };
                // The two IDs are path-only; matching query names are still unsupported.
                let extra = ignored_parameters(&form, &["client"]);
                return Ok(format!("\"is_subscribed\":{subscribed}{extra}"));
            }
            Self::SubscribedChannels(target) => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let target = target.ok_or(ApiError::MissingTargetUser)?;
                let ids =
                    match subscribed_channels::read(database, user_id, realm_id, target).await? {
                        SubscribedChannels::NoSuchUser => return Err(ApiError::MissingTargetUser),
                        SubscribedChannels::DeactivatedUser => {
                            return Err(ApiError::DeactivatedTargetUser);
                        }
                        SubscribedChannels::RestrictedUser => {
                            return Err(ApiError::RestrictedTargetUser);
                        }
                        SubscribedChannels::Visible(ids) => ids,
                    };
                let ids = serde_json::to_string(&ids).expect("channel IDs can serialize to JSON");
                let extra = ignored_parameters(&form, &["client"]);
                return Ok(format!("\"subscribed_channel_ids\":{ids}{extra}"));
            }
            Self::ChannelMembers(channel) => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let channel = channel.ok_or(ApiError::InvalidChannelId)?;
                let roster =
                    match channel_members::read(database, user_id, realm_id, channel).await? {
                        ChannelMembers::InvalidChannel => return Err(ApiError::InvalidChannelId),
                        ChannelMembers::PublicUnavailable => {
                            return Err(ApiError::PublicSubscribersUnavailable);
                        }
                        ChannelMembers::PrivateUnavailable => {
                            return Err(ApiError::PrivateSubscribersUnavailable);
                        }
                        ChannelMembers::Visible(roster) => roster,
                    };
                let roster =
                    serde_json::to_string(&roster).expect("user IDs can serialize to JSON");
                let extra = ignored_parameters(&form, &["client"]);
                return Ok(format!("\"subscribers\":{roster}{extra}"));
            }
            Self::ChannelList => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let options = channel_details::ListOptions::parse(&form).map_err(ApiError::Form)?;
                let streams = channel_details::list(database, user_id, realm_id, options)
                    .await
                    .map_err(ApiError::database_unavailable)?;
                let streams = match streams {
                    channel_details::ListOutcome::Listed(streams) => streams,
                    channel_details::ListOutcome::MissingBotOwner => {
                        return Err(ApiError::ChannelListMissingBotOwner);
                    }
                };
                let extra = ignored_parameters(&form, channel_details::LIST_PARAMETERS);
                return Ok(format!("\"streams\":{streams}{extra}"));
            }
            Self::ChannelDetails(channel) => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let channel = channel.ok_or(ApiError::InvalidChannelId)?;
                let stream = channel_details::read(database, user_id, realm_id, channel)
                    .await
                    .map_err(ApiError::database_unavailable)?
                    .ok_or(ApiError::InvalidChannelId)?;
                let extra = ignored_parameters(&form, &["client"]);
                return Ok(format!("\"stream\":{stream}{extra}"));
            }
            Self::ExportConsents => {
                // Django's untyped endpoint ignores extra query parameters.
                let consents = export_consents::read(database, user_id, realm_id)
                    .await?
                    .ok_or(ApiError::AdministratorRequired)?;
                ("export_consents", serde_json::to_string(&consents))
            }
            Self::ReadReceipts(id) => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let id = id.ok_or(ApiError::InvalidMessage)?;
                let users = match read_receipts::read(database, user_id, realm_id, id).await? {
                    ReadReceipts::NoMessage => return Err(ApiError::InvalidMessage),
                    ReadReceipts::Disabled => return Err(ApiError::ReadReceiptsDisabled),
                    ReadReceipts::Visible(users) => users,
                };
                let users = serde_json::to_string(&users).expect("user IDs can serialize to JSON");
                let extra = ignored_parameters(&form, &["client"]);
                return Ok(format!("\"user_ids\":{users}{extra}"));
            }
            Self::ChannelFolders => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let folders = channel_folders::list(
                    database,
                    realm_id,
                    channel_folders::include_archived(&form).map_err(ApiError::Form)?,
                )
                .await?;
                let folders =
                    serde_json::to_string(&folders).expect("database fields can serialize to JSON");
                let extra =
                    ignored_parameters(&form, &[channel_folders::INCLUDE_ARCHIVED, "client"]);
                return Ok(format!("\"channel_folders\":{folders}{extra}"));
            }
            Self::BotStorage => {
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let storage = bot_storage::read(
                    database,
                    user_id,
                    bot_storage::keys(&form).map_err(ApiError::Form)?,
                )
                .await?
                .ok_or(ApiError::BotStorageKeyNotFound)?;
                let storage =
                    serde_json::to_string(&storage).expect("database fields can serialize to JSON");
                let extra = ignored_parameters(&form, &[bot_storage::KEYS, "client"]);
                return Ok(format!("\"storage\":{storage}{extra}"));
            }
            Self::ScheduledMessages | Self::Reminders => {
                let (field, kind) = match self {
                    Self::ScheduledMessages => ("scheduled_messages", JobKind::Message),
                    _ => ("reminders", JobKind::Reminder),
                };
                let form = Form::from_query(query).map_err(ApiError::Form)?;
                let entries = scheduled_messages::list(database, user_id, realm_id, kind).await?;
                let entries =
                    serde_json::to_string(&entries).expect("database fields can serialize to JSON");
                let extra = ignored_parameters(&form, &["client"]);
                return Ok(format!("\"{field}\":{entries}{extra}"));
            }
            Self::Drafts => {
                require_draft_synchronization(database, user_id).await?;
                let drafts = drafts::list(database, user_id).await?;
                let list =
                    serde_json::to_string(&drafts).expect("database fields can serialize to JSON");
                return Ok(format!("\"count\":{},\"drafts\":{list}", drafts.len()));
            }
            Self::Attachments => {
                let (attachments, upload_space_used) =
                    attachments::list(database, user_id, realm_id).await?;
                let list = serde_json::to_string(&attachments)
                    .expect("database fields can serialize to JSON");
                return Ok(format!(
                    "\"attachments\":{list},\"upload_space_used\":{upload_space_used}"
                ));
            }
        };
        let value = serialized.expect("database fields can serialize to JSON");
        Ok(format!("\"{field}\":{value}"))
    }
}

async fn require_draft_synchronization(database: &Client, user_id: i32) -> Result<(), ApiError> {
    if drafts::synchronization_enabled(database, user_id).await? {
        Ok(())
    } else {
        Err(ApiError::DraftSyncDisabled)
    }
}

pub(super) async fn ready(State(state): State<UserReads>) -> Response {
    let ready = state.sessions.is_some()
        && tokio::time::timeout(Duration::from_secs(2), async {
            let Ok(database) = state.database.client().await else {
                return false;
            };
            if database
                .batch_execute(
                    "SELECT u.id, r.id, w.word, s.session_key
                 FROM zulip.zerver_userprofile u, zulip.zerver_realm r,
                      zulip.zerver_alertword w, zulip.django_session s LIMIT 0",
                )
                .await
                .is_err()
            {
                return false;
            }
            if let Some(limiter) = &state.rate_limiter
                && !limiter.ping().await
            {
                return false;
            }
            if let Some(path) = state.alert_word_cache_manifest
                && !alert_word_cache::ready(path).await
            {
                return false;
            }
            if let Some(activity) = &state.activity {
                return activity.ready().await;
            }
            true
        })
        .await
        .unwrap_or(false);
    let (status, body) = if ready {
        (StatusCode::OK, "{\"ready\":true}\n")
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, "{\"ready\":false}\n")
    };
    (
        status,
        [
            (header::CONTENT_TYPE, "application/json"),
            (header::CACHE_CONTROL, "no-store"),
        ],
        body,
    )
        .into_response()
}

#[derive(Clone)]
pub(super) struct UserReads {
    database: Arc<Database>,
    external_host: Arc<str>,
    emoji_url_base: Option<Arc<str>>,
    locales: Locales,
    rate_limiter: Option<RateLimiter>,
    activity: Option<ActivityPublisher>,
    alert_word_cache_manifest: Option<Arc<FsPath>>,
    alert_word_lowercase: Option<Arc<UnicodeLowercase>>,
    channel_topics_lowercase: Option<Arc<UnicodeLowercase>>,
    sessions: Option<Arc<Sessions>>,
    web_post_scheme: Option<WebPostScheme>,
    saved_snippet_max_content_length: Option<usize>,
    bot_storage_size_limit: Option<u64>,
    draft_writes: Option<Arc<DraftWriteConfig>>,
}

impl UserReads {
    pub(super) async fn connect(
        settings: UserReadConfig<'_>,
        locales: Locales,
    ) -> Result<Self, Box<dyn Error>> {
        let sessions = settings
            .session
            .map(Sessions::new)
            .transpose()?
            .map(Arc::new);
        if !settings.external_host.split('.').all(valid_label) {
            return Err("ZULIP_RUST_EXTERNAL_HOST must be a hostname without a port".into());
        }
        if settings
            .emoji_url_base
            .is_some_and(|base| !base.ends_with('/') || base.contains(['?', '#']))
        {
            return Err("ZULIP_RUST_EMOJI_URL_BASE must be a URL prefix ending in /".into());
        }
        let alert_word_lowercase = settings
            .alert_word_lowercase_file
            .map(UnicodeLowercase::load)
            .transpose()?
            .map(Arc::new);
        if alert_word_lowercase.is_some() && settings.alert_word_cache_manifest.is_none() {
            return Err("Alert-word DELETE requires ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST".into());
        }
        let channel_topics_lowercase = settings
            .channel_topics_lowercase_file
            .map(UnicodeLowercase::load)
            .transpose()?
            .map(Arc::new);
        let read_only = settings.activity.is_none()
            && !settings.bot_storage_deletes
            && settings.bot_storage_size_limit.is_none();
        let database = Database::connect(settings.database, read_only).await?;
        let rate_limiter = match settings.rate_limit {
            Some(config) => Some(RateLimiter::connect(config).await?),
            None => None,
        };
        let saved_snippet_writes = settings.saved_snippet_max_content_length.is_some();
        let activity = match settings.activity {
            Some(config) => Some(
                ActivityPublisher::connect(
                    config,
                    saved_snippet_writes
                        || settings.draft_deletes
                        || settings.draft_writes.is_some()
                        || settings.reminder_deletes
                        || settings.scheduled_message_deletes
                        || settings.navigation_view_posts
                        || settings.navigation_view_details
                        || alert_word_lowercase.is_some(),
                )
                .await?,
            ),
            None if alert_word_lowercase.is_some() => {
                return Err("Alert-word DELETE requires ZULIP_RUST_AMQP_URL".into());
            }
            None if saved_snippet_writes => {
                return Err("Saved snippet writes require ZULIP_RUST_AMQP_URL".into());
            }
            None if settings.draft_writes.is_some() => {
                return Err("Draft POST and PATCH require ZULIP_RUST_AMQP_URL".into());
            }
            None if settings.draft_deletes => {
                return Err("Draft DELETE requires ZULIP_RUST_AMQP_URL".into());
            }
            None if settings.reminder_deletes => {
                return Err("Reminder DELETE requires ZULIP_RUST_AMQP_URL".into());
            }
            None if settings.scheduled_message_deletes => {
                return Err("Scheduled message DELETE requires ZULIP_RUST_AMQP_URL".into());
            }
            None if settings.navigation_view_posts || settings.navigation_view_details => {
                return Err("Navigation view writes require ZULIP_RUST_AMQP_URL".into());
            }
            None => None,
        };
        Ok(Self {
            database: Arc::new(database),
            external_host: Arc::from(settings.external_host.to_ascii_lowercase()),
            emoji_url_base: settings.emoji_url_base.map(Arc::from),
            locales,
            rate_limiter,
            activity,
            alert_word_cache_manifest: settings.alert_word_cache_manifest.map(Arc::from),
            alert_word_lowercase,
            channel_topics_lowercase,
            sessions,
            web_post_scheme: settings.web_post_scheme,
            saved_snippet_max_content_length: settings.saved_snippet_max_content_length,
            bot_storage_size_limit: settings.bot_storage_size_limit,
            draft_writes: settings.draft_writes.map(Arc::new),
        })
    }

    async fn authenticate(
        &self,
        headers: &HeaderMap,
    ) -> Result<(Arc<Client>, AuthenticatedUser), ApiError> {
        let subdomain = subdomain(headers, &self.external_host).ok_or(ApiError::InvalidHost)?;
        let auth = credentials(headers)?;
        let database = self
            .database
            .client()
            .await
            .map_err(ApiError::database_unavailable)?;
        let user = database
            .query_opt(
                "SELECT u.id, u.realm_id, u.delivery_email, u.is_active, u.bot_type, u.is_bot, u.rate_limits,
                        r.string_id, r.deactivated
                 FROM zulip.zerver_userprofile AS u
                 JOIN zulip.zerver_realm AS r ON r.id = u.realm_id
                 WHERE u.api_key = $1",
                &[&auth.key],
            )
            .await?
            .ok_or(ApiError::Unauthorized("Invalid API key"))?;
        let email: &str = user.try_get("delivery_email")?;
        if email.to_lowercase() != auth.email.to_lowercase() {
            return Err(ApiError::Unauthorized("Invalid API key"));
        }
        if user.try_get("deactivated")? {
            return Err(ApiError::Unauthorized(
                "This organization has been deactivated",
            ));
        }
        if !user.try_get::<_, bool>("is_active")? {
            return Err(ApiError::Unauthorized("Account is deactivated"));
        }
        if user.try_get::<_, &str>("string_id")? != subdomain {
            return Err(ApiError::Unauthorized(
                "Account is not associated with this subdomain",
            ));
        }
        if user.try_get::<_, Option<i16>>("bot_type")? == Some(INCOMING_WEBHOOK_BOT) {
            return Err(ApiError::Unauthorized(
                "This API is not available to incoming webhook bots.",
            ));
        }

        let user = AuthenticatedUser {
            id: user.try_get("id")?,
            realm_id: user.try_get("realm_id")?,
            rate_limits: user.try_get("rate_limits")?,
            is_bot: user.try_get("is_bot")?,
        };
        Ok((database, user))
    }

    async fn change_alert_words(
        &self,
        form: &Form,
        user_id: i32,
        realm_id: i32,
        adding: bool,
    ) -> Result<(), ApiError> {
        let lowercase = self
            .alert_word_lowercase
            .as_deref()
            .expect("alert-word write route configured");
        let mut words =
            alert_words::parse_words(form, adding.then_some(100)).map_err(ApiError::Form)?;
        if !adding {
            for word in &mut words {
                *word = lowercase.lower(word);
            }
        }
        let manifest = self
            .alert_word_cache_manifest
            .as_deref()
            .expect("alert-word writes require a cache manifest");
        let activity = self
            .activity
            .as_ref()
            .expect("alert-word writes require RabbitMQ");
        let queue = activity
            .notification_queue(realm_id, user_id)
            .await
            .ok_or(ApiError::NotificationUnavailable)?;

        let remaining = {
            let mut writer = self
                .database
                .writer()
                .await
                .map_err(ApiError::database_unavailable)?;
            let result = if adding {
                alert_words::add(&mut writer, manifest, user_id, realm_id, &words, lowercase).await
            } else {
                alert_words::remove(&mut writer, manifest, user_id, realm_id, &words).await
            };
            result.map_err(|error| {
                if error.is::<io::Error>() {
                    eprintln!("Alert-word cache invalidation failed: {error}");
                    ApiError::AlertWordCacheUnavailable
                } else {
                    ApiError::database_unavailable(error)
                }
            })?
        };
        activity
            .notify_user(
                queue,
                user_id,
                serde_json::json!({
                    "type": "alert_words",
                    "alert_words": remaining,
                }),
            )
            .await
            .map_err(|error| {
                eprintln!("Could not confirm alert-word notification: {error}");
                ApiError::NotificationUnavailable
            })
    }

    async fn limit(
        &self,
        user_id: i32,
        rules: &str,
        result: &mut Option<RateLimit>,
    ) -> Result<(), ApiError> {
        if let Some(limiter) = &self.rate_limiter {
            let limit = limiter.check(user_id, rules).await.map_err(|error| {
                eprintln!("User read rate limit check failed: {error}");
                ApiError::RateLimiterUnavailable
            })?;
            let retry = limit.retry_after;
            *result = Some(limit);
            if let Some(seconds) = retry {
                return Err(ApiError::RateLimited(seconds));
            }
        }
        Ok(())
    }

    async fn record_activity(
        &self,
        database: &Client,
        user_id: i32,
        activity_name: &str,
        client: &str,
    ) -> Result<(), ApiError> {
        if let Some(activity) = &self.activity {
            activity
                .record(database, user_id, activity_name, client)
                .await
                .map_err(|error| {
                    eprintln!("User read activity failed: {error}");
                    ApiError::ActivityUnavailable
                })?;
        }
        Ok(())
    }
}

struct AuthenticatedUser {
    id: i32,
    realm_id: i32,
    rate_limits: String,
    is_bot: bool,
}

fn valid_label(label: &str) -> bool {
    !label.is_empty()
        && label.len() <= 63
        && !label.starts_with('-')
        && !label.ends_with('-')
        && label
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn subdomain(headers: &HeaderMap, external_host: &str) -> Option<String> {
    let mut hosts = headers.get_all(header::HOST).iter();
    let host: Authority = hosts.next()?.to_str().ok()?.parse().ok()?;
    if hosts.next().is_some() || host.as_str().contains('@') {
        return None;
    }
    let host = host.host().to_ascii_lowercase();
    if host == external_host {
        return Some(String::new());
    }
    let (subdomain, domain) = host.split_once('.')?;
    if domain != external_host || !valid_label(subdomain) {
        return None;
    }
    Some(if subdomain == "www" {
        String::new()
    } else {
        subdomain.to_owned()
    })
}

struct Credentials {
    email: String,
    key: String,
}

fn credentials(headers: &HeaderMap) -> Result<Credentials, ApiError> {
    let mut values = headers.get_all(header::AUTHORIZATION).iter();
    let Some(header) = values.next() else {
        return Err(ApiError::MissingAuthentication);
    };
    let malformed = || ApiError::Unauthorized("Invalid authorization header for basic auth");
    let mut fields = header.to_str().map_err(|_| malformed())?.split_whitespace();
    let (Some(scheme), Some(encoded), None, None) =
        (fields.next(), fields.next(), fields.next(), values.next())
    else {
        return Err(malformed());
    };
    if !scheme.eq_ignore_ascii_case("basic") {
        return Err(ApiError::BadRequest(
            "This endpoint requires HTTP basic authentication.",
        ));
    }
    let decoded = STANDARD.decode(encoded).map_err(|_| malformed())?;
    let decoded = std::str::from_utf8(&decoded).map_err(|_| malformed())?;
    let (email, key) = decoded.split_once(':').ok_or_else(malformed)?;
    if key.contains(':') {
        return Err(malformed());
    }
    let key = key.trim();
    if key.len() != 32 || !key.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
        return Err(ApiError::Unauthorized("Malformed API key"));
    }
    Ok(Credentials {
        email: email.trim().to_owned(),
        key: key.to_owned(),
    })
}

enum ApiError {
    MissingAuthentication,
    Csrf(String),
    Unauthorized(&'static str),
    BadRequest(&'static str),
    UserGroupGuest,
    AdministratorRequired,
    InvalidUserGroup,
    DeactivatedUserGroup,
    MissingTargetUser,
    DeactivatedTargetUser,
    RestrictedTargetUser,
    BotRequired,
    BotStorageKeyNotFound,
    BotStorageLimitExceeded { excess: i128, limit: u64 },
    InvalidNavigationView(&'static str),
    InvalidChannelName(String),
    InvalidChannelId,
    ChannelListMissingBotOwner,
    InvalidMessage,
    ReadReceiptsDisabled,
    PublicSubscribersUnavailable,
    PrivateSubscribersUnavailable,
    NavigationViewNotFound,
    DeactivatedUser,
    DeactivatedRealm,
    InvalidHost,
    DatabaseUnavailable,
    RateLimiterUnavailable,
    ActivityUnavailable,
    NotificationUnavailable,
    AlertWordCacheUnavailable,
    Form(FormError),
    UnsupportedMethodOverride(Endpoint),
    SnippetNotFound,
    DraftSyncDisabled,
    DraftNotFound,
    DraftSchema(drafts::SchemaError),
    DraftWrite(draft_writes::WriteError),
    ScheduledJobNotFound(JobKind),
    RateLimited(f64),
}

impl From<tokio_postgres::Error> for ApiError {
    fn from(error: tokio_postgres::Error) -> Self {
        Self::database_unavailable(error)
    }
}

impl From<draft_writes::WriteError> for ApiError {
    fn from(error: draft_writes::WriteError) -> Self {
        match error {
            draft_writes::WriteError::Database(error) => Self::from(error),
            other => Self::DraftWrite(other),
        }
    }
}

fn group_detail_error(error: DetailReadError, parameter: &'static str) -> ApiError {
    match error {
        DetailReadError::Request(error) => group_read_error(error, parameter),
        DetailReadError::InvalidGroup => ApiError::InvalidUserGroup,
        DetailReadError::DeactivatedGroup => ApiError::DeactivatedUserGroup,
        DetailReadError::MissingTargetUser => ApiError::MissingTargetUser,
        DetailReadError::DeactivatedTargetUser => ApiError::DeactivatedTargetUser,
    }
}

fn group_read_error(error: GroupReadError, parameter: &'static str) -> ApiError {
    match error {
        GroupReadError::Guest => ApiError::UserGroupGuest,
        GroupReadError::InvalidJson => ApiError::Form(FormError::InvalidJson(parameter)),
        GroupReadError::ExpectedBoolean => ApiError::Form(FormError::ExpectedBoolean(parameter)),
        GroupReadError::MalformedQuery => ApiError::Form(FormError::Malformed),
        // The previously authenticated account or realm assignment may no
        // longer be available by the time we load its current role. Fail closed.
        GroupReadError::PrincipalUnavailable => ApiError::MissingAuthentication,
        GroupReadError::Database(error) => ApiError::database_unavailable(error),
    }
}

fn draft_schema_error(parameter: &'static str, error: ParseError) -> ApiError {
    match error {
        ParseError::Unsupported => ApiError::Form(FormError::InvalidJson(parameter)),
        ParseError::Schema(kind, path) => {
            ApiError::DraftSchema(drafts::SchemaError::new(parameter, kind, path))
        }
    }
}

impl ApiError {
    fn database_unavailable(error: impl Display) -> Self {
        eprintln!("User read PostgreSQL request failed: {error}");
        Self::DatabaseUnavailable
    }
}

// Django chooses the most specific accepted range for each response type,
// then compares their quality, specificity, and order in the Accept header.
fn prefers_html(headers: &HeaderMap) -> bool {
    let Some(accept) = headers
        .get(header::ACCEPT)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };
    let score = |media_type, wildcard| {
        accept
            .split(',')
            .enumerate()
            .filter_map(|(index, range)| {
                let mut parts = range.split(';');
                let specificity = match parts.next()?.trim() {
                    range if range == media_type => 2,
                    range if range == wildcard => 1,
                    "*/*" => 0,
                    _ => return None,
                };
                let mut quality = 1.0;
                for parameter in parts {
                    if let Some((name, value)) = parameter.trim().split_once('=') {
                        if !name.trim().eq_ignore_ascii_case("q") {
                            return None;
                        }
                        quality = value.trim().trim_matches('"').parse::<f32>().unwrap_or(1.0);
                    }
                }
                if !(0.0..=1.0).contains(&quality) {
                    quality = 1.0;
                }
                (quality > 0.0).then_some((specificity, quality, std::cmp::Reverse(index)))
            })
            .max_by(|a, b| a.partial_cmp(b).expect("quality is finite"))
            .map(|(specificity, quality, index)| (quality, specificity, index))
    };
    score("text/html", "text/*") > score("application/json", "application/*")
}

pub(super) async fn get(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    respond(state, peer, uri, headers, None, None).await
}

pub(super) async fn get_group_detail(
    State(state): State<UserReads>,
    Extension(kind): Extension<DetailKind>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    // Django's int path converter accepts one or more ASCII decimal digits.
    // Keep an overflowing numeric path distinguishable from an unmatched path.
    if !numeric_group_path(&id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::GroupDetail(kind, id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

fn numeric_group_path(id: &str) -> bool {
    !id.is_empty() && id.bytes().all(|byte| byte.is_ascii_digit())
}

pub(super) async fn get_group_membership(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path((group_id, target_id)): Path<(String, String)>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&group_id) || !numeric_group_path(&target_id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::GroupMembership(group_id.parse().ok(), target_id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn get_channel_topics(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(channel_id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&channel_id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::ChannelTopics(channel_id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn get_user_status(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::UserStatus(id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn get_subscription_status(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path((user_id, stream_id)): Path<(String, String)>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&user_id) || !numeric_group_path(&stream_id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::SubscriptionStatus(user_id.parse().ok(), stream_id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn get_subscribed_channels(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::SubscribedChannels(id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn get_channel_members(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::ChannelMembers(id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn get_channel_list(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    respond(state, peer, uri, headers, None, Some(Endpoint::ChannelList)).await
}

pub(super) async fn get_channel_details(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::ChannelDetails(id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}
pub(super) async fn get_export_consents(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    respond(
        state,
        peer,
        uri,
        headers,
        None,
        Some(Endpoint::ExportConsents),
    )
    .await
}

pub(super) async fn get_read_receipts(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if !numeric_group_path(&id) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let endpoint = Endpoint::ReadReceipts(id.parse().ok());
    respond(state, peer, uri, headers, None, Some(endpoint)).await
}

pub(super) async fn add_alert_words(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::AddAlertWords).await
}

pub(super) async fn remove_alert_words(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::RemoveAlertWords).await
}

pub(super) async fn create_saved_snippet(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::CreateSnippet).await
}

pub(super) async fn create_navigation_view(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::CreateNavigationView).await
}

pub(super) async fn write_navigation_view_detail(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(fragment): Path<String>,
    request: Request,
) -> Response {
    let write = if request.method() == Method::DELETE {
        WriteOperation::DeleteNavigationView(&fragment)
    } else {
        WriteOperation::EditNavigationView(&fragment)
    };
    respond_write(state, peer, request, write).await
}

pub(super) async fn delete_bot_storage(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::DeleteBotStorage).await
}

pub(super) async fn update_bot_storage(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::UpdateBotStorage).await
}

pub(super) async fn create_drafts(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
) -> Response {
    respond_write(state, peer, request, WriteOperation::CreateDrafts).await
}

pub(super) async fn edit_draft(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    request: Request,
) -> Response {
    if !id.bytes().all(|byte| byte.is_ascii_digit()) {
        return StatusCode::NOT_FOUND.into_response();
    }
    respond_write(
        state,
        peer,
        request,
        WriteOperation::EditDraft(id.parse().ok()),
    )
    .await
}

pub(super) async fn delete_draft(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    request: Request,
) -> Response {
    if id.is_empty() || !id.bytes().all(|byte| byte.is_ascii_digit()) {
        return StatusCode::NOT_FOUND.into_response();
    }
    respond_write(
        state,
        peer,
        request,
        WriteOperation::DeleteDraft(id.parse().ok()),
    )
    .await
}

pub(super) async fn delete_scheduled_job(
    State(state): State<UserReads>,
    Extension(kind): Extension<JobKind>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    request: Request,
) -> Response {
    if id.is_empty() || !id.bytes().all(|byte| byte.is_ascii_digit()) {
        return StatusCode::NOT_FOUND.into_response();
    }
    respond_write(
        state,
        peer,
        request,
        WriteOperation::DeleteScheduledJob(kind, id.parse().ok()),
    )
    .await
}

pub(super) async fn write_saved_snippet_detail(
    State(state): State<UserReads>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(id): Path<String>,
    request: Request,
) -> Response {
    if id.is_empty() || !id.bytes().all(|byte| byte.is_ascii_digit()) {
        return StatusCode::NOT_FOUND.into_response();
    }
    // An integer outside the storage range can still be used with a no-op edit.
    let id = id.parse::<i64>().ok();
    let write = if request.method() == Method::DELETE {
        WriteOperation::DeleteSnippet(id)
    } else {
        WriteOperation::EditSnippet(id)
    };
    respond_write(state, peer, request, write).await
}

async fn respond_write(
    state: UserReads,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    request: Request,
    write: WriteOperation<'_>,
) -> Response {
    let (uri, headers) = (request.uri().clone(), request.headers().clone());
    let form = Form::parse(request).await;
    respond(state, peer, uri, headers, Some((write, form)), None).await
}

async fn respond(
    state: UserReads,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    uri: Uri,
    headers: HeaderMap,
    form: Option<(WriteOperation<'_>, Result<Form, FormError>)>,
    endpoint: Option<Endpoint>,
) -> Response {
    let language = Locales::for_request(&headers);
    let endpoint = endpoint.unwrap_or_else(|| match uri.path() {
        saved_snippets::PATH | saved_snippets::SESSION_PATH => Endpoint::SavedSnippets,
        drafts::PATH | drafts::SESSION_PATH => Endpoint::Drafts,
        attachments::PATH | attachments::SESSION_PATH => Endpoint::Attachments,
        bot_storage::PATH | bot_storage::SESSION_PATH => Endpoint::BotStorage,
        linkifiers::PATH | linkifiers::SESSION_PATH => Endpoint::Linkifiers,
        realm_domains::PATH | realm_domains::SESSION_PATH => Endpoint::RealmDomains,
        realm_emoji::PATH | realm_emoji::SESSION_PATH => Endpoint::RealmEmoji,
        custom_profile_fields::PATH | custom_profile_fields::SESSION_PATH => {
            Endpoint::CustomProfileFields
        }
        navigation_views::PATH | navigation_views::SESSION_PATH => Endpoint::NavigationViews,
        channel_folders::PATH | channel_folders::SESSION_PATH => Endpoint::ChannelFolders,
        channel_id::PATH | channel_id::SESSION_PATH => Endpoint::ChannelId,
        group_listing_request::PATH | group_listing_request::SESSION_PATH => Endpoint::UserGroups,
        scheduled_messages::PATH | scheduled_messages::SESSION_PATH => Endpoint::ScheduledMessages,
        scheduled_messages::REMINDERS_PATH | scheduled_messages::SESSION_REMINDERS_PATH => {
            Endpoint::Reminders
        }
        _ => Endpoint::AlertWords,
    });
    let browser_session = if uri.path().starts_with("/json/") {
        state.sessions.as_deref()
    } else {
        None
    };
    let browser = browser_session.is_some();
    let mut clear_session_cookie = false;
    let mut rate_limit = None;
    let data = async {
        let (requested_write, form) = match form {
            Some((write, form)) => (Some(write), Some(form.map_err(ApiError::Form)?)),
            None => (None, None),
        };
        let write = match (
            requested_write,
            form.as_ref().and_then(|form| form.post_value("method")),
        ) {
            (Some(write), Some("GET" | "HEAD")) if write.is_post() => None,
            (Some(WriteOperation::AddAlertWords), Some("DELETE")) => {
                Some(WriteOperation::RemoveAlertWords)
            }
            (Some(write), Some(method)) if write.is_post() && method != "POST" => {
                return Err(ApiError::UnsupportedMethodOverride(endpoint));
            }
            _ => requested_write,
        };
        let activity_name = match write {
            Some(WriteOperation::CreateDrafts) => "create_drafts",
            Some(WriteOperation::EditDraft(_)) => "edit_draft",
            Some(WriteOperation::CreateSnippet) => "create_saved_snippet",
            Some(WriteOperation::CreateNavigationView) => "add_navigation_view",
            Some(WriteOperation::EditNavigationView(_)) => "update_navigation_view",
            Some(WriteOperation::DeleteNavigationView(_)) => "remove_navigation_view",
            Some(WriteOperation::EditSnippet(_)) => "edit_saved_snippet",
            Some(WriteOperation::DeleteSnippet(_)) => "delete_saved_snippet",
            Some(WriteOperation::AddAlertWords) => "add_alert_words",
            Some(WriteOperation::RemoveAlertWords) => "remove_alert_words",
            Some(WriteOperation::DeleteDraft(_)) => "delete_draft",
            Some(WriteOperation::DeleteScheduledJob(kind, _)) => kind.delete_activity(),
            Some(WriteOperation::DeleteBotStorage) => "remove_storage",
            Some(WriteOperation::UpdateBotStorage) => "update_storage",
            None => endpoint.activity_name(),
        };
        let form_client = if requested_write.is_some_and(WriteOperation::is_post) {
            form.as_ref().and_then(|form| form.value("client"))
        } else {
            None
        };
        let (database, user_id, realm_id, is_bot) = if let Some(sessions) = browser_session {
            let subdomain =
                subdomain(&headers, &state.external_host).ok_or(ApiError::InvalidHost)?;
            let (user, database) = match sessions
                .lookup(&state.database, &headers)
                .await
                .map_err(ApiError::database_unavailable)?
            {
                SessionLookup::User(user, database) => (user, database),
                SessionLookup::Anonymous { clear_cookie } => {
                    clear_session_cookie = clear_cookie;
                    return Err(ApiError::MissingAuthentication);
                }
            };
            if requested_write.is_some() {
                // Django only accepts the form token on a physical POST.
                let form_token = form
                    .as_ref()
                    .filter(|_| requested_write.is_some_and(WriteOperation::is_post))
                    .and_then(|form| form.post_value("csrfmiddlewaretoken"));
                state
                    .web_post_scheme
                    .expect("web write route configured")
                    .validate(&headers, sessions.csrf_cookie(&headers), form_token)
                    .map_err(ApiError::Csrf)?;
            }
            // The browser dispatcher checks the quota before account state and activity.
            state
                .limit(user.id, &user.rate_limits, &mut rate_limit)
                .await?;
            if user.realm_deactivated {
                return Err(ApiError::DeactivatedRealm);
            }
            if !user.active {
                return Err(ApiError::DeactivatedUser);
            }
            if user.subdomain != subdomain {
                return Err(ApiError::BadRequest(
                    "Account is not associated with this subdomain",
                ));
            }
            if user.bot_type == Some(INCOMING_WEBHOOK_BOT) {
                return Err(ApiError::BadRequest(
                    "Webhook bots can only access webhooks",
                ));
            }
            let client = client_name(form_client, uri.query(), &headers);
            let client = if client.starts_with("Zulip") {
                &client
            } else {
                "website"
            };
            state
                .record_activity(&database, user.id, activity_name, client)
                .await?;
            (database, user.id, user.realm_id, user.is_bot)
        } else {
            let (database, user) = state.authenticate(&headers).await?;
            let client = client_name(form_client, uri.query(), &headers);
            // Basic Auth records activity before the quota check.
            state
                .record_activity(&database, user.id, activity_name, &client)
                .await?;
            if !state.rate_limiter.as_ref().is_some_and(|limiter| {
                limiter.exempts_internal_client(
                    &client,
                    peer.map(|Extension(ConnectInfo(address))| address.ip()),
                    &headers,
                )
            }) {
                state
                    .limit(user.id, &user.rate_limits, &mut rate_limit)
                    .await?;
            }
            (database, user.id, user.realm_id, user.is_bot)
        };
        if matches!(endpoint, Endpoint::BotStorage) && !is_bot {
            return Err(ApiError::BotRequired);
        }
        if let (Some(write), Some(form)) = (write, form) {
            if matches!(write, WriteOperation::DeleteBotStorage | WriteOperation::UpdateBotStorage) {
                let parameter = if matches!(write, WriteOperation::UpdateBotStorage) {
                    let storage = bot_storage::storage(&form).map_err(ApiError::Form)?;
                    let limit = state.bot_storage_size_limit.expect("bot storage update route configured");
                    let mut writer = state.database.writer().await.map_err(ApiError::database_unavailable)?;
                    if let Some(excess) = bot_storage::update(&mut writer, user_id, storage, limit).await? {
                        return Err(ApiError::BotStorageLimitExceeded { excess, limit });
                    }
                    bot_storage::STORAGE
                } else {
                    let keys = bot_storage::keys(&form).map_err(ApiError::Form)?;
                    let mut writer = state.database.writer().await.map_err(ApiError::database_unavailable)?;
                    if !bot_storage::delete(&mut writer, user_id, keys).await? {
                        return Err(ApiError::BotStorageKeyNotFound);
                    }
                    bot_storage::KEYS
                };
                let processed: &[_] = if form.has_query_client() {
                    &[parameter, "client"]
                } else {
                    &[parameter]
                };
                let extra = ignored_parameters(&form, processed);
                return Ok(format!("{{\"result\":\"success\",\"msg\":\"\"{extra}}}\n"));
            }
            if let WriteOperation::EditNavigationView(fragment)
            | WriteOperation::DeleteNavigationView(fragment) = write
            {
                let update = if matches!(write, WriteOperation::EditNavigationView(_)) {
                    let update = NavigationViewUpdate::from_form(&form).map_err(ApiError::Form)?;
                    update.validate_name(fragment).map_err(ApiError::InvalidNavigationView)?;
                    Some(update)
                } else {
                    None
                };
                let activity = state
                    .activity
                    .as_ref()
                    .expect("navigation view writes require RabbitMQ");
                let queue = activity
                    .notification_queue(realm_id, user_id)
                    .await
                    .ok_or(ApiError::NotificationUnavailable)?;
                let (event, extra) = {
                    let mut writer = state
                        .database
                        .writer()
                        .await
                        .map_err(ApiError::database_unavailable)?;
                    if let Some(update) = update {
                        match update.apply(&mut writer, user_id, realm_id, fragment).await? {
                            UpdateOutcome::Updated => {}
                            UpdateOutcome::DuplicateName => {
                                return Err(ApiError::InvalidNavigationView(DUPLICATE_NAVIGATION_VIEW));
                            }
                            UpdateOutcome::Missing => return Err(ApiError::NavigationViewNotFound),
                        }
                        let processed: &[_] = if form.has_query_client() {
                            &["is_pinned", "name", "client"]
                        } else {
                            &["is_pinned", "name"]
                        };
                        (update.event(fragment), ignored_parameters(&form, processed))
                    } else {
                        if !navigation_views::delete(&mut writer, user_id, realm_id, fragment).await? {
                            return Err(ApiError::NavigationViewNotFound);
                        }
                        (serde_json::json!({"type": "navigation_view", "op": "remove", "fragment": fragment}), String::new())
                    }
                };
                activity.notify_user(queue, user_id, event).await.map_err(|error| {
                    eprintln!("Could not confirm navigation view notification for user {user_id}: {error}");
                    ApiError::NotificationUnavailable
                })?;
                return Ok(format!("{{\"result\":\"success\",\"msg\":\"\"{extra}}}\n"));
            }
            if matches!(write, WriteOperation::CreateNavigationView) {
                let view = navigation_views::NavigationView::from_form(&form).map_err(ApiError::Form)?;
                view.validate_name().map_err(ApiError::InvalidNavigationView)?;
                let activity = state
                    .activity
                    .as_ref()
                    .expect("navigation view creation requires RabbitMQ");
                let queue = activity
                    .notification_queue(realm_id, user_id)
                    .await
                    .ok_or(ApiError::NotificationUnavailable)?;
                let created = {
                    let mut writer = state
                        .database
                        .writer()
                        .await
                        .map_err(ApiError::database_unavailable)?;
                    view.create(&mut writer, user_id, realm_id).await?
                };
                if !created {
                    return Err(ApiError::InvalidNavigationView(DUPLICATE_NAVIGATION_VIEW));
                }
                activity.notify_user(queue, user_id, view.add_event()).await.map_err(|error| {
                    eprintln!("Could not confirm navigation view notification for user {user_id}: {error}");
                    ApiError::NotificationUnavailable
                })?;
                let extra = ignored_parameters(&form, &["fragment", "is_pinned", "name", "client"]);
                return Ok(format!("{{\"result\":\"success\",\"msg\":\"\"{extra}}}\n"));
            }
            if matches!(write, WriteOperation::CreateDrafts) {
                require_draft_synchronization(&database, user_id).await?;
                let input = form.value("drafts").ok_or(ApiError::Form(FormError::Missing("drafts")))?;
                let input = draft_input::parse_http_draft_batch(input)
                    .map_err(|error| draft_schema_error("drafts", error))?;
                let config = state.draft_writes.as_ref().expect("draft POST configured");
                let activity = state.activity.as_ref().expect("draft creation requires RabbitMQ");
                let queue = activity.notification_queue(realm_id, user_id).await
                    .ok_or(ApiError::NotificationUnavailable)?;
                let (ids, fields) = {
                    let mut writer = state.database.writer().await.map_err(ApiError::database_unavailable)?;
                    draft_writes::create(&mut writer, user_id, realm_id, input, config.max_message_length,
                        &config.cross_realm_bot_emails).await?
                };
                activity.notify_user(
                    queue, user_id, serde_json::json!({"type": "drafts", "op": "add", "drafts": fields}),
                ).await.map_err(|error| {
                    eprintln!("Could not confirm draft creation notification for user {user_id}: {error}");
                    ApiError::NotificationUnavailable
                })?;
                let processed: &[_] = if form.has_query_client() { &["drafts", "client"] } else { &["drafts"] };
                let extra = ignored_parameters(&form, processed);
                let ids = serde_json::to_string(&ids).expect("IDs can serialize to JSON");
                return Ok(format!("{{\"result\":\"success\",\"msg\":\"\",\"ids\":{ids}{extra}}}\n"));
            }
            if let WriteOperation::EditDraft(id) = write {
                require_draft_synchronization(&database, user_id).await?;
                let input = form.value("draft").ok_or(ApiError::Form(FormError::Missing("draft")))?;
                let input = draft_input::parse_http_draft(input)
                    .map_err(|error| draft_schema_error("draft", error))?;
                let id = id.ok_or(ApiError::DraftNotFound)?;
                if !drafts::exists(&database, user_id, id).await? {
                    return Err(ApiError::DraftNotFound);
                }
                let config = state.draft_writes.as_ref().expect("draft PATCH configured");
                let activity = state.activity.as_ref().expect("draft editing requires RabbitMQ");
                let queue = activity.notification_queue(realm_id, user_id).await
                    .ok_or(ApiError::NotificationUnavailable)?;
                let event = {
                    let mut writer = state.database.writer().await.map_err(ApiError::database_unavailable)?;
                    draft_writes::edit(&mut writer, user_id, realm_id, id, input, config.max_message_length,
                        &config.cross_realm_bot_emails).await?
                };
                activity.notify_user(queue, user_id, event).await.map_err(|error| {
                    eprintln!("Could not confirm draft update notification for user {user_id}: {error}");
                    ApiError::NotificationUnavailable
                })?;
                let processed: &[_] = if form.has_query_client() { &["draft", "client"] } else { &["draft"] };
                let extra = ignored_parameters(&form, processed);
                return Ok(format!("{{\"result\":\"success\",\"msg\":\"\"{extra}}}\n"));
            }
            if let WriteOperation::DeleteDraft(id) = write {
                require_draft_synchronization(&database, user_id).await?;
                let id = id.ok_or(ApiError::DraftNotFound)?;
                let activity = state
                    .activity
                    .as_ref()
                    .expect("draft deletion requires RabbitMQ");
                let queue = activity
                    .notification_queue(realm_id, user_id)
                    .await
                    .ok_or(ApiError::NotificationUnavailable)?;
                if !drafts::delete(&database, user_id, id).await? {
                    return Err(ApiError::DraftNotFound);
                }
                activity
                    .notify_user(
                        queue,
                        user_id,
                        serde_json::json!({
                            "type": "drafts", "op": "remove", "draft_id": id,
                        }),
                    )
                    .await
                    .map_err(|error| {
                        eprintln!("Could not confirm notification for draft {id}: {error}");
                        ApiError::NotificationUnavailable
                    })?;
                return Ok("{\"result\":\"success\",\"msg\":\"\"}\n".to_owned());
            }
            if let WriteOperation::DeleteScheduledJob(kind, id) = write {
                let id = id.ok_or(ApiError::ScheduledJobNotFound(kind))?;
                let activity = state
                    .activity
                    .as_ref()
                    .expect("scheduled job deletion requires RabbitMQ");
                let queue = activity
                    .notification_queue(realm_id, user_id)
                    .await
                    .ok_or(ApiError::NotificationUnavailable)?;
                let deleted = {
                    let mut writer = state
                        .database
                        .writer()
                        .await
                        .map_err(ApiError::database_unavailable)?;
                    scheduled_messages::delete(&mut writer, user_id, id, kind).await?
                };
                if !deleted {
                    return Err(ApiError::ScheduledJobNotFound(kind));
                }
                activity
                    .notify_user(queue, user_id, kind.removed_event(id))
                    .await
                    .map_err(|error| {
                        eprintln!("Could not confirm notification for scheduled job {id}: {error}");
                        ApiError::NotificationUnavailable
                    })?;
                let processed: &[_] = if form.has_query_client() { &["client"] } else { &[] };
                let extra = ignored_parameters(&form, processed);
                return Ok(format!("{{\"result\":\"success\",\"msg\":\"\"{extra}}}\n"));
            }
            if matches!(
                write,
                WriteOperation::AddAlertWords | WriteOperation::RemoveAlertWords
            ) {
                let adding = matches!(write, WriteOperation::AddAlertWords);
                state
                    .change_alert_words(&form, user_id, realm_id, adding)
                    .await?;
                // Django reads the current list again after the event has been published.
                let fields = Endpoint::AlertWords
                    .read(&database, user_id, realm_id, uri.query(), &state)
                    .await?;
                let processed: &[_] = if requested_write.is_some_and(WriteOperation::is_post)
                    || form.has_query_client()
                {
                    &["alert_words", "client"]
                } else {
                    &["alert_words"]
                };
                let extra = ignored_parameters(&form, processed);
                return Ok(format!(
                    "{{\"result\":\"success\",\"msg\":\"\",{fields}{extra}}}\n"
                ));
            }

            let max = state
                .saved_snippet_max_content_length
                .expect("write route configured");
            let activity = state
                .activity
                .as_ref()
                .expect("saved snippet writes require RabbitMQ");
            let queue = activity.notification_queue(realm_id, user_id).await;
            let mut result_fields = String::new();
            let change = match write {
                WriteOperation::CreateSnippet => {
                    let (title, content) = form.create_values(max).map_err(ApiError::Form)?;
                    if queue.is_none() {
                        return Err(ApiError::NotificationUnavailable);
                    }
                    let snippet =
                        saved_snippets::create(&database, user_id, realm_id, title, content)
                            .await?;
                    result_fields = format!(",\"saved_snippet_id\":{}", snippet.id);
                    Some(SnippetEvent::Added(snippet))
                }
                WriteOperation::EditSnippet(id) => {
                    let (title, content) = form.edit_values(max).map_err(ApiError::Form)?;
                    if title.is_none() && content.is_none() {
                        None
                    } else {
                        let id = id.ok_or(ApiError::SnippetNotFound)?;
                        if queue.is_none() {
                            return Err(ApiError::NotificationUnavailable);
                        }
                        let snippet = saved_snippets::edit(&database, user_id, id, title, content)
                            .await?
                            .ok_or(ApiError::SnippetNotFound)?;
                        Some(SnippetEvent::Updated(snippet))
                    }
                }
                WriteOperation::DeleteSnippet(id) => {
                    let id = id.ok_or(ApiError::SnippetNotFound)?;
                    if queue.is_none() {
                        return Err(ApiError::NotificationUnavailable);
                    }
                    if !saved_snippets::delete(&database, user_id, id).await? {
                        return Err(ApiError::SnippetNotFound);
                    }
                    Some(SnippetEvent::Removed(id))
                }
                WriteOperation::AddAlertWords
                | WriteOperation::RemoveAlertWords
                | WriteOperation::CreateNavigationView
                | WriteOperation::EditNavigationView(_)
                | WriteOperation::DeleteNavigationView(_)
                | WriteOperation::CreateDrafts
                | WriteOperation::EditDraft(_)
                | WriteOperation::DeleteDraft(_)
                | WriteOperation::DeleteScheduledJob(..)
                | WriteOperation::DeleteBotStorage
                | WriteOperation::UpdateBotStorage => {
                    unreachable!("handled above")
                }
            };
            if let Some(event) = change {
                activity
                    .notify_user(
                        queue.expect("mutation requires a notification queue"),
                        user_id,
                        event.payload(),
                    )
                    .await
                    .map_err(|error| {
                        eprintln!(
                            "Could not confirm notification for saved snippet {}: {error}",
                            event.snippet_id()
                        );
                        ApiError::NotificationUnavailable
                    })?;
            }
            if matches!(write, WriteOperation::DeleteSnippet(_)) {
                // Django's delete view returns a plain success response.
                return Ok("{\"result\":\"success\",\"msg\":\"\"}\n".to_owned());
            }
            // Django identifies the client before it parses the PATCH body.
            let processed: &[_] =
                if !matches!(write, WriteOperation::EditSnippet(_)) || form.has_query_client() {
                    &["title", "content", "client"]
                } else {
                    &["title", "content"]
                };
            let extra = ignored_parameters(&form, processed);
            return Ok(format!(
                "{{\"result\":\"success\",\"msg\":\"\"{result_fields}{extra}}}\n"
            ));
        }
        let fields = endpoint
            .read(&database, user_id, realm_id, uri.query(), &state)
            .await?;
        Ok(format!(
            "{{\"result\":\"success\",\"msg\":\"\",{fields}}}\n"
        ))
    }
    .await;
    let mut response = match data {
        Ok(body) => (
            StatusCode::OK,
            [
                (header::CONTENT_TYPE, "application/json"),
                (header::CACHE_CONTROL, NEVER_CACHE),
                (header::VARY, "Accept-Language"),
            ],
            body,
        )
            .into_response(),
        Err(error) => {
            // Django evaluates typed-parameter templates at import under its
            // computed English default, not the later client's language.
            let error_language = if matches!(endpoint, Endpoint::ChannelList)
                && matches!(
                    error,
                    ApiError::Form(FormError::InvalidJson(_) | FormError::ExpectedBoolean(_))
                ) {
                "en"
            } else {
                language
            };
            error_response(
                error,
                &state.locales,
                error_language,
                &headers,
                uri.path(),
                browser,
            )
        }
    };
    let response_headers = response.headers_mut();
    response_headers.insert(header::CONTENT_LANGUAGE, HeaderValue::from_static(language));
    if response_headers.contains_key(header::CACHE_CONTROL) {
        let expires = httpdate::fmt_http_date(SystemTime::now());
        response_headers.insert(
            header::EXPIRES,
            HeaderValue::try_from(expires).expect("HTTP date"),
        );
    }
    if let Some(limit) = rate_limit {
        limit.set_headers(response_headers);
    }
    if let Some(sessions) = browser_session {
        response_headers.insert(
            header::VARY,
            HeaderValue::from_static("Accept-Language, Cookie"),
        );
        if let Some(csrf) = sessions.refresh_csrf_cookie(&headers) {
            response_headers.append(header::SET_COOKIE, csrf);
        }
        if clear_session_cookie {
            response_headers.append(header::SET_COOKIE, sessions.delete_cookie_header());
        }
    }
    response
}

fn ignored_parameters(form: &Form, processed: &[&str]) -> String {
    let ignored = form.ignored_parameters(processed);
    if ignored.is_empty() {
        return String::new();
    }
    let names = serde_json::to_string(&ignored).expect("parameter names serialize to JSON");
    format!(",\"ignored_parameters_unsupported\":{names}")
}

fn error_response(
    error: ApiError,
    locales: &Locales,
    language: &str,
    headers: &HeaderMap,
    path: &str,
    browser: bool,
) -> Response {
    if let ApiError::UnsupportedMethodOverride(endpoint) = error {
        let (allow, body) = match endpoint {
            Endpoint::AlertWords => (
                "GET, POST, DELETE, HEAD",
                "{\"result\":\"error\",\"msg\":\"Method Not Allowed\",\"allowed_methods\":[\"GET\",\"POST\",\"DELETE\",\"HEAD\"]}",
            ),
            Endpoint::Drafts
            | Endpoint::Attachments
            | Endpoint::BotStorage
            | Endpoint::Linkifiers
            | Endpoint::RealmDomains
            | Endpoint::RealmEmoji
            | Endpoint::CustomProfileFields
            | Endpoint::ChannelFolders
            | Endpoint::ChannelId
            | Endpoint::ChannelTopics(_)
            | Endpoint::UserStatus(_)
            | Endpoint::SubscriptionStatus(..)
            | Endpoint::SubscribedChannels(_)
            | Endpoint::ChannelMembers(_)
            | Endpoint::ChannelDetails(_)
            | Endpoint::ChannelList
            | Endpoint::ReadReceipts(_)
            | Endpoint::ExportConsents
            | Endpoint::UserGroups
            | Endpoint::GroupDetail(..)
            | Endpoint::GroupMembership(..)
            | Endpoint::ScheduledMessages
            | Endpoint::Reminders => {
                unreachable!("endpoints without a Rust POST do not override methods")
            }
            Endpoint::SavedSnippets | Endpoint::NavigationViews => (
                "GET, POST, HEAD",
                "{\"result\":\"error\",\"msg\":\"Method Not Allowed\",\"allowed_methods\":[\"GET\",\"POST\",\"HEAD\"]}",
            ),
        };
        return (
            StatusCode::METHOD_NOT_ALLOWED,
            [
                (header::ALLOW, allow),
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                (header::VARY, "Accept-Language, Cookie"),
                (header::CACHE_CONTROL, NEVER_CACHE),
            ],
            body,
        )
            .into_response();
    }
    if matches!(error, ApiError::MissingAuthentication) && prefers_html(headers) {
        let query = form_urlencoded::Serializer::new(String::new())
            .append_pair("next", path)
            .finish();
        let location = format!("/login/?{query}");
        return (
            StatusCode::FOUND,
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                (header::LOCATION, location.as_str()),
                (header::VARY, "Accept-Language, Cookie"),
            ],
            "",
        )
            .into_response();
    }
    let (status, code, message) = match error {
        ApiError::Csrf(_) => (StatusCode::FORBIDDEN, "CSRF_FAILED", "CSRF error: {reason}"),
        ApiError::MissingAuthentication => (
            StatusCode::UNAUTHORIZED,
            "UNAUTHORIZED",
            "Not logged in: API authentication or user session required",
        ),
        ApiError::Unauthorized(message) => (StatusCode::UNAUTHORIZED, "UNAUTHORIZED", message),
        ApiError::BadRequest(message) | ApiError::InvalidNavigationView(message) => {
            (StatusCode::BAD_REQUEST, "BAD_REQUEST", message)
        }
        ApiError::InvalidMessage => (StatusCode::BAD_REQUEST, "BAD_REQUEST", "Invalid message(s)"),
        ApiError::ReadReceiptsDisabled => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Read receipts are disabled in this organization.",
        ),
        ApiError::ChannelListMissingBotOwner => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "",
            "Internal server error",
        ),
        ApiError::InvalidChannelId => {
            (StatusCode::BAD_REQUEST, "BAD_REQUEST", "Invalid channel ID")
        }
        ApiError::PublicSubscribersUnavailable => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Subscriber data is not available for this channel",
        ),
        ApiError::PrivateSubscribersUnavailable => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Unable to retrieve subscribers for private channel",
        ),
        ApiError::InvalidChannelName(_) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Invalid channel name '{channel_name}'",
        ),
        ApiError::AdministratorRequired => (
            StatusCode::BAD_REQUEST,
            "UNAUTHORIZED_PRINCIPAL",
            "Must be an organization administrator",
        ),
        ApiError::UserGroupGuest => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Not allowed for guest users",
        ),
        ApiError::InvalidUserGroup => {
            (StatusCode::BAD_REQUEST, "BAD_REQUEST", "Invalid user group")
        }
        ApiError::DeactivatedUserGroup => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "User group is deactivated.",
        ),
        ApiError::MissingTargetUser => (StatusCode::BAD_REQUEST, "BAD_REQUEST", "No such user"),
        ApiError::DeactivatedTargetUser => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "User is deactivated",
        ),
        ApiError::RestrictedTargetUser => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Insufficient permission",
        ),
        ApiError::DeactivatedUser => (
            StatusCode::UNAUTHORIZED,
            "USER_DEACTIVATED",
            "Account is deactivated",
        ),
        ApiError::DeactivatedRealm => (
            StatusCode::UNAUTHORIZED,
            "REALM_DEACTIVATED",
            "This organization has been deactivated",
        ),
        ApiError::InvalidHost => (StatusCode::BAD_REQUEST, "BAD_REQUEST", "Invalid host"),
        ApiError::DatabaseUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Database unavailable",
        ),
        ApiError::RateLimiterUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Rate limiter unavailable",
        ),
        ApiError::ActivityUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Activity service unavailable",
        ),
        ApiError::NotificationUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Notification service unavailable",
        ),
        ApiError::AlertWordCacheUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Alert-word cache unavailable",
        ),
        ApiError::Form(FormError::InvalidJson(_)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} is not valid JSON",
        ),
        ApiError::Form(FormError::ExpectedList(_)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} is not a list",
        ),
        ApiError::Form(FormError::ExpectedBoolean(_)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} is not a boolean",
        ),
        ApiError::Form(FormError::ExpectedDict(_)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} is not a dict",
        ),
        ApiError::Form(FormError::ExpectedString(..) | FormError::ExpectedDictString(..)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} is not a string",
        ),
        ApiError::Form(FormError::Missing(_)) => (
            StatusCode::BAD_REQUEST,
            "REQUEST_VARIABLE_MISSING",
            "Missing '{var_name}' argument",
        ),
        ApiError::Form(FormError::Blank(_)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} cannot be blank",
        ),
        ApiError::Form(FormError::TooLong(..) | FormError::ItemTooLong(..)) => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "{var_name} is too long (limit: {max_length} characters)",
        ),
        ApiError::Form(FormError::Malformed) => {
            (StatusCode::BAD_REQUEST, "BAD_REQUEST", "Malformed payload")
        }
        ApiError::UnsupportedMethodOverride(_) => unreachable!("override handled above"),
        ApiError::DraftSyncDisabled => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "User has disabled synchronizing drafts.",
        ),
        ApiError::BotRequired => (
            StatusCode::BAD_REQUEST,
            "PERMISSION_DENIED",
            "Must be a bot user",
        ),
        ApiError::BotStorageKeyNotFound => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Key does not exist.",
        ),
        ApiError::BotStorageLimitExceeded { .. } => (
            StatusCode::BAD_REQUEST,
            "BAD_REQUEST",
            "Request exceeds storage limit by {excess} characters. The limit is {limit} characters.",
        ),
        ApiError::DraftNotFound => (StatusCode::NOT_FOUND, "BAD_REQUEST", "Draft does not exist"),
        ApiError::DraftSchema(ref error) => {
            (StatusCode::BAD_REQUEST, "BAD_REQUEST", error.template())
        }
        ApiError::DraftWrite(ref error) => error.response(),
        ApiError::ScheduledJobNotFound(JobKind::Reminder) => (
            StatusCode::NOT_FOUND,
            "BAD_REQUEST",
            "Reminder does not exist",
        ),
        ApiError::ScheduledJobNotFound(JobKind::Message) => (
            StatusCode::NOT_FOUND,
            "BAD_REQUEST",
            "Scheduled message does not exist",
        ),
        ApiError::NavigationViewNotFound => (
            StatusCode::NOT_FOUND,
            "BAD_REQUEST",
            "Navigation view does not exist.",
        ),
        ApiError::SnippetNotFound => (
            StatusCode::NOT_FOUND,
            "BAD_REQUEST",
            "Saved snippet does not exist.",
        ),
        ApiError::RateLimited(_) => (
            StatusCode::TOO_MANY_REQUESTS,
            "RATE_LIMIT_HIT",
            "API usage exceeded rate limit; see https://zulip.com/api/http-headers#rate-limiting-response-headers",
        ),
    };
    let translated = locales.gettext(language, message);
    let message = match &error {
        ApiError::Csrf(reason) => translated.replace("{reason}", reason),
        ApiError::InvalidChannelName(name) => translated.replace("{channel_name}", name),
        ApiError::DraftSchema(error) => error.format(translated),
        ApiError::DraftWrite(error) => error.format(translated),
        ApiError::Form(
            FormError::Missing(name)
            | FormError::Blank(name)
            | FormError::InvalidJson(name)
            | FormError::ExpectedBoolean(name)
            | FormError::ExpectedList(name)
            | FormError::ExpectedDict(name),
        ) => translated.replace("{var_name}", name),
        ApiError::Form(FormError::ExpectedString(name, index)) => {
            translated.replace("{var_name}", &format!("{name}[{index}]"))
        }
        ApiError::Form(FormError::ExpectedDictString(name, key)) => {
            translated.replace("{var_name}", &format!("{name}[{key}]"))
        }
        ApiError::BotStorageLimitExceeded { excess, limit } => message
            .replace("{excess}", &excess.to_string())
            .replace("{limit}", &limit.to_string()),
        ApiError::Form(FormError::ItemTooLong(name, index, length)) => translated
            .replace("{var_name}", &format!("{name}[{index}]"))
            .replace("{max_length}", &length.to_string()),
        ApiError::Form(FormError::TooLong(name, length)) => translated
            .replace("{var_name}", name)
            .replace("{max_length}", &length.to_string()),
        _ => translated.to_owned(),
    };
    let message = serde_json::to_string(&message).expect("strings can serialize to JSON");
    let details = match &error {
        ApiError::Form(FormError::Missing(name)) => {
            format!(",\"var_name\":\"{name}\"")
        }
        ApiError::Csrf(reason) => {
            let reason = serde_json::to_string(reason).expect("strings can serialize to JSON");
            format!(",\"reason\":{reason}")
        }
        _ => String::new(),
    };
    let (extra, vary) = match error {
        ApiError::RateLimited(seconds) => {
            (format!(",\"retry-after\":{seconds:?}"), "Accept-Language")
        }
        ApiError::ChannelListMissingBotOwner if browser => {
            (String::new(), "Accept-Language, Cookie")
        }
        ApiError::Form(_)
        | ApiError::SnippetNotFound
        | ApiError::InvalidNavigationView(_)
        | ApiError::InvalidChannelName(_)
        | ApiError::InvalidChannelId
        | ApiError::ChannelListMissingBotOwner
        | ApiError::InvalidMessage
        | ApiError::ReadReceiptsDisabled
        | ApiError::AdministratorRequired
        | ApiError::PublicSubscribersUnavailable
        | ApiError::PrivateSubscribersUnavailable
        | ApiError::NavigationViewNotFound
        | ApiError::DraftSyncDisabled
        | ApiError::UserGroupGuest
        | ApiError::InvalidUserGroup
        | ApiError::DeactivatedUserGroup
        | ApiError::MissingTargetUser
        | ApiError::DeactivatedTargetUser
        | ApiError::RestrictedTargetUser
        | ApiError::BotRequired
        | ApiError::BotStorageKeyNotFound
        | ApiError::BotStorageLimitExceeded { .. }
        | ApiError::DraftNotFound
        | ApiError::DraftSchema(_)
        | ApiError::DraftWrite(_)
        | ApiError::ScheduledJobNotFound(_) => (String::new(), "Accept-Language"),
        _ => (String::new(), "Accept-Language, Cookie"),
    };
    let mut response = (
        status,
        [
            (header::CONTENT_TYPE, "application/json"),
            (header::VARY, vary),
        ],
        if matches!(error, ApiError::ChannelListMissingBotOwner) {
            format!("{{\"result\":\"error\",\"msg\":{message}}}\n")
        } else {
            format!(
                "{{\"result\":\"error\",\"msg\":{message}{details},\"code\":\"{code}\"{extra}}}\n"
            )
        },
    )
        .into_response();
    if matches!(error, ApiError::Csrf(_)) {
        response
            .headers_mut()
            .insert(header::CACHE_CONTROL, HeaderValue::from_static(NEVER_CACHE));
    }
    if matches!(
        error,
        ApiError::MissingAuthentication | ApiError::Unauthorized(_)
    ) {
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            HeaderValue::from_static(if browser {
                "Session realm=\"zulip\""
            } else {
                "Basic realm=\"zulip\""
            }),
        );
    }
    if let ApiError::RateLimited(seconds) = error {
        let retry = format!("{seconds:?}");
        let retry = if let Some((mantissa, exponent)) = retry.split_once('e') {
            format!(
                "{mantissa}e{:+03}",
                exponent.parse::<i32>().expect("float exponent")
            )
        } else {
            retry
        };
        response.headers_mut().insert(
            header::RETRY_AFTER,
            HeaderValue::try_from(retry).expect("numeric retry delay"),
        );
    }
    response
}

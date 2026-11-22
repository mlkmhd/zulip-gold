use std::{collections::HashSet, error::Error, path::Path};

use axum::{
    Extension, Router,
    extract::DefaultBodyLimit,
    routing::{delete, get, patch},
};
pub use csrf::WebPostScheme;
pub use rate_limit::ClientIpSource;

mod activity;
mod alert_word_cache;
pub mod alert_words;
mod attachments;
mod bot_storage;
mod channel_folders;
mod channel_id;
mod channel_topics;
mod compatibility;
mod csrf;
mod custom_profile_fields;
mod database;
mod draft_writes;
mod drafts;
mod form;
#[doc(hidden)]
pub mod group_detail_request;
#[doc(hidden)]
pub mod group_listing_request;
mod linkifiers;
mod locale;
mod navigation_views;
#[doc(hidden)]
pub mod own_profile;
#[doc(hidden)]
pub mod own_profile_presentation;
mod rate_limit;
mod realm_domains;
mod realm_emoji;
mod saved_snippets;
mod scheduled_messages;
mod session;
mod subscription_status;
mod tornado_routing;
mod unicode_lowercase;
mod user_agent;
mod user_reads;
mod user_status_read;
mod visible_target;

pub fn router(locale_dir: &Path) -> Result<Router, Box<dyn Error>> {
    Ok(compatibility_router(locale::Locales::load(locale_dir)?))
}

pub struct UserReadConfig<'a> {
    pub database: &'a str,
    pub external_host: &'a str,
    pub emoji_url_base: Option<&'a str>,
    pub user_group_read_pilot: bool,
    pub user_group_detail_read_pilot: bool,
    pub user_group_membership_read_pilot: bool,
    pub channel_id_read_pilot: bool,
    pub user_status_read_pilot: bool,
    pub subscription_status_read_pilot: bool,
    pub channel_topics_lowercase_file: Option<&'a Path>,
    pub rate_limit: Option<RateLimitConfig<'a>>,
    pub activity: Option<ActivityConfig<'a>>,
    pub alert_word_cache_manifest: Option<&'a Path>,
    pub alert_word_lowercase_file: Option<&'a Path>,
    pub alert_word_posts: bool,
    pub draft_deletes: bool,
    pub draft_writes: Option<DraftWriteConfig>,
    pub reminder_deletes: bool,
    pub scheduled_message_deletes: bool,
    pub bot_storage_deletes: bool,
    pub bot_storage_size_limit: Option<u64>,
    pub navigation_view_posts: bool,
    pub navigation_view_details: bool,
    pub session: Option<SessionConfig<'a>>,
    pub web_post_scheme: Option<WebPostScheme>,
    pub saved_snippet_max_content_length: Option<usize>,
}

pub struct DraftWriteConfig {
    pub posts: bool,
    pub patches: bool,
    pub max_message_length: usize,
    pub cross_realm_bot_emails: HashSet<String>,
}

pub struct SessionConfig<'a> {
    pub secret_file: &'a Path,
    pub backends: &'a str,
    pub cookie_name: &'a str,
    pub csrf_cookie_name: &'a str,
}

pub struct RateLimitConfig<'a> {
    pub url: &'a str,
    pub key_prefix: &'a str,
    pub rules: &'a str,
    pub client_ip_source: ClientIpSource,
    pub debug_rate_limiting: bool,
}

pub struct ActivityConfig<'a> {
    pub url: &'a str,
    pub shards: u32,
    pub tornado_routing_file: Option<&'a Path>,
}

pub async fn router_with_user_reads(
    locale_dir: &Path,
    config: UserReadConfig<'_>,
) -> Result<Router, Box<dyn Error>> {
    let locales = locale::Locales::load(locale_dir)?;
    let browser_enabled = config.session.is_some();
    let user_group_read_pilot = config.user_group_read_pilot;
    if user_group_read_pilot && !browser_enabled {
        return Err("User-group read pilot requires signed Django sessions".into());
    }
    let user_group_detail_read_pilot = config.user_group_detail_read_pilot;
    if user_group_detail_read_pilot && !browser_enabled {
        return Err("User-group detail read pilot requires signed Django sessions".into());
    }
    let user_group_membership_read_pilot = config.user_group_membership_read_pilot;
    if user_group_membership_read_pilot && !browser_enabled {
        return Err("User-group membership read pilot requires signed Django sessions".into());
    }
    let channel_id_read_pilot = config.channel_id_read_pilot;
    if channel_id_read_pilot && !browser_enabled {
        return Err("Channel ID read pilot requires signed Django sessions".into());
    }
    let user_status_read_pilot = config.user_status_read_pilot;
    if user_status_read_pilot && !browser_enabled {
        return Err("User status read pilot requires signed Django sessions".into());
    }
    let subscription_status_read_pilot = config.subscription_status_read_pilot;
    if subscription_status_read_pilot && !browser_enabled {
        return Err("Subscription status read pilot requires signed Django sessions".into());
    }
    let channel_topics = config.channel_topics_lowercase_file.is_some();
    let emoji_reads = config.emoji_url_base.is_some();
    let saved_snippet_writes = config.saved_snippet_max_content_length.is_some();
    let alert_word_writes = config.alert_word_lowercase_file.is_some();
    let alert_word_posts = config.alert_word_posts;
    let draft_deletes = config.draft_deletes;
    let draft_posts = config
        .draft_writes
        .as_ref()
        .is_some_and(|writes| writes.posts);
    let draft_patches = config
        .draft_writes
        .as_ref()
        .is_some_and(|writes| writes.patches);
    let reminder_deletes = config.reminder_deletes;
    let scheduled_message_deletes = config.scheduled_message_deletes;
    let bot_storage_deletes = config.bot_storage_deletes;
    let bot_storage_updates = config.bot_storage_size_limit.is_some();
    let navigation_view_posts = config.navigation_view_posts;
    let navigation_view_details = config.navigation_view_details;
    if alert_word_posts && !alert_word_writes {
        return Err("Alert-word POST requires ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE".into());
    }
    let web_writes_enabled = config.web_post_scheme.is_some();
    if web_writes_enabled
        && !(browser_enabled
            && (saved_snippet_writes
                || alert_word_writes
                || draft_deletes
                || draft_posts
                || draft_patches
                || reminder_deletes
                || scheduled_message_deletes
                || bot_storage_deletes
                || bot_storage_updates
                || navigation_view_posts
                || navigation_view_details))
    {
        return Err("Web writes require Django sessions and an enabled write route".into());
    }
    let user_reads = user_reads::UserReads::connect(config, locales.clone()).await?;
    let alert_word_route = get(user_reads::get);
    let mut alert_word_write_route = get(user_reads::get).delete(user_reads::remove_alert_words);
    if alert_word_posts {
        alert_word_write_route = alert_word_write_route.post(user_reads::add_alert_words);
    }
    let alert_word_write_route =
        alert_word_write_route.layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let mut snippet_route = get(user_reads::get);
    if saved_snippet_writes {
        snippet_route = snippet_route
            .post(user_reads::create_saved_snippet)
            .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    }
    let navigation_view_post_route = get(user_reads::get)
        .post(user_reads::create_navigation_view)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let mut bot_storage_route = get(user_reads::get);
    if bot_storage_deletes {
        bot_storage_route = bot_storage_route.delete(user_reads::delete_bot_storage);
    }
    if bot_storage_updates {
        bot_storage_route = bot_storage_route.put(user_reads::update_bot_storage);
    }
    let bot_storage_route = bot_storage_route.layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let draft_read_route = get(user_reads::get);
    let draft_post_route = draft_read_route
        .clone()
        .post(user_reads::create_drafts)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let mut routes = Router::new()
        .route(
            user_reads::ALERT_WORDS_PATH,
            if alert_word_writes {
                alert_word_write_route.clone()
            } else {
                alert_word_route.clone()
            },
        )
        .route(saved_snippets::PATH, snippet_route)
        .route(
            drafts::PATH,
            if draft_posts {
                draft_post_route.clone()
            } else {
                draft_read_route.clone()
            },
        )
        .route(attachments::PATH, get(user_reads::get))
        .route(linkifiers::PATH, get(user_reads::get))
        .route(realm_domains::PATH, get(user_reads::get))
        .route(custom_profile_fields::PATH, get(user_reads::get))
        .route(
            navigation_views::PATH,
            if navigation_view_posts {
                navigation_view_post_route.clone()
            } else {
                get(user_reads::get)
            },
        )
        .route(channel_folders::PATH, get(user_reads::get))
        .route(bot_storage::PATH, bot_storage_route.clone())
        .route(scheduled_messages::PATH, get(user_reads::get))
        .route(scheduled_messages::REMINDERS_PATH, get(user_reads::get))
        .route("/health/rust-alert-words", get(user_reads::ready));
    if emoji_reads {
        routes = routes.route(realm_emoji::PATH, get(user_reads::get));
    }
    if user_group_read_pilot {
        routes = routes
            .route(group_listing_request::PATH, get(user_reads::get))
            .route(group_listing_request::SESSION_PATH, get(user_reads::get));
    }
    if user_group_detail_read_pilot {
        use group_detail_request::DetailKind::{Members, Subgroups};
        let members = get(user_reads::get_group_detail).layer(Extension(Members));
        let subgroups = get(user_reads::get_group_detail).layer(Extension(Subgroups));
        routes = routes
            .route("/api/v1/user_groups/{id}/members", members.clone())
            .route("/json/user_groups/{id}/members", members)
            .route("/api/v1/user_groups/{id}/subgroups", subgroups.clone())
            .route("/json/user_groups/{id}/subgroups", subgroups);
    }
    if user_group_membership_read_pilot {
        let membership = get(user_reads::get_group_membership);
        routes = routes
            .route(
                "/api/v1/user_groups/{id}/members/{user_id}",
                membership.clone(),
            )
            .route("/json/user_groups/{id}/members/{user_id}", membership);
    }
    if channel_id_read_pilot {
        routes = routes
            .route(channel_id::PATH, get(user_reads::get))
            .route(channel_id::SESSION_PATH, get(user_reads::get));
    }
    if user_status_read_pilot {
        let status = get(user_reads::get_user_status);
        routes = routes
            .route(user_status_read::PATH, status.clone())
            .route(user_status_read::SESSION_PATH, status);
    }
    if subscription_status_read_pilot {
        let read = get(user_reads::get_subscription_status);
        routes = routes
            .route(subscription_status::PATH, read.clone())
            .route(subscription_status::SESSION_PATH, read);
    }
    if channel_topics {
        routes = routes.route(channel_topics::PATH, get(user_reads::get_channel_topics));
    }
    let snippet_detail_route = patch(user_reads::write_saved_snippet_detail)
        .delete(user_reads::write_saved_snippet_detail)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    if saved_snippet_writes {
        routes = routes.route(saved_snippets::DETAIL_PATH, snippet_detail_route.clone());
    }
    let mut draft_detail_route = axum::routing::MethodRouter::new();
    if draft_deletes {
        draft_detail_route = draft_detail_route.delete(user_reads::delete_draft);
    }
    if draft_patches {
        draft_detail_route = draft_detail_route.patch(user_reads::edit_draft);
    }
    let draft_detail_route = draft_detail_route.layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    if draft_deletes || draft_patches {
        routes = routes.route(drafts::DETAIL_PATH, draft_detail_route.clone());
    }
    let scheduled_job_delete: axum::routing::MethodRouter<_> =
        delete(user_reads::delete_scheduled_job)
            .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let reminder_detail_route = scheduled_job_delete
        .clone()
        .layer(axum::Extension(scheduled_messages::JobKind::Reminder));
    let scheduled_message_detail_route =
        scheduled_job_delete.layer(axum::Extension(scheduled_messages::JobKind::Message));
    if reminder_deletes {
        routes = routes.route(
            scheduled_messages::REMINDER_DETAIL_PATH,
            reminder_detail_route.clone(),
        );
    }
    if scheduled_message_deletes {
        routes = routes.route(
            scheduled_messages::DETAIL_PATH,
            scheduled_message_detail_route.clone(),
        );
    }
    let navigation_view_detail_route = patch(user_reads::write_navigation_view_detail)
        .delete(user_reads::write_navigation_view_detail)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    if navigation_view_details {
        routes = routes.route(
            navigation_views::DETAIL_PATH,
            navigation_view_detail_route.clone(),
        );
    }
    if browser_enabled {
        routes = routes.route(
            bot_storage::SESSION_PATH,
            if web_writes_enabled {
                bot_storage_route
            } else {
                get(user_reads::get)
            },
        );
        if emoji_reads {
            routes = routes.route(realm_emoji::SESSION_PATH, get(user_reads::get));
        }
        let mut web_snippet_route = get(user_reads::get);
        if web_writes_enabled && saved_snippet_writes {
            web_snippet_route = web_snippet_route
                .post(user_reads::create_saved_snippet)
                .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
            routes = routes.route(saved_snippets::SESSION_DETAIL_PATH, snippet_detail_route);
        }
        if web_writes_enabled && (draft_deletes || draft_patches) {
            routes = routes.route(drafts::SESSION_DETAIL_PATH, draft_detail_route);
        }
        if web_writes_enabled && reminder_deletes {
            routes = routes.route(
                scheduled_messages::SESSION_REMINDER_DETAIL_PATH,
                reminder_detail_route,
            );
        }
        if web_writes_enabled && scheduled_message_deletes {
            routes = routes.route(
                scheduled_messages::SESSION_DETAIL_PATH,
                scheduled_message_detail_route,
            );
        }
        if web_writes_enabled && navigation_view_details {
            routes = routes.route(
                navigation_views::SESSION_DETAIL_PATH,
                navigation_view_detail_route,
            );
        }
        routes = routes
            .route(
                user_reads::ALERT_WORDS_SESSION_PATH,
                if web_writes_enabled && alert_word_writes {
                    alert_word_write_route
                } else {
                    alert_word_route
                },
            )
            .route(
                drafts::SESSION_PATH,
                if web_writes_enabled && draft_posts {
                    draft_post_route
                } else {
                    draft_read_route
                },
            )
            .route(attachments::SESSION_PATH, get(user_reads::get))
            .route(linkifiers::SESSION_PATH, get(user_reads::get))
            .route(realm_domains::SESSION_PATH, get(user_reads::get))
            .route(custom_profile_fields::SESSION_PATH, get(user_reads::get))
            .route(
                navigation_views::SESSION_PATH,
                if web_writes_enabled && navigation_view_posts {
                    navigation_view_post_route
                } else {
                    get(user_reads::get)
                },
            )
            .route(channel_folders::SESSION_PATH, get(user_reads::get))
            .route(scheduled_messages::SESSION_PATH, get(user_reads::get))
            .route(
                scheduled_messages::SESSION_REMINDERS_PATH,
                get(user_reads::get),
            )
            .route(saved_snippets::SESSION_PATH, web_snippet_route);
    }
    Ok(compatibility_router(locales).merge(routes.with_state(user_reads)))
}

fn compatibility_router(locales: locale::Locales) -> Router {
    Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(locales)
}

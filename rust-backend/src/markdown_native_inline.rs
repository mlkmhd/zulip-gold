//! Plan inline formatting in an indexed graph before committing changes to a host tree.
//!
//! This module has no Python dependency. The caller validates its handler registry,
//! represents shared nodes with shared indices, and supplies a guard for handlers
//! that still require the host renderer. A rejected guard discards the whole plan.

use std::collections::{HashMap, HashSet, VecDeque};

use crate::draft_text::is_python_whitespace;
use crate::markdown_autolinks;
use crate::markdown_channel_spans;
use crate::markdown_channel_syntax::{self, Kind as ChannelKind};
use crate::markdown_code::find_inline_code;
use crate::markdown_emphasis::find_emphasis;
use crate::markdown_inline_mentions::{
    self, MentionNotification, MentionUser, UserPolicy, UserReference,
};
use crate::markdown_inline_stash::Scanner;
use crate::markdown_link_syntax::TextStash;
use crate::markdown_links;
use crate::markdown_media::{self, AudioPolicy, ImageMetadata};
use crate::markdown_mention_syntax::{self, Kind as MentionKind};
use crate::markdown_timestamp::{self, Rendered as Timestamp};
use crate::markdown_unicode_emoji::{self, UnicodeEmoji};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Text {
    pub value: String,
    pub atomic: bool,
}

impl Text {
    fn plain(value: impl Into<String>) -> Self {
        Self {
            value: value.into(),
            atomic: false,
        }
    }
}

#[derive(Debug)]
pub struct Node {
    pub tag: String,
    /// The host renderer's lowercase form of the tag.
    pub lower_tag: String,
    pub text: Option<Text>,
    pub tail: Option<Text>,
    pub children: Vec<usize>,
    pub attributes: Vec<(String, String)>,
}

impl Node {
    fn new(tag: &str) -> Self {
        Self {
            tag: tag.to_owned(),
            lower_tag: tag.to_owned(),
            text: None,
            tail: None,
            children: Vec::new(),
            attributes: Vec::new(),
        }
    }
}

#[derive(Debug)]
pub enum Stashed {
    Node(usize),
    Text(Text),
}

pub struct Pattern {
    /// 0: guarded host handler; 1: code; 2: strong/em; 3: strong; 4: em;
    /// 5: strikethrough; 6: literal emphasis markers; 7: line break; 8: named emoji;
    /// 9: emoticon translation; 10: Unicode emoji; 11: explicit link;
    /// 12: audio; 13: image; 14: channel; 15: topic; 16: message link;
    /// 17: user mention; 18: group mention; 19: ISO timestamp; 20: HTML entity;
    /// 21: automatic link.
    pub kind: u8,
    pub excludes: Vec<String>,
    pub emoji: Option<NamedEmoji>,
    pub emoticons: Option<Emoticons>,
    pub unicode_emoji: Option<UnicodeEmoji>,
    pub links: Option<markdown_links::Policy>,
}

/// Ordered literal replacements and Unicode names from the current renderer.
pub struct Emoticons {
    pub conversions: Vec<(String, String)>,
    pub unicode: HashMap<String, String>,
}

/// The current renderer's emoji catalog, including active realm overrides.
pub struct NamedEmoji {
    pub unicode: HashMap<String, String>,
    pub realm: HashMap<String, String>,
}

pub struct State {
    pub nodes: Vec<Node>,
    pub parents: Vec<Option<usize>>,
    pub stash: Vec<Stashed>,
    pub parent_order: Vec<usize>,
    pub registry_used: bool,
    pub ancestors: Vec<String>,
    /// Pending effects, committed with the entire graph; duplicates are meaningful.
    pub attachment_paths: Vec<String>,
    /// Set the host's has_image when true; false never clears an existing value.
    pub has_image: bool,
    /// Union these notifications with existing host metadata after plan success.
    pub mentions: MentionEffects,
    /// Raw entities for the host HTML stash, in encounter order.
    pub html_blocks: Vec<String>,
}

/// Already-authorized media snapshots and live renderer settings.
pub struct Media {
    pub realm_host: Option<String>,
    pub audio_previews: Option<HashSet<String>>,
    pub image_previews: Option<HashMap<String, ImageMetadata>>,
    pub audio_policy: AudioPolicy,
}

/// Channel data already visible to the renderer, keyed by unmodified names.
pub struct Channel {
    pub id: i64,
    pub empty_topic_only: bool,
}

pub struct Channels {
    pub names: HashMap<String, Channel>,
    pub topic_with: HashMap<(String, String), i64>,
    pub empty_topic_name: String,
    pub unicode_decimal: HashSet<char>,
}

pub struct MentionUserSnapshot {
    pub id: i32,
    pub full_name: String,
    pub is_active: bool,
}

pub struct MentionGroupSnapshot {
    pub id: i32,
    pub display_name: String,
    pub deactivated: bool,
}

/// Keys are exact syntax names resolved by the host, including its lowercase
/// rules and duplicate-name ordering. Do not lowercase them again in Rust.
/// User values in either map must come from the same authorized lookup snapshot.
/// Name maps include cached misses as `None`. An absent key delegates the whole
/// plan, since earlier handlers can introduce syntax that was not snapshotted.
pub struct MentionData {
    pub users_by_name: HashMap<String, Option<MentionUserSnapshot>>,
    pub users_by_id: HashMap<i32, MentionUserSnapshot>,
    pub groups_by_name: HashMap<String, Option<MentionGroupSnapshot>>,
}

pub struct Mentions {
    /// None represents a renderer without database context, even for wildcards.
    pub data: Option<MentionData>,
    pub user_policy: UserPolicy,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct MentionEffects {
    pub users: HashSet<i32>,
    pub groups: HashSet<i32>,
    pub channel: bool,
    pub topic: bool,
}

/// Immutable authorized snapshots; none of the native handlers perform lookups.
#[derive(Default)]
pub struct Inputs<'a> {
    pub media: Option<&'a Media>,
    pub channels: Option<&'a Channels>,
    pub mentions: Option<&'a Mentions>,
    pub html_counter: Option<usize>,
    pub autolinks: Option<&'a markdown_autolinks::Matcher>,
}

/// Snapshot stash values for link unescaping without resolving nested placeholders.
///
/// Node values follow ElementTree's `itertext`: text, children in order, and
/// child tails, excluding the stashed root's tail. Shared children are visited
/// once per occurrence. Atomic strings have the same text as plain strings.
///
/// Invalid indices, cycles, more than 100,000 visits, or more than 8 MiB of
/// combined snapshot text return `None`; callers must delegate the whole plan.
/// This does not sanitize URLs and does not mutate the arena or stash.
pub fn snapshot_text_stash(nodes: &[Node], stash: &[Stashed]) -> Option<TextStash> {
    enum Visit {
        Enter(usize),
        Exit(usize),
        Tail(usize),
    }

    let mut result = TextStash::new();
    let mut active = vec![false; nodes.len()];
    let mut remaining_visits = 100_000_usize;
    let mut remaining_bytes = 8 * 1024 * 1024_usize;
    for (identifier, stashed) in stash.iter().enumerate() {
        remaining_visits = remaining_visits.checked_sub(1)?;
        let mut value = String::new();
        match stashed {
            Stashed::Text(text) => {
                remaining_bytes = remaining_bytes.checked_sub(text.value.len())?;
                value.push_str(&text.value);
            }
            Stashed::Node(root) => {
                let mut pending = vec![Visit::Enter(*root)];
                while let Some(visit) = pending.pop() {
                    remaining_visits = remaining_visits.checked_sub(1)?;
                    let text = match visit {
                        Visit::Enter(index) => {
                            let node = nodes.get(index)?;
                            if active[index] {
                                return None;
                            }
                            let available = remaining_visits
                                .checked_sub(pending.len())?
                                .checked_sub(1)?;
                            if node.children.len() > available / 2 {
                                return None;
                            }
                            active[index] = true;
                            pending.push(Visit::Exit(index));
                            for &child in node.children.iter().rev() {
                                pending.push(Visit::Tail(child));
                                pending.push(Visit::Enter(child));
                            }
                            node.text.as_ref()
                        }
                        Visit::Exit(index) => {
                            active[index] = false;
                            None
                        }
                        Visit::Tail(index) => nodes.get(index)?.tail.as_ref(),
                    };
                    if let Some(text) = text {
                        remaining_bytes = remaining_bytes.checked_sub(text.value.len())?;
                        value.push_str(&text.value);
                    }
                }
            }
        }
        result.insert(format!("{identifier:04}"), value);
    }
    Some(result)
}

struct Engine<'a, G> {
    state: State,
    patterns: &'a [Pattern],
    media: Option<&'a Media>,
    channels: Option<&'a Channels>,
    mentions: Option<&'a Mentions>,
    guard: G,
    remaining: usize,
    remaining_link_bytes: usize,
    remaining_media_bytes: usize,
    remaining_channel_bytes: usize,
    remaining_mention_bytes: usize,
    remaining_timestamp_bytes: usize,
    remaining_entity_bytes: usize,
    remaining_autolink_bytes: usize,
    html_counter: Option<usize>,
    autolinks: Option<&'a markdown_autolinks::Matcher>,
}

impl<G: FnMut(usize, &str) -> Option<()>> Engine<'_, G> {
    fn step(&mut self, depth: usize) -> Option<()> {
        if depth > 64 || self.remaining == 0 || self.state.nodes.len() > 20_000 {
            return None;
        }
        self.remaining -= 1;
        Some(())
    }

    fn set_parent(&mut self, child: usize, parent: usize) -> Option<()> {
        let previous = self.state.parents.get_mut(child)?;
        if previous.is_none() {
            self.state.parent_order.push(child);
        }
        *previous = Some(parent);
        Some(())
    }

    fn add_node(&mut self, tag: &str) -> usize {
        let index = self.state.nodes.len();
        self.state.nodes.push(Node::new(tag));
        self.state.parents.push(None);
        index
    }

    fn emoji_node(&mut self, codepoint: &str, syntax: &str) -> usize {
        let title = syntax[1..syntax.len() - 1].replace('_', " ");
        let node = self.add_node("span");
        self.state.nodes[node].attributes = vec![
            ("class".to_owned(), format!("emoji emoji-{codepoint}")),
            ("title".to_owned(), title.clone()),
            ("role".to_owned(), "img".to_owned()),
            ("aria-label".to_owned(), title),
        ];
        self.state.nodes[node].text = Some(Text {
            value: syntax.to_owned(),
            atomic: true,
        });
        node
    }

    fn handle(&mut self, data: Text, first: usize, depth: usize) -> Option<Text> {
        self.step(depth)?;
        if data.atomic {
            return Some(data);
        }
        self.state.registry_used |= !self.patterns.is_empty();
        let mut data = data.value;
        for index in first..self.patterns.len() {
            if self.patterns[index]
                .excludes
                .iter()
                .any(|tag| self.state.ancestors.contains(tag))
            {
                continue;
            }
            loop {
                self.step(depth)?;
                let kind = self.patterns[index].kind;
                let (before, after, mut stashed) = match kind {
                    0 => {
                        (self.guard)(index, &data)?;
                        break;
                    }
                    1 => {
                        let Some(matched) = find_inline_code(&data, 0) else {
                            break;
                        };
                        let begin = data.char_indices().nth(matched.start)?.0;
                        let end = data
                            .char_indices()
                            .nth(matched.end)
                            .map_or(data.len(), |(i, _)| i);
                        let value = matched.rendered_text();
                        let stashed = if matched.code.is_some() {
                            let node = self.add_node("code");
                            self.state.nodes[node].text = Some(Text {
                                value,
                                atomic: true,
                            });
                            Stashed::Node(node)
                        } else {
                            Stashed::Text(Text::plain(value))
                        };
                        (data[..begin].to_owned(), data[end..].to_owned(), stashed)
                    }
                    2..=5 => {
                        let tag = match kind {
                            2 => "strong,em",
                            3 => "strong",
                            4 => "em",
                            _ => "del",
                        };
                        let Some(matched) = find_emphasis(&data, tag) else {
                            break;
                        };
                        let node = self.add_node(if kind == 2 { "strong" } else { tag });
                        let text_node = if kind == 2 {
                            let child = self.add_node("em");
                            self.state.nodes[node].children.push(child);
                            child
                        } else {
                            node
                        };
                        self.state.nodes[text_node].text = Some(Text::plain(matched.text));
                        (
                            matched.before.to_owned(),
                            matched.after.to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    6 => {
                        let Some((start, end)) = find_not_strong(&data) else {
                            break;
                        };
                        (
                            data[..start].to_owned(),
                            data[end..].to_owned(),
                            Stashed::Text(Text::plain(&data[start..end])),
                        )
                    }
                    7 => {
                        let Some(start) = data.find('\n') else {
                            break;
                        };
                        (
                            data[..start].to_owned(),
                            data[start + 1..].to_owned(),
                            Stashed::Node(self.add_node("br")),
                        )
                    }
                    8 => {
                        let Some((start, end)) = find_named_emoji(&data)? else {
                            break;
                        };
                        let catalog = self.patterns[index].emoji.as_ref()?;
                        let syntax = &data[start..end];
                        let name = &syntax[1..syntax.len() - 1];
                        let title = name.replace('_', " ");
                        let source = catalog.realm.get(name).map(String::as_str).or_else(|| {
                            (name == "zulip")
                                .then_some("/static/generated/emoji/images/emoji/unicode/zulip.png")
                        });
                        let stashed = if let Some(source) = source {
                            let attributes = vec![
                                ("src".to_owned(), source.to_owned()),
                                ("class".to_owned(), "emoji".to_owned()),
                                ("alt".to_owned(), syntax.to_owned()),
                                ("title".to_owned(), title),
                            ];
                            let node = self.add_node("img");
                            self.state.nodes[node].attributes = attributes;
                            Stashed::Node(node)
                        } else if let Some(codepoint) = catalog.unicode.get(name) {
                            let codepoint = codepoint.clone();
                            Stashed::Node(self.emoji_node(&codepoint, syntax))
                        } else {
                            Stashed::Text(Text::plain(syntax))
                        };
                        (data[..start].to_owned(), data[end..].to_owned(), stashed)
                    }
                    9 => {
                        let Some(catalog) = &self.patterns[index].emoticons else {
                            break;
                        };
                        let Some((start, end)) = find_emoticon(&data, &catalog.conversions) else {
                            break;
                        };
                        let mut syntax = data[start..end].to_owned();
                        for (literal, replacement) in &catalog.conversions {
                            syntax = syntax.replace(literal, replacement);
                        }
                        if !syntax.starts_with(':') || !syntax.ends_with(':') || syntax.len() < 2 {
                            return None;
                        }
                        let codepoint = catalog.unicode.get(&syntax[1..syntax.len() - 1])?.clone();
                        let node = self.emoji_node(&codepoint, &syntax);
                        (
                            data[..start].to_owned(),
                            data[end..].to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    10 => {
                        let catalog = self.patterns[index].unicode_emoji.as_ref()?;
                        let Some(found) = markdown_unicode_emoji::find(&data, catalog) else {
                            break;
                        };
                        let node = self.emoji_node(&found.codepoint, &found.syntax);
                        (
                            data[..found.start].to_owned(),
                            data[found.end..].to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    11 => {
                        if !data.contains('[') {
                            break;
                        }
                        let stash = snapshot_text_stash(&self.state.nodes, &self.state.stash)?;
                        let policy = self.patterns[index].links.as_ref()?.clone();
                        let mut found = None;
                        for (start, _) in data.match_indices('[') {
                            self.step(depth)?;
                            // Failed openers can each scan the entire remaining suffix.
                            self.remaining_link_bytes =
                                self.remaining_link_bytes.checked_sub(data.len() - start)?;
                            if let Some(link) =
                                markdown_links::prepare(&data, start, Some(&stash), &policy).ok()?
                            {
                                found = Some((start, link));
                                break;
                            }
                        }
                        let Some((start, link)) = found else {
                            break;
                        };
                        let node = self.add_node("a");
                        self.state.nodes[node]
                            .attributes
                            .push(("href".to_owned(), link.href));
                        if let Some(title) = link.title {
                            self.state.nodes[node]
                                .attributes
                                .push(("title".to_owned(), title));
                        }
                        if link.reply {
                            self.state.nodes[node]
                                .attributes
                                .push(("class".to_owned(), "message-reply-link".to_owned()));
                        }
                        self.state.nodes[node].text = Some(Text {
                            value: link.text,
                            atomic: link.reply_parts.is_none(),
                        });
                        if let Some((sender, excerpt)) = link.reply_parts {
                            for (tag, class, value) in [
                                ("strong", "message-reply-sender", sender),
                                ("span", "message-reply-excerpt", excerpt),
                            ] {
                                let child = self.add_node(tag);
                                self.state.nodes[child].attributes = vec![
                                    ("class".to_owned(), class.to_owned()),
                                    ("dir".to_owned(), "auto".to_owned()),
                                ];
                                self.state.nodes[child].text = Some(Text {
                                    value,
                                    atomic: true,
                                });
                                self.state.nodes[node].children.push(child);
                            }
                        }
                        (
                            data[..start].to_owned(),
                            data[link.end..].to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    12 | 13 => {
                        if !data.contains("![") {
                            break;
                        }
                        let media = self.media?;
                        let stash = snapshot_text_stash(&self.state.nodes, &self.state.stash)?;
                        let policy = self.patterns[index].links.as_ref()?.clone();
                        let mut found = None;
                        for (start, _) in data.match_indices("![") {
                            self.step(depth)?;
                            self.remaining_link_bytes =
                                self.remaining_link_bytes.checked_sub(data.len() - start)?;
                            let (element, attachment) = if kind == 12 {
                                let outcome = markdown_media::prepare_audio(
                                    &data,
                                    start,
                                    Some(&stash),
                                    &policy,
                                    media.realm_host.as_deref(),
                                    media.audio_previews.as_ref(),
                                    &media.audio_policy,
                                )
                                .ok()?;
                                (
                                    outcome.audio.map(|audio| (audio.end, audio.attributes)),
                                    outcome.attachment_path,
                                )
                            } else {
                                let outcome = markdown_media::prepare_image(
                                    &data,
                                    start,
                                    Some(&stash),
                                    &policy,
                                    media.realm_host.as_deref(),
                                    media.image_previews.as_ref(),
                                )
                                .ok()?;
                                (
                                    outcome.image.map(|image| (image.end, image.attributes)),
                                    outcome.attachment_path,
                                )
                            };
                            if let Some(path) = attachment {
                                self.remaining_media_bytes =
                                    self.remaining_media_bytes.checked_sub(path.len())?;
                                self.state.attachment_paths.push(path);
                            }
                            if let Some(element) = element {
                                for (key, value) in &element.1 {
                                    self.remaining_media_bytes = self
                                        .remaining_media_bytes
                                        .checked_sub(key.len())?
                                        .checked_sub(value.len())?;
                                }
                                found = Some((start, element));
                                break;
                            }
                        }
                        let Some((start, (end, attributes))) = found else {
                            break;
                        };
                        let node = self.add_node(if kind == 12 { "audio" } else { "img" });
                        self.state.nodes[node].attributes = attributes;
                        self.state.has_image |= kind == 13;
                        (
                            data[..start].to_owned(),
                            data[end..].to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    14..=16 => {
                        if !data.contains("#**") {
                            break;
                        }
                        let channels = self.channels?;
                        let kind = match kind {
                            14 => ChannelKind::Channel,
                            15 => ChannelKind::Topic,
                            _ => ChannelKind::Message,
                        };
                        let mut start = 0;
                        let mut found = None;
                        loop {
                            self.step(depth)?;
                            self.remaining_link_bytes =
                                self.remaining_link_bytes.checked_sub(data.len() - start)?;
                            let Some(candidate) = markdown_channel_syntax::find(
                                &data,
                                start,
                                kind,
                                &channels.unicode_decimal,
                            )
                            .ok()?
                            else {
                                break;
                            };
                            start = candidate.range.end;
                            let channel_name = &data[candidate.channel];
                            let Some(channel) = channels.names.get(channel_name) else {
                                continue;
                            };
                            let topic_name = candidate.topic.map(|range| &data[range]);
                            if topic_name.is_some_and(|topic| !topic.is_empty())
                                && channel.empty_topic_only
                            {
                                continue;
                            }
                            let with_operand = topic_name.and_then(|topic| {
                                channels
                                    .topic_with
                                    .get(&(channel_name.to_owned(), topic.to_owned()))
                                    .copied()
                            });
                            let link = markdown_channel_spans::channel_link(
                                channel.id,
                                channel_name,
                                topic_name,
                                with_operand,
                                candidate.message.map(|range| &data[range]),
                            );
                            found = Some((candidate.range, link));
                            break;
                        }
                        let Some((range, link)) = found else {
                            break;
                        };
                        self.remaining_channel_bytes =
                            self.remaining_channel_bytes.checked_sub(link.text.len())?;
                        for (key, value) in &link.attributes {
                            self.remaining_channel_bytes = self
                                .remaining_channel_bytes
                                .checked_sub(key.len())?
                                .checked_sub(value.len())?;
                        }
                        if link.empty_topic {
                            self.remaining_channel_bytes = self
                                .remaining_channel_bytes
                                .checked_sub(channels.empty_topic_name.len())?;
                        }
                        let node = self.add_node("a");
                        self.state.nodes[node].attributes = link
                            .attributes
                            .into_iter()
                            .map(|(key, value)| (key.to_owned(), value))
                            .collect();
                        self.state.nodes[node].text = Some(Text {
                            value: link.text,
                            atomic: true,
                        });
                        if link.empty_topic {
                            let child = self.add_node("em");
                            self.state.nodes[child].text =
                                Some(Text::plain(&channels.empty_topic_name));
                            self.state.nodes[child].tail = link.tail.map(|tail| Text {
                                value: tail.to_owned(),
                                atomic: true,
                            });
                            self.state.nodes[node].children.push(child);
                        }
                        (
                            data[..range.start].to_owned(),
                            data[range.end..].to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    17 | 18 => {
                        if !data.contains('@') {
                            break;
                        }
                        let kind = if kind == 17 {
                            MentionKind::User
                        } else {
                            MentionKind::Group
                        };
                        let mut start = 0;
                        let mut found = None;
                        loop {
                            self.step(depth)?;
                            self.remaining_link_bytes =
                                self.remaining_link_bytes.checked_sub(data.len() - start)?;
                            let Some(candidate) =
                                markdown_mention_syntax::find(&data, start, kind).ok()?
                            else {
                                break;
                            };
                            let mentions = self.mentions?;
                            let Some(snapshot) = mentions.data.as_ref() else {
                                break;
                            };
                            start = candidate.range.end;
                            let name = &data[candidate.name];
                            let span = if matches!(kind, MentionKind::User) {
                                let mut missing_name = false;
                                let span = markdown_inline_mentions::prepare_user(
                                    name,
                                    candidate.silent,
                                    &mentions.user_policy,
                                    |reference| {
                                        let user = match reference {
                                            UserReference::Name(name) => {
                                                match snapshot.users_by_name.get(name) {
                                                    Some(user) => user.as_ref(),
                                                    None => {
                                                        missing_name = true;
                                                        None
                                                    }
                                                }
                                            }
                                            UserReference::Id(id) => snapshot.users_by_id.get(&id),
                                        }?;
                                        Some(MentionUser {
                                            id: user.id,
                                            full_name: &user.full_name,
                                            is_active: user.is_active,
                                        })
                                    },
                                )
                                .ok()?;
                                if missing_name {
                                    return None;
                                }
                                span
                            } else {
                                snapshot.groups_by_name.get(name)?.as_ref().map(|group| {
                                    markdown_inline_mentions::group_mention_span(
                                        group.id,
                                        &group.display_name,
                                        candidate.silent,
                                        group.deactivated,
                                    )
                                })
                            };
                            if let Some(span) = span {
                                found = Some((candidate.range, span));
                                break;
                            }
                        }
                        let Some((range, span)) = found else {
                            break;
                        };
                        self.remaining_mention_bytes =
                            self.remaining_mention_bytes.checked_sub(span.text.len())?;
                        for (key, value) in &span.attributes {
                            self.remaining_mention_bytes = self
                                .remaining_mention_bytes
                                .checked_sub(key.len())?
                                .checked_sub(value.len())?;
                        }
                        match span.notification {
                            Some(MentionNotification::User(id)) => {
                                self.state.mentions.users.insert(id);
                            }
                            Some(MentionNotification::Group(id)) => {
                                self.state.mentions.groups.insert(id);
                            }
                            Some(MentionNotification::Channel) => {
                                self.state.mentions.channel = true
                            }
                            Some(MentionNotification::Topic) => self.state.mentions.topic = true,
                            None => {}
                        }
                        let node = self.add_node("span");
                        self.state.nodes[node].attributes = span
                            .attributes
                            .into_iter()
                            .map(|(key, value)| (key.to_owned(), value))
                            .collect();
                        self.state.nodes[node].text = Some(Text {
                            value: span.text,
                            atomic: true,
                        });
                        (
                            data[..range.start].to_owned(),
                            data[range.end..].to_owned(),
                            Stashed::Node(node),
                        )
                    }
                    19 => {
                        self.remaining_timestamp_bytes =
                            self.remaining_timestamp_bytes.checked_sub(data.len())?;
                        let Some(start) = data.find("<time:") else {
                            break;
                        };
                        let body = start + "<time:".len();
                        let Some(length) = data[body..].find('>') else {
                            break;
                        };
                        let end = body + length;
                        let source = &data[body..end];
                        let stashed = match markdown_timestamp::render(source)? {
                            Timestamp::Invalid => {
                                Stashed::Text(Text::plain(format!("&lt;time:{source}&gt;")))
                            }
                            Timestamp::Valid(value) => {
                                let node = self.add_node("time");
                                self.state.nodes[node].attributes =
                                    vec![("datetime".to_owned(), value)];
                                self.state.nodes[node].text = Some(Text {
                                    value: source.to_owned(),
                                    atomic: true,
                                });
                                Stashed::Node(node)
                            }
                        };
                        (
                            data[..start].to_owned(),
                            data[end + 1..].to_owned(),
                            stashed,
                        )
                    }
                    20 => {
                        self.remaining_entity_bytes =
                            self.remaining_entity_bytes.checked_sub(data.len())?;
                        let Some((start, end)) = find_entity(&data) else {
                            break;
                        };
                        let counter = self
                            .html_counter?
                            .checked_add(self.state.html_blocks.len())?;
                        self.state.html_blocks.push(data[start..end].to_owned());
                        (
                            data[..start].to_owned(),
                            data[end..].to_owned(),
                            Stashed::Text(Text::plain(format!("\u{2}wzxhzdk:{counter}\u{3}"))),
                        )
                    }
                    21 => {
                        let Some(range) = self
                            .autolinks?
                            .find(&data, &mut self.remaining_autolink_bytes)?
                        else {
                            break;
                        };
                        let url = &data[range.clone()];
                        // The legacy Pattern suffix uses '$', before a final LF.
                        let suffix_end = if range.end < data.len() && data.ends_with('\n') {
                            data.len() - 1
                        } else {
                            data.len()
                        };
                        let policy = self.patterns[index].links.as_ref()?;
                        let stashed = match markdown_links::sanitize(url, policy).ok()? {
                            None => Stashed::Text(Text::plain(url)),
                            Some(href) => {
                                let href = markdown_links::rewrite_local(href, policy);
                                let node = self.add_node("a");
                                self.state.nodes[node].attributes = vec![("href".to_owned(), href)];
                                self.state.nodes[node].text = Some(Text {
                                    value: url.to_owned(),
                                    atomic: true,
                                });
                                Stashed::Node(node)
                            }
                        };
                        (
                            data[..range.start].to_owned(),
                            data[range.end..suffix_end].to_owned(),
                            stashed,
                        )
                    }
                    _ => return None,
                };
                if let Stashed::Node(node) = &mut stashed {
                    let node = *node;
                    if !self.state.nodes[node]
                        .text
                        .as_ref()
                        .is_some_and(|text| text.atomic)
                    {
                        let mut children = vec![node];
                        children.extend_from_slice(&self.state.nodes[node].children);
                        for child in children {
                            if let Some(text) = self.state.nodes[child]
                                .text
                                .clone()
                                .filter(|t| !t.value.is_empty())
                            {
                                self.state
                                    .ancestors
                                    .push(self.state.nodes[child].lower_tag.clone());
                                let text = self.handle(text, index + 1, depth + 1)?;
                                self.state.nodes[child].text = Some(text);
                                self.state.ancestors.pop();
                            }
                            if let Some(tail) = self.state.nodes[child]
                                .tail
                                .clone()
                                .filter(|t| !t.value.is_empty())
                            {
                                let tail = self.handle(tail, index, depth + 1)?;
                                self.state.nodes[child].tail = Some(tail);
                            }
                        }
                    }
                }
                let id = self.state.stash.len();
                self.state.stash.push(stashed);
                data = format!("{before}\u{2}klzzwxh:{id:04}\u{3}{after}");
            }
        }
        Some(Text::plain(data))
    }

    fn link(&mut self, parent: usize, result: &[(usize, Vec<String>)], is_text: bool, text: Text) {
        if text.value.is_empty() {
            return;
        }
        let target = if let Some((last, _)) = result.last() {
            &mut self.state.nodes[*last].tail
        } else if is_text {
            &mut self.state.nodes[parent].text
        } else {
            &mut self.state.nodes[parent].tail
        };
        if let Some(previous) = target.as_mut().filter(|t| !t.value.is_empty()) {
            previous.value.push_str(&text.value);
            previous.atomic = false;
        } else {
            *target = Some(text);
        }
    }

    fn placeholders(
        &mut self,
        data: Option<Text>,
        parent: usize,
        is_text: bool,
        depth: usize,
    ) -> Option<Vec<(usize, Vec<String>)>> {
        self.step(depth)?;
        let mut result = Vec::new();
        let Some(data) = data else {
            return Some(result);
        };
        let atomic = data.atomic;
        let mut scanner = Scanner::new(data.value);
        let mut accepted = false;
        while let Some(step) = scanner.next(accepted) {
            self.step(depth)?;
            let Some(rejected) = step.rejected_text else {
                self.link(
                    parent,
                    &result,
                    is_text,
                    Text {
                        value: step.text,
                        atomic,
                    },
                );
                break;
            };
            let id = step.identifier.as_ref().and_then(|id| {
                let number = id.parse::<usize>().ok()?;
                (format!("{number:04}") == *id && number < self.state.stash.len()).then_some(number)
            });
            accepted = id.is_some();
            let Some(id) = id else {
                self.link(parent, &result, is_text, Text::plain(rejected));
                continue;
            };
            self.link(parent, &result, is_text, Text::plain(step.text));
            match &self.state.stash[id] {
                Stashed::Text(text) => {
                    self.link(parent, &result, is_text, text.clone());
                }
                Stashed::Node(node) => {
                    let node = *node;
                    let mut children = vec![node];
                    children.extend_from_slice(&self.state.nodes[node].children);
                    for child in children {
                        if self.state.nodes[child]
                            .tail
                            .as_ref()
                            .is_some_and(|t| t.value.chars().any(|c| !is_python_whitespace(c)))
                        {
                            self.element_text(node, child, false, depth + 1)?;
                        }
                        if self.state.nodes[child]
                            .text
                            .as_ref()
                            .is_some_and(|t| t.value.chars().any(|c| !is_python_whitespace(c)))
                        {
                            self.element_text(child, child, true, depth + 1)?;
                        }
                    }
                    result.push((node, self.state.ancestors.clone()));
                }
            }
        }
        Some(result)
    }

    fn element_text(
        &mut self,
        node: usize,
        subnode: usize,
        is_text: bool,
        depth: usize,
    ) -> Option<()> {
        self.step(depth)?;
        let data = if is_text {
            self.state.nodes[subnode].text.take()
        } else {
            self.state.nodes[subnode].tail.take()
        };
        let children = self.placeholders(data, subnode, is_text, depth + 1)?;
        let position = if !is_text && node != subnode {
            self.state.nodes[node]
                .children
                .iter()
                .position(|child| *child == subnode)?
                + 1
        } else {
            0
        };
        for (child, _) in children.into_iter().rev() {
            self.state.nodes[node].children.insert(position, child);
        }
        Some(())
    }

    fn run(mut self, ancestors: Vec<String>) -> Option<State> {
        let mut pending = vec![(0, 0)];
        while let Some((node, depth)) = pending.pop() {
            self.step(depth)?;
            let children = self.state.nodes.get(node)?.children.clone();
            for &child in &children {
                self.set_parent(child, node)?;
            }
            pending.extend(children.into_iter().rev().map(|child| (child, depth + 1)));
        }
        let mut stack = VecDeque::from([(0, ancestors)]);
        while let Some((current, parents)) = stack.pop_front() {
            self.step(0)?;
            self.state.ancestors = parents;
            let mut ancestors = Vec::new();
            let mut node = Some(current);
            while let Some(parent) = node {
                self.step(0)?;
                ancestors.push(self.state.nodes[parent].lower_tag.clone());
                node = self.state.parents[parent];
            }
            self.state.ancestors.extend(ancestors.into_iter().rev());
            let mut insert_queue = Vec::new();
            let mut position = 0;
            while position < self.state.nodes[current].children.len() {
                self.step(0)?;
                let child = self.state.nodes[current].children[position];
                if self.state.nodes[child]
                    .text
                    .as_ref()
                    .is_some_and(|t| !t.atomic && !t.value.is_empty())
                {
                    self.state
                        .ancestors
                        .push(self.state.nodes[child].lower_tag.clone());
                    let data = self.state.nodes[child].text.take()?;
                    let data = self.handle(data, 0, 0)?;
                    let children = self.placeholders(Some(data), child, true, 0)?;
                    for (new_child, _) in &children {
                        self.set_parent(*new_child, child)?;
                    }
                    stack.extend(children.iter().cloned());
                    insert_queue.push((child, children));
                    self.state.ancestors.pop();
                }
                if self.state.nodes[child]
                    .tail
                    .as_ref()
                    .is_some_and(|t| !t.value.is_empty())
                {
                    let tail = self.state.nodes[child].tail.take()?;
                    let tail = self.handle(tail, 0, 0)?;
                    let dummy = self.add_node("d");
                    let children = self.placeholders(Some(tail), dummy, false, 0)?;
                    self.state.nodes[child].tail = self.state.nodes[dummy].tail.take();
                    let at = self.state.nodes[current]
                        .children
                        .iter()
                        .position(|n| *n == child)?
                        + 1;
                    for (new_child, _) in children.into_iter().rev() {
                        self.set_parent(new_child, current)?;
                        self.state.nodes[current].children.insert(at, new_child);
                    }
                }
                if !self.state.nodes[child].children.is_empty() {
                    self.set_parent(child, current)?;
                    stack.push_back((child, self.state.ancestors.clone()));
                }
                position += 1;
            }
            for (node, children) in insert_queue {
                for (index, (child, _)) in children.into_iter().enumerate() {
                    self.state.nodes[node].children.insert(index, child);
                }
            }
        }
        Some(self.state)
    }
}

fn find_entity(data: &str) -> Option<(usize, usize)> {
    for (start, _) in data.match_indices('&') {
        let body = &data.as_bytes()[start + 1..];
        let (prefix, accepts): (usize, fn(&u8) -> bool) = if body.starts_with(b"#x") {
            (2, u8::is_ascii_hexdigit)
        } else if body.starts_with(b"#") {
            (1, u8::is_ascii_digit)
        } else {
            (0, u8::is_ascii_alphanumeric)
        };
        let length = body[prefix..].iter().take_while(|c| accepts(c)).count();
        if length > 0 && body.get(prefix + length) == Some(&b';') {
            return Some((start, start + 1 + prefix + length + 1));
        }
    }
    None
}

fn find_not_strong(data: &str) -> Option<(usize, usize)> {
    let mut previous = None;
    for (start, c) in data.char_indices() {
        if matches!(c, '*' | '_') && previous.is_none_or(is_python_whitespace) {
            let length = data[start..].chars().take_while(|next| *next == c).count();
            let end = start + length;
            if length <= 3 && data[end..].chars().next().is_none_or(is_python_whitespace) {
                return Some((start, end));
            }
        }
        previous = Some(c);
    }
    None
}

fn find_emoticon(data: &str, conversions: &[(String, String)]) -> Option<(usize, usize)> {
    fn terminal(c: char) -> bool {
        ",.;?!()[] \"'\n\t".contains(c)
    }
    for (start, _) in data.char_indices() {
        if data[..start]
            .chars()
            .next_back()
            .is_some_and(|c| !terminal(c))
        {
            continue;
        }
        for (literal, _) in conversions {
            if literal.is_empty() || !data[start..].starts_with(literal) {
                continue;
            }
            let end = start + literal.len();
            if data[end..].chars().next().is_none_or(terminal) {
                return Some((start, end));
            }
        }
    }
    None
}

fn find_named_emoji(data: &str) -> Option<Option<(usize, usize)>> {
    for (start, _) in data.match_indices(':') {
        for (offset, c) in data[start + 1..].char_indices() {
            if c == ':' {
                if offset > 0 {
                    return Some(Some((start, start + offset + 2)));
                }
                break;
            }
            // Python's Unicode word class depends on its Unicode database version.
            if !c.is_ascii() {
                return None;
            }
            if !c.is_ascii_alphanumeric() && !matches!(c, '_' | '-' | '+') {
                break;
            }
        }
    }
    Some(None)
}

/// Return a complete plan rooted at node 0, or delegate the entire run.
///
/// The guard receives a pattern index and the text remaining at that priority.
/// It must return `Some(())` only when the host handler would have no effect.
/// It must not mutate host state, since a later guard or resource limit can
/// reject the plan. Invalid indices, cycles, and depth or work limits also
/// return `None`. Returned nodes include detached stash and temporary nodes;
/// the caller commits graph edges, parent mappings and stash identities together.
pub fn run(
    nodes: Vec<Node>,
    patterns: &[Pattern],
    ancestors: Vec<String>,
    guard: impl FnMut(usize, &str) -> Option<()>,
) -> Option<State> {
    run_with_media(nodes, patterns, ancestors, None, guard)
}

/// Plan media in registry order, keeping all metadata pending until success.
/// A rejected candidate may record an attachment, and scanning restarts after
/// each successful substitution just as in Python-Markdown. Do not deduplicate
/// these effects. Any unsupported input/guard/resource limit discards them with
/// the entire graph. The caller must guard the live media helpers first.
pub fn run_with_media(
    nodes: Vec<Node>,
    patterns: &[Pattern],
    ancestors: Vec<String>,
    media: Option<&Media>,
    guard: impl FnMut(usize, &str) -> Option<()>,
) -> Option<State> {
    run_with_inputs(
        nodes,
        patterns,
        ancestors,
        Inputs {
            media,
            ..Inputs::default()
        },
        guard,
    )
}

/// Plan with authorized snapshots, preserving the supplied registry order.
///
/// The caller must guard live syntax, lookup methods, topic policy, localized
/// fallback text and Unicode properties before supplying channel data. This
/// neither authorizes links nor verifies message existence. Mention snapshots
/// require guarded syntax, host lookup semantics, localized group names and
/// live decimal properties/conversion limits. All graph, media and notification
/// effects are discarded together on unsupported input or resource limits.
pub fn run_with_inputs(
    nodes: Vec<Node>,
    patterns: &[Pattern],
    ancestors: Vec<String>,
    inputs: Inputs<'_>,
    guard: impl FnMut(usize, &str) -> Option<()>,
) -> Option<State> {
    if nodes.is_empty()
        || nodes.len() > 20_000
        || patterns.iter().any(|pattern| {
            pattern.kind == 10
                && pattern.unicode_emoji.as_ref().is_none_or(|catalog| {
                    catalog.regex_version != markdown_unicode_emoji::REGEX_VERSION
                })
        })
    {
        return None;
    }
    let parents = vec![None; nodes.len()];
    Engine {
        state: State {
            nodes,
            parents,
            stash: Vec::new(),
            parent_order: Vec::new(),
            registry_used: false,
            ancestors: Vec::new(),
            attachment_paths: Vec::new(),
            has_image: false,
            mentions: MentionEffects::default(),
            html_blocks: Vec::new(),
        },
        patterns,
        media: inputs.media,
        channels: inputs.channels,
        mentions: inputs.mentions,
        guard,
        remaining: 100_000,
        remaining_link_bytes: 8 * 1024 * 1024,
        remaining_media_bytes: 8 * 1024 * 1024,
        remaining_channel_bytes: 8 * 1024 * 1024,
        remaining_mention_bytes: 8 * 1024 * 1024,
        remaining_timestamp_bytes: 8 * 1024 * 1024,
        remaining_entity_bytes: 8 * 1024 * 1024,
        remaining_autolink_bytes: 8 * 1024 * 1024,
        html_counter: inputs.html_counter,
        autolinks: inputs.autolinks,
    }
    .run(ancestors)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_invalid_graphs_and_unknown_handlers() {
        assert!(run(vec![], &[], vec![], |_, _| Some(())).is_none());
        let mut root = Node::new("div");
        root.children = vec![1];
        assert!(run(vec![root], &[], vec![], |_, _| Some(())).is_none());
        let mut root = Node::new("div");
        root.children = vec![0];
        assert!(run(vec![root], &[], vec![], |_, _| Some(())).is_none());
        let mut root = Node::new("div");
        root.children = vec![1];
        let mut paragraph = Node::new("p");
        paragraph.text = Some(Text::plain("text"));
        assert!(
            run(
                vec![root, paragraph],
                &[Pattern {
                    kind: 255,
                    excludes: vec![],
                    emoji: None,
                    emoticons: None,
                    unicode_emoji: None,
                    links: None,
                }],
                vec![],
                |_, _| Some(())
            )
            .is_none()
        );
    }

    #[test]
    fn rejected_guard_discards_the_entire_plan() {
        let mut root = Node::new("div");
        root.children = vec![1];
        let mut paragraph = Node::new("p");
        paragraph.text = Some(Text::plain("**bold** unsupported"));
        let patterns = [
            Pattern {
                kind: 3,
                excludes: vec![],
                emoji: None,
                emoticons: None,
                unicode_emoji: None,
                links: None,
            },
            Pattern {
                kind: 0,
                excludes: vec![],
                emoji: None,
                emoticons: None,
                unicode_emoji: None,
                links: None,
            },
        ];
        let mut calls = 0;
        assert!(
            run(vec![root, paragraph], &patterns, vec![], |_, _| {
                calls += 1;
                None
            })
            .is_none()
        );
        assert_eq!(calls, 1);
    }

    #[test]
    fn atomic_text_does_not_use_the_registry() {
        let mut root = Node::new("div");
        root.children = vec![1];
        let mut paragraph = Node::new("p");
        paragraph.text = Some(Text {
            value: "**atomic**".to_owned(),
            atomic: true,
        });
        let result = run(
            vec![root, paragraph],
            &[Pattern {
                kind: 0,
                excludes: vec![],
                emoji: None,
                emoticons: None,
                unicode_emoji: None,
                links: None,
            }],
            vec![],
            |_, _| panic!("atomic string matched"),
        )
        .unwrap();
        assert!(!result.registry_used);
        assert!(result.stash.is_empty());
        assert_eq!(result.nodes[1].text.as_ref().unwrap().value, "**atomic**");
    }
}

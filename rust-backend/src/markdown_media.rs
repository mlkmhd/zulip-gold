//! Prepare uploaded Markdown images and their pending rendering metadata.
//!
//! This is independent of Python and does not mutate a tree or rendering result.
//! Callers must discard the whole plan on `Unsupported`, and commit attachment
//! effects even for a rejected image candidate when the plan succeeds.

use std::collections::{HashMap, HashSet};

use sha1_smol::Sha1;

use crate::draft_text::is_python_whitespace;
use crate::markdown_link_syntax::{TextStash, parse_image, prepare_text, unescape};
use crate::markdown_links::{Policy, Unsupported, rewrite_local, sanitize, split};

/// Live, initialized CPython 3.12 POSIX mimetypes tables (strict lookup).
/// Values and supported types must be snapshotted rather than inferred from the
/// Rust host's MIME database. Non-ASCII extensions require reference handling.
#[derive(Default)]
pub struct MimePolicy {
    pub suffix_map: HashMap<String, String>,
    pub encodings: HashSet<String>,
    pub types: HashMap<String, String>,
    pub supported_audio_types: HashSet<String>,
    pub param_schemes: HashSet<String>,
}

fn split_extension(path: &str) -> (&str, &str) {
    let start = path.rfind('/').map_or(0, |at| at + 1);
    if let Some(dot) = path.rfind('.')
        && dot >= start
        && path[start..dot].bytes().any(|byte| byte != b'.')
    {
        return path.split_at(dot);
    }
    (path, "")
}

/// Match CPython 3.12's strict guess_type on POSIX, without filesystem reads.
/// Only the MIME type is needed; compression encodings still affect lookup.
/// Cyclic or excessive suffix expansions require whole-plan delegation.
pub fn mime_type(url: &str, policy: &MimePolicy) -> Result<Option<String>, Unsupported> {
    let cleaned = url
        .trim_start_matches(|c| c <= '\u{20}')
        .replace(['\t', '\r', '\n'], "");
    let parts = split(&cleaned)?;
    let path = if parts.scheme.len() > 1 {
        let mut path = parts.path;
        if policy.param_schemes.contains(&parts.scheme) {
            let start = path.rfind('/').map_or(0, |at| at + 1);
            if let Some(at) = path[start..].find(';') {
                path = &path[..start + at];
            }
        }
        if parts.scheme == "data" {
            let Some((header, _)) = path.split_once(',') else {
                return Ok(None);
            };
            let content_type = header.split(';').next().unwrap();
            return Ok(Some(
                if content_type.contains('=') || !content_type.contains('/') {
                    "text/plain"
                } else {
                    content_type
                }
                .to_owned(),
            ));
        }
        path
    } else {
        url
    };
    let mut expanded = path.to_owned();
    for _ in 0..64 {
        let (base, extension) = split_extension(&expanded);
        if !extension.is_ascii() {
            return Err(Unsupported);
        }
        if let Some(suffix) = policy.suffix_map.get(&extension.to_ascii_lowercase()) {
            if base.len().saturating_add(suffix.len()) > 8 * 1024 * 1024 {
                return Err(Unsupported);
            }
            expanded = format!("{base}{suffix}");
            continue;
        }
        let extension = if policy.encodings.contains(extension) {
            split_extension(base).1
        } else {
            extension
        };
        if !extension.is_ascii() {
            return Err(Unsupported);
        }
        return Ok(policy.types.get(&extension.to_ascii_lowercase()).cloned());
    }
    Err(Unsupported)
}

/// Live static/Camo settings. A public upload root must be an authorized base
/// URL ending in '/', and None means local uploads. No credentials are logged.
pub struct AudioPolicy {
    pub mime: MimePolicy,
    pub static_url: String,
    pub external_uri_scheme: String,
    pub public_upload_root: Option<String>,
    pub camo_uri: String,
    pub camo_key: Option<String>,
}

/// Match the reference's static URL, current realm and public upload exemptions.
pub fn is_static_url(
    url: &str,
    realm_host: Option<&str>,
    policy: &AudioPolicy,
) -> Result<bool, Unsupported> {
    let clean = |url: &str| {
        url.trim_start_matches(|c| c <= '\u{20}')
            .replace(['\t', '\r', '\n'], "")
    };
    let cleaned = clean(url);
    let parts = split(&cleaned)?;
    let static_url = clean(&policy.static_url);
    let static_parts = split(&static_url)?;
    if (parts.netloc == static_parts.netloc && url.starts_with(&policy.static_url))
        || (Some(parts.netloc) == realm_host
            && format!("{}://", parts.scheme) == policy.external_uri_scheme)
        || (parts.netloc.is_empty() && parts.scheme.is_empty())
    {
        return Ok(true);
    }
    if let Some(root) = &policy.public_upload_root {
        if !root.ends_with('/') {
            return Err(Unsupported);
        }
        return Ok(url.starts_with(root));
    }
    Ok(false)
}

/// Encode the original UTF-8 URL using the existing Camo HMAC-SHA1 protocol.
/// Disabled Camo preserves the URL even when no key is configured.
pub fn camo_url(url: &str, policy: &AudioPolicy) -> Result<String, Unsupported> {
    if policy.camo_uri.is_empty() {
        return Ok(url.to_owned());
    }
    let key = policy.camo_key.as_ref().ok_or(Unsupported)?.as_bytes();
    let mut block = [0; 64];
    if key.len() > block.len() {
        block[..20].copy_from_slice(&Sha1::from(key).digest().bytes());
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha1::new();
    inner.update(&block.map(|byte| byte ^ 0x36));
    inner.update(url.as_bytes());
    let mut outer = Sha1::new();
    outer.update(&block.map(|byte| byte ^ 0x5c));
    outer.update(&inner.digest().bytes());
    let encoded: String = url.bytes().map(|byte| format!("{byte:02x}")).collect();
    Ok(format!("{}{}/{encoded}", policy.camo_uri, outer.digest()))
}

#[derive(Debug, PartialEq, Eq)]
pub struct Audio {
    pub end: usize,
    /// Ordered ElementTree attributes. Values still require HTML escaping.
    pub attributes: Vec<(String, String)>,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct AudioOutcome {
    pub audio: Option<Audio>,
    /// Append even on media rejection, retaining candidate order and duplicates.
    pub attachment_path: Option<String>,
}

/// Prepare one audio candidate, without changing the tree or rendering result.
/// Preview IDs must be already authorized. An absent snapshot uses MIME lookup,
/// while a present empty snapshot rejects local uploads regardless of suffix.
/// Explicit link titles reject audio before any attachment effect. The audio
/// title is the stripped raw label, with no stash substitution or formatting.
pub fn prepare_audio(
    data: &str,
    start: usize,
    stash: Option<&TextStash>,
    policy: &Policy,
    realm_host: Option<&str>,
    previews: Option<&HashSet<String>>,
    audio_policy: &AudioPolicy,
) -> Result<AudioOutcome, Unsupported> {
    let Some(parsed) = parse_image(data, start).map_err(|_| Unsupported)? else {
        return Ok(AudioOutcome::default());
    };
    let prepared = prepare_text(data, &parsed, stash).ok_or(Unsupported)?;
    if prepared.title.is_some() {
        return Ok(AudioOutcome::default());
    }
    let restored = unescape(prepared.href.trim_matches(is_python_whitespace), stash);
    let Some(src) = sanitize(&restored, policy)? else {
        return Ok(AudioOutcome::default());
    };
    let src = rewrite_local(src, policy);
    let attachment_path = attachment_path(&src, realm_host)?;
    let supported =
        if let (Some(path), Some(previews)) = (src.strip_prefix("/user_uploads/"), previews) {
            previews.contains(path)
        } else {
            mime_type(&src, &audio_policy.mime)?
                .is_some_and(|mime| audio_policy.mime.supported_audio_types.contains(&mime))
        };
    if !supported {
        return Ok(AudioOutcome {
            audio: None,
            attachment_path,
        });
    }
    let final_src = if is_static_url(&src, realm_host, audio_policy)? {
        src.clone()
    } else {
        camo_url(&src, audio_policy)?
    };
    let mut attributes = vec![("src".to_owned(), final_src.clone())];
    let title = data[parsed.text].trim_matches(is_python_whitespace);
    if !title.is_empty() {
        attributes.push(("title".to_owned(), title.to_owned()));
    }
    attributes.extend([
        ("controls".to_owned(), "controls".to_owned()),
        ("preload".to_owned(), "metadata".to_owned()),
    ]);
    if src != final_src {
        attributes.push(("data-original-url".to_owned(), src));
    }
    Ok(AudioOutcome {
        audio: Some(Audio {
            end: parsed.end,
            attributes,
        }),
        attachment_path,
    })
}

/// Identify an attachment using CPython 3.12's standard urljoin scheme lists.
///
/// Unlike the fast common-path bridge, this resolves relative dot/empty segments
/// and preserves arbitrary Unicode suffixes. Unsupported authorities require
/// delegation; `Ok(None)` means there is no local attachment to record.
pub fn attachment_path(url: &str, realm_host: Option<&str>) -> Result<Option<String>, Unsupported> {
    crate::markdown_links::attachment_path(url, realm_host)
}

/// Already-authorized preview metadata, keyed by the exact raw upload suffix.
/// Dimensions are decimal strings so Python's unbounded integers remain exact.
pub struct ImageMetadata {
    pub width: String,
    pub height: String,
    pub content_type: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Image {
    pub end: usize,
    /// Ordered ElementTree attributes. Values still require HTML escaping.
    pub attributes: Vec<(String, String)>,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct ImageOutcome {
    pub image: Option<Image>,
    /// Append in candidate order, retaining duplicates even on media rejection.
    pub attachment_path: Option<String>,
}

/// Prepare one image candidate at a UTF-8 byte offset.
///
/// `previews` distinguishes absent message data from an empty preview snapshot.
/// Preview lookup deliberately uses the complete source suffix, including query
/// and fragment, while attachment recording uses the parsed URL path only.
/// The caller must snapshot CPython 3.12's standard urljoin scheme lists and
/// delegate if they have changed. Unicode/bracketed authorities also delegate.
/// `has_image` becomes true only when `image` is present; rejection never clears
/// an existing true value. Other rendering metadata remains unchanged.
pub fn prepare_image(
    data: &str,
    start: usize,
    stash: Option<&TextStash>,
    policy: &Policy,
    realm_host: Option<&str>,
    previews: Option<&HashMap<String, ImageMetadata>>,
) -> Result<ImageOutcome, Unsupported> {
    let Some(parsed) = parse_image(data, start).map_err(|_| Unsupported)? else {
        return Ok(ImageOutcome::default());
    };
    let prepared = prepare_text(data, &parsed, stash).ok_or(Unsupported)?;
    let restored = unescape(prepared.href.trim_matches(is_python_whitespace), stash);
    let Some(src) = sanitize(&restored, policy)? else {
        return Ok(ImageOutcome::default());
    };
    let src = rewrite_local(src, policy);
    let attachment_path = attachment_path(&src, realm_host)?;
    let metadata = src
        .strip_prefix("/user_uploads/")
        .and_then(|path_id| previews?.get(path_id));
    let Some(metadata) = metadata else {
        return Ok(ImageOutcome {
            image: None,
            attachment_path,
        });
    };
    let mut attributes = vec![(
        "src".to_owned(),
        "/static/images/loading/loader-black.svg".to_owned(),
    )];
    if let Some(title) = prepared.title {
        attributes.push(("title".to_owned(), title));
    }
    attributes.extend([
        ("alt".to_owned(), unescape(&data[parsed.text], stash)),
        ("data-original-src".to_owned(), src),
        (
            "class".to_owned(),
            "inline-image image-loading-placeholder".to_owned(),
        ),
        (
            "data-original-dimensions".to_owned(),
            format!("{}x{}", metadata.width, metadata.height),
        ),
    ]);
    if let Some(content_type) = metadata
        .content_type
        .as_ref()
        .filter(|value| !value.is_empty())
    {
        attributes.push((
            "data-original-content-type".to_owned(),
            content_type.clone(),
        ));
    }
    Ok(ImageOutcome {
        image: Some(Image {
            end: parsed.end,
            attributes,
        }),
        attachment_path,
    })
}

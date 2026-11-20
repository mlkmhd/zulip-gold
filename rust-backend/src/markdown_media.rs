//! Prepare uploaded Markdown images and their pending rendering metadata.
//!
//! This is independent of Python and does not mutate a tree or rendering result.
//! Callers must discard the whole plan on `Unsupported`, and commit attachment
//! effects even for a rejected image candidate when the plan succeeds.

use std::collections::HashMap;

use crate::draft_text::is_python_whitespace;
use crate::markdown_link_syntax::{TextStash, parse_image, prepare_text, unescape};
use crate::markdown_links::{Policy, Unsupported, rewrite_local, sanitize};

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

//! Prepare explicit Markdown links without mutating the inline arena.
//!
//! URL decomposition/recomposition follows CPython 3.12 urllib.parse, adapted
//! under PSF-2.0; see docs/THIRDPARTY for its copyright and license notice.
//!
//! Authority validation involving Unicode normalization or bracketed IP hosts
//! is delegated to the reference renderer. A caller must discard the whole
//! native plan on `Unsupported`, rather than rendering an unchecked URL.

use crate::draft_text::is_python_whitespace;
use crate::markdown_link_syntax::{TextStash, parse, prepare_text, unescape};

/// Live renderer settings, including urllib's schemes with authority syntax.
#[derive(Clone)]
pub struct Policy {
    pub allowed_schemes: Vec<String>,
    pub netloc_schemes: Vec<String>,
    pub enable_file_links: bool,
    pub realm_url: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Unsupported;

pub(crate) struct Parts<'a> {
    pub(crate) scheme: String,
    pub(crate) netloc: &'a str,
    pub(crate) path: &'a str,
    pub(crate) query: &'a str,
    pub(crate) fragment: &'a str,
}

pub(crate) fn split(url: &str) -> Result<Parts<'_>, Unsupported> {
    let mut path = url;
    let mut scheme = String::new();
    if let Some(colon) = url.find(':') {
        let candidate = &url[..colon];
        if candidate
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphabetic)
            && candidate
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"+-.".contains(&c))
        {
            scheme = candidate.to_ascii_lowercase();
            path = &url[colon + 1..];
        }
    }
    let mut netloc = "";
    if let Some(authority) = path.strip_prefix("//") {
        let end = authority.find(['/', '?', '#']).unwrap_or(authority.len());
        netloc = &authority[..end];
        path = &authority[end..];
        if !netloc.is_ascii() || netloc.contains(['[', ']']) {
            return Err(Unsupported);
        }
    }
    let (path, fragment) = path.split_once('#').unwrap_or((path, ""));
    let (path, query) = path.split_once('?').unwrap_or((path, ""));
    Ok(Parts {
        scheme,
        netloc,
        path,
        query,
        fragment,
    })
}

fn unsplit(parts: &Parts<'_>, policy: &Policy) -> String {
    let mut result = String::new();
    if !parts.scheme.is_empty() {
        result.push_str(&parts.scheme);
        result.push(':');
    }
    if !parts.netloc.is_empty()
        || (!parts.scheme.is_empty()
            && policy.netloc_schemes.contains(&parts.scheme)
            && !parts.path.starts_with("//"))
    {
        result.push_str("//");
        result.push_str(parts.netloc);
        if !parts.path.is_empty() && !parts.path.starts_with('/') {
            result.push('/');
        }
    }
    result.push_str(parts.path);
    if !parts.query.is_empty() {
        result.push('?');
        result.push_str(parts.query);
    }
    if !parts.fragment.is_empty() {
        result.push('#');
        result.push_str(parts.fragment);
    }
    result
}

/// Match Zulip's sanitizer on supported authorities.
///
/// `Ok(None)` rejects a scheme. `Err(Unsupported)` requires reference handling,
/// including its distinction between malformed URLs (`Some("")`) and rejected
/// schemes. Unicode is supported in paths, queries, fragments and mail addresses.
pub fn sanitize(url: &str, policy: &Policy) -> Result<Option<String>, Unsupported> {
    let clean = |url: &str| {
        url.replace(' ', "%20")
            .trim_start_matches(|c| c <= '\u{20}')
            .replace(['\t', '\r', '\n'], "")
    };
    let cleaned = clean(url);
    let mut parts = split(&cleaned)?;
    if parts.scheme.is_empty() && parts.netloc.is_empty() && parts.path.contains('@') {
        parts.scheme = "mailto".to_owned();
    } else if parts.scheme.is_empty()
        && parts.netloc.is_empty()
        && (parts.path.starts_with('/')
            || (parts.path.is_empty() && parts.query.is_empty() && !parts.fragment.is_empty()))
    {
        return Ok(Some(unsplit(&parts, policy)));
    }
    if parts.scheme.is_empty() {
        // The reference reparses the original input, including stripped controls.
        return sanitize(&format!("http://{url}"), policy);
    }
    if !policy.allowed_schemes.contains(&parts.scheme)
        || (parts.scheme == "file" && !policy.enable_file_links)
    {
        return Ok(None);
    }
    Ok(Some(unsplit(&parts, policy)))
}

/// Match `urlsplit(urljoin("/", url))` with CPython 3.12's standard scheme lists.
pub(crate) fn attachment_path(
    url: &str,
    realm_host: Option<&str>,
) -> Result<Option<String>, Unsupported> {
    let cleaned = url
        .trim_start_matches(|c| c <= '\u{20}')
        .replace(['\t', '\r', '\n'], "");
    let mut parts = split(&cleaned)?;
    let mut joined;
    if parts.scheme.is_empty() {
        // urlparse splits parameters only from the final path segment.
        let segment_start = parts.path.rfind('/').map_or(0, |at| at + 1);
        let (path, params) = parts.path[segment_start..]
            .find(';')
            .map(|at| parts.path.split_at(segment_start + at))
            .map_or((parts.path, ""), |(path, params)| (path, &params[1..]));
        joined = String::new();
        if !parts.netloc.is_empty() {
            joined.push_str("//");
            joined.push_str(parts.netloc);
            joined.push_str(path);
        } else {
            let mut segments: Vec<_> = path.split('/').collect();
            if !path.starts_with('/') {
                // The base directory is '/'; relative paths discard empty
                // intermediate segments, but absolute paths preserve them.
                let last = segments.pop().unwrap();
                segments.retain(|segment| !segment.is_empty());
                segments.insert(0, "");
                segments.push(last);
            }
            let trailing_directory = matches!(segments.last(), Some(&"." | &".."));
            let mut resolved = Vec::new();
            for segment in segments {
                match segment {
                    ".." => {
                        resolved.pop();
                    }
                    "." => {}
                    _ => resolved.push(segment),
                }
            }
            if trailing_directory {
                resolved.push("");
            }
            joined.push_str(&resolved.join("/"));
            if joined.is_empty() {
                joined.push('/');
            }
        }
        if !params.is_empty() {
            joined.push(';');
            joined.push_str(params);
        }
        // Re-parsing can interpret a normalized leading '//' as an authority.
        joined = joined
            .trim_start_matches(|c| c <= '\u{20}')
            .replace(['\t', '\r', '\n'], "");
        parts = split(&joined)?;
    }
    if !parts.netloc.is_empty() && Some(parts.netloc) != realm_host {
        return Ok(None);
    }
    Ok(parts.path.strip_prefix("/user_uploads/").map(str::to_owned))
}

pub(crate) fn rewrite_local(mut url: String, policy: &Policy) -> String {
    if let Some(realm_url) = &policy.realm_url {
        let prefix = format!("{realm_url}/");
        if let Some(relative) = url.strip_prefix(&prefix)
            && (relative.starts_with('#') || relative.starts_with("user_uploads/"))
        {
            url = relative.to_owned();
        }
    }
    url
}

#[derive(Debug, PartialEq, Eq)]
pub struct Link {
    pub end: usize,
    pub href: String,
    pub title: Option<String>,
    pub text: String,
    pub reply: bool,
    pub reply_parts: Option<(String, String)>,
}

/// Prepare one link at a UTF-8 byte offset, retaining atomic label semantics.
///
/// `None` covers both syntax misses and sanitizer rejection. Neither case may
/// consume input; the caller must keep searching for later link openers.
pub fn prepare(
    data: &str,
    start: usize,
    stash: Option<&TextStash>,
    policy: &Policy,
) -> Result<Option<Link>, Unsupported> {
    let Some(parsed) = parse(data, start).map_err(|_| Unsupported)? else {
        return Ok(None);
    };
    let prepared = prepare_text(data, &parsed, stash).ok_or(Unsupported)?;
    let restored = unescape(prepared.href.trim_matches(is_python_whitespace), stash);
    let Some(href) = sanitize(&restored, policy)? else {
        return Ok(None);
    };
    let href = rewrite_local(href, policy);
    let mut title = prepared.title;
    let mut text = data[parsed.text].to_owned();
    let reply = title.as_deref() == Some("zulip-message-reply")
        && (href.starts_with("#narrow/channel/") || href.starts_with("#narrow/dm/"))
        && href
            .rsplit_once("/near/")
            .is_some_and(|(_, id)| !id.is_empty() && id.bytes().all(|b| b.is_ascii_digit()));
    let mut reply_parts = None;
    if reply {
        title = None;
        let separator = if text.contains(" | ") {
            " | "
        } else {
            " &#183; "
        };
        if let Some((sender, excerpt)) = text.split_once(separator)
            && !sender.is_empty()
            && !excerpt.is_empty()
        {
            reply_parts = Some((sender.to_owned(), excerpt.to_owned()));
            text.clear();
        }
    }
    if reply_parts.is_none() && text.chars().all(is_python_whitespace) {
        text.clone_from(&href);
    }
    Ok(Some(Link {
        end: parsed.end,
        href,
        title,
        text,
        reply,
        reply_parts,
    }))
}

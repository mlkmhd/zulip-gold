//! Pure URL decisions for the external image and video preview processor.

use std::borrow::Cow;
use std::sync::LazyLock;

use regex::Regex;

use crate::markdown_links::{Parts, Unsupported, split};

fn clean(url: &str) -> Cow<'_, str> {
    let url = url.trim_start_matches(|c| c <= '\u{20}');
    if url.contains(['\t', '\r', '\n']) {
        Cow::Owned(url.replace(['\t', '\r', '\n'], ""))
    } else {
        Cow::Borrowed(url)
    }
}

fn hex(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    }
}

/// The reference replaces invalid UTF-8 instead of rejecting it. Leave those
/// inputs to Python rather than depending on the libraries' replacement rules.
fn decode_form(value: &str) -> Result<Cow<'_, str>, Unsupported> {
    if !value.contains(['%', '+']) {
        return Ok(Cow::Borrowed(value));
    }
    let mut decoded = Vec::with_capacity(value.len());
    let mut rest = value.as_bytes();
    while let Some((&first, tail)) = rest.split_first() {
        if let [b'%', high, low, next @ ..] = rest
            && let (Some(high), Some(low)) = (hex(*high), hex(*low))
        {
            decoded.push(high * 16 + low);
            rest = next;
            continue;
        }
        decoded.push(if first == b'+' { b' ' } else { first });
        rest = tail;
    }
    String::from_utf8(decoded)
        .map(Cow::Owned)
        .map_err(|_| Unsupported)
}

fn form_pairs(query: &str) -> impl Iterator<Item = (&str, &str)> {
    query
        .split('&')
        .filter_map(|field| field.split_once('='))
        .filter(|(_, value)| !value.is_empty())
}

pub fn youtube_id(url: &str) -> Result<Option<String>, Unsupported> {
    let cleaned = clean(url);
    let url = split(&cleaned)?;
    if !matches!(url.scheme.as_str(), "http" | "https") {
        return Ok(None);
    }
    let hostname = url
        .netloc
        .rsplit('@')
        .next()
        .unwrap()
        .split(':')
        .next()
        .unwrap()
        .to_ascii_lowercase();
    let id = match hostname.as_str() {
        "youtu.be" => url.path.strip_prefix('/').map(Cow::Borrowed),
        "m.youtube.com"
        | "www.youtube.com"
        | "www.youtube-nocookie.com"
        | "youtube.com"
        | "youtube-nocookie.com" => match url.path {
            "/watch" | "/watch_popup" | "/watch_videos" => {
                let playlist = url.path == "/watch_videos";
                let key = if playlist { "video_ids" } else { "v" };
                let mut id = None;
                for (name, value) in form_pairs(url.query) {
                    if decode_form(name)? == key {
                        let value = decode_form(value)?;
                        id = Some(if playlist {
                            match value.split_once(',') {
                                Some((first, _)) => Cow::Owned(first.to_owned()),
                                None => value,
                            }
                        } else {
                            value
                        });
                        break;
                    }
                }
                id
            }
            path => ["/embed/", "/shorts/", "/v/"]
                .iter()
                .find_map(|prefix| path.strip_prefix(prefix))
                .map(|path| Cow::Borrowed(path.split('/').next().unwrap())),
        },
        _ => None,
    };
    Ok(id
        .filter(|id| {
            !id.is_empty()
                && id
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
        })
        .map(Cow::into_owned))
}

static VIMEO: LazyLock<Regex> = LazyLock::new(|| {
    // The historical pattern does not escape the dot in vimeo.com.
    Regex::new(
        r"\A(?:https?)?://(?:www\.)?vimeo.com/(?:channels/(?:[0-9A-Za-z_]+/)?|groups/[^/]*/videos/|)([0-9]+)(?:/\?)?\z",
    )
    .unwrap()
});

pub fn vimeo_id(url: &str) -> Result<Option<String>, Unsupported> {
    if !url.is_ascii() {
        // Python's Unicode \w and \d are not the Rust regex character classes.
        return Err(Unsupported);
    }
    let url = url.strip_suffix('\n').unwrap_or(url);
    Ok(VIMEO.captures(url).map(|captures| captures[1].to_owned()))
}

/// Return only rewrites; the caller keeps the original object when unmodified.
pub fn github_image_url(url: &str) -> Result<Option<String>, Unsupported> {
    let cleaned = clean(url);
    let url = split(&cleaned)?;
    if url.netloc != "github.com" && !url.netloc.ends_with(".github.com") {
        return Ok(None);
    }
    let mut segments: Vec<_> = url.path.split('/').collect();
    if segments.get(3) != Some(&"blob") {
        return Ok(None);
    }
    // The Python path uses urljoin. Leave noncanonical paths to it, including
    // cases where urljoin would not concatenate the given authority and path.
    if url.path.contains("//")
        || url.path.contains(';')
        || segments
            .iter()
            .any(|segment| matches!(*segment, "." | ".."))
    {
        return Err(Unsupported);
    }
    segments.remove(3);
    Ok(Some(format!(
        "https://raw.githubusercontent.com{}",
        segments.join("/")
    )))
}

fn recompose_authority(parts: &Parts<'_>, path: &str, query: &str) -> String {
    let mut out = String::new();
    if !parts.scheme.is_empty() {
        out.push_str(&parts.scheme);
        out.push(':');
    }
    out.push_str("//");
    out.push_str(parts.netloc);
    out.push_str(path);
    if !query.is_empty() {
        out.push('?');
        out.push_str(query);
    }
    if !parts.fragment.is_empty() {
        out.push('#');
        out.push_str(parts.fragment);
    }
    out
}

pub fn corrected_image_source(url: &str) -> Result<Option<String>, Unsupported> {
    let cleaned = clean(url);
    let url = split(&cleaned)?;
    if url.netloc == "linx.li" {
        return Ok(Some(format!("https://linx.li/s{}", url.path)));
    }
    if url.netloc.to_ascii_lowercase().ends_with(".wikipedia.org")
        && let Some(filename) = url.path.strip_prefix("/wiki/File:")
    {
        return Ok(Some(recompose_authority(
            &url,
            &format!("/wiki/Special:FilePath/File:{filename}"),
            url.query,
        )));
    }
    Ok(None)
}

fn encode_form(out: &mut String, text: &str) {
    let digits = b"0123456789ABCDEF";
    for b in text.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(char::from(b));
            }
            b' ' => out.push('+'),
            _ => {
                out.push('%');
                out.push(char::from(digits[usize::from(b / 16)]));
                out.push(char::from(digits[usize::from(b % 16)]));
            }
        }
    }
}

fn dropbox_query(query: &str) -> Result<String, Unsupported> {
    use std::collections::HashMap;

    let mut params = Vec::new();
    let mut positions = HashMap::new();
    let mut insert = |name: Cow<'_, str>, value| {
        // Own keys in the position index so duplicate names overwrite their
        // values without changing the first occurrence's insertion order.
        if let Some(&position) = positions.get(name.as_ref()) {
            params[position] = (name.into_owned(), value);
        } else {
            positions.insert(name.to_string(), params.len());
            params.push((name.into_owned(), value));
        }
    };
    for (name, value) in form_pairs(query) {
        insert(decode_form(name)?, decode_form(value)?);
    }
    insert(Cow::Borrowed("raw"), Cow::Borrowed("1"));

    let mut out = String::new();
    for (name, value) in params {
        if !out.is_empty() {
            out.push('&');
        }
        encode_form(&mut out, &name);
        out.push('=');
        encode_form(&mut out, &value);
    }
    Ok(out)
}

pub struct DropboxPlan {
    pub is_album: bool,
    pub path: String,
    pub raw_url: Option<String>,
}

pub fn dropbox_plan(url: &str) -> Result<Option<DropboxPlan>, Unsupported> {
    let cleaned = clean(url);
    let url = split(&cleaned)?;
    if url.netloc != "dropbox.com" && !url.netloc.ends_with(".dropbox.com") {
        return Ok(None);
    }
    let is_album = url.path.starts_with("/scl/fo/") || url.path.starts_with("/sc/");
    if !is_album && !url.path.starts_with("/scl/fi/") {
        return Ok(None);
    }
    Ok(Some(DropboxPlan {
        is_album,
        path: url.path.to_owned(),
        raw_url: if is_album {
            None
        } else {
            Some(recompose_authority(
                &url,
                url.path,
                &dropbox_query(url.query)?,
            ))
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_youtube_ids_with_query_and_host_semantics() {
        assert_eq!(
            youtube_id(" HTTPS://WWW.YouTube.COM:443/watch?v=&%76=Fi_rst%2D1&v=ignored").unwrap(),
            Some("Fi_rst-1".into())
        );
        assert_eq!(
            youtube_id("https://www.youtube.com/watch_videos?video_ids=first%2Csecond").unwrap(),
            Some("first".into())
        );
        assert_eq!(youtube_id("https://youtu.be/foo/bar").unwrap(), None);
        assert_eq!(
            youtube_id("https://youtube.com/watch?v=%FF"),
            Err(Unsupported)
        );
        assert_eq!(
            youtube_id("https://youtube.com/shorts/test/extra").unwrap(),
            Some("test".into())
        );
    }

    #[test]
    fn preserves_vimeo_pattern_and_uses_reference_unicode() {
        assert_eq!(
            vimeo_id("https://vimeo.com/channels/my_channel/0355/?\n").unwrap(),
            Some("0355".into())
        );
        assert_eq!(vimeo_id("https://vimeo.com/123?foo=bar").unwrap(), None);
        assert_eq!(vimeo_id("https://vimeo.com/۱۲۳"), Err(Unsupported));
    }

    #[test]
    fn rewrites_public_image_sources_without_reparsing_unsupported_paths() {
        assert_eq!(
            github_image_url("https://github.com/owner/repo/blob/main/pic.png?raw=1#hash").unwrap(),
            Some("https://raw.githubusercontent.com/owner/repo/main/pic.png".into())
        );
        assert_eq!(
            github_image_url("https://github.com/owner/repo/blob/main/../pic.png"),
            Err(Unsupported)
        );
        assert_eq!(
            corrected_image_source("HTTPS://FA.WIKIPEDIA.ORG/wiki/File:سلام.png?a=1#cite").unwrap(),
            Some("https://FA.WIKIPEDIA.ORG/wiki/Special:FilePath/File:سلام.png?a=1#cite".into())
        );
        assert_eq!(
            corrected_image_source("https://linx.li/item.png?x=1#ignored").unwrap(),
            Some("https://linx.li/s/item.png".into())
        );
    }

    #[test]
    fn plans_dropbox_deferred_and_inline_forms() {
        let file = dropbox_plan(
            "https://www.dropbox.com/scl/fi/abc/image.png?raw=0&key=one&blank=&key=%E2%98%83+test#top",
        )
        .unwrap()
        .unwrap();
        assert!(!file.is_album);
        assert_eq!(file.path, "/scl/fi/abc/image.png");
        assert_eq!(
            file.raw_url.as_deref(),
            Some("https://www.dropbox.com/scl/fi/abc/image.png?raw=1&key=%E2%98%83+test#top")
        );
        let album = dropbox_plan("https://dropbox.com/scl/fo/abc/part")
            .unwrap()
            .unwrap();
        assert!(album.is_album);
        assert_eq!(album.raw_url, None);
        assert!(
            dropbox_plan("https://dropbox.com/static/photo.png")
                .unwrap()
                .is_none()
        );
    }
}

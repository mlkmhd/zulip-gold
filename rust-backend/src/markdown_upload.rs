pub(crate) fn local_upload_path<'url>(
    url: &'url str,
    realm_host: Option<&str>,
) -> Option<&'url str> {
    let path = if let Some(absolute) = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
    {
        let (host, path) = absolute.split_once('/')?;
        if Some(host) != realm_host
            || host.is_empty()
            || !host
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b':'))
        {
            return None;
        }
        path
    } else {
        url.strip_prefix('/').unwrap_or(url)
    };

    let path = path.split(['?', '#']).next()?;
    let path_id = path.strip_prefix("user_uploads/")?;

    // Relative URL normalization can remove empty or dot segments.
    if path_id.split('/').all(|segment| {
        !matches!(segment, "" | "." | "..")
            && segment.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b'~' | b'%')
            })
    }) {
        Some(path_id)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_path_ids_from_supported_local_links() {
        for url in [
            "https://zulip.example:443/user_uploads/1/a/%D9%87.txt#preview",
            "http://zulip.example:443/user_uploads/1/a/%D9%87.txt?download=1",
            "/user_uploads/1/a/%D9%87.txt",
            "user_uploads/1/a/%D9%87.txt",
        ] {
            assert_eq!(
                local_upload_path(url, Some("zulip.example:443")),
                Some("1/a/%D9%87.txt")
            );
        }
        assert_eq!(
            local_upload_path("user_uploads/1/a.txt", None),
            Some("1/a.txt")
        );
    }

    #[test]
    fn delegates_urls_with_other_hosts_or_path_normalization() {
        for url in [
            "https://other.example/user_uploads/1/a.txt",
            "https://zulip.example.evil/user_uploads/1/a.txt",
            "//zulip.example/user_uploads/1/a.txt",
            "user_uploads/1//a.txt",
            "/user_uploads/1/../a.txt",
            "/user_uploads/1/./a.txt",
            "/user_uploads/1/foo\tbar.txt",
            "/user_uploads/1/سلام.txt",
            "/user_uploads/",
        ] {
            assert_eq!(local_upload_path(url, Some("zulip.example")), None, "{url}");
        }
    }
}

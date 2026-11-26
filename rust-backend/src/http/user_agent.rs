use axum::http::HeaderValue;

pub(super) use crate::user_agent::parse;

pub(super) fn decode(value: &HeaderValue) -> String {
    // WSGI exposes HTTP header bytes as ISO-8859-1 strings.
    value.as_bytes().iter().copied().map(char::from).collect()
}

//! Transport already-authorized media snapshots across the Python bridge.
//!
//! The host must guard its live helpers and settings before using these inputs.
//! This module performs no lookups and never grants attachment access.

use std::collections::{HashMap, HashSet};

use crate::markdown_media::{AudioPolicy, ImageMetadata, MimePolicy};
use crate::markdown_native_inline::Media;

type MimeInputs = (
    HashMap<String, String>,
    HashSet<String>,
    HashMap<String, String>,
    HashSet<String>,
    HashSet<String>,
);
type AudioInputs = (
    MimeInputs,
    String,
    String,
    Option<String>,
    String,
    Option<String>,
);
type ImageInputs = HashMap<String, (String, String, Option<String>)>;
pub(crate) type MediaInputs = (
    Option<String>,
    Option<HashSet<String>>,
    Option<ImageInputs>,
    AudioInputs,
);

pub(crate) fn into_media(
    (realm_host, audio_previews, image_previews, audio): MediaInputs,
) -> Media {
    let (mime, static_url, external_uri_scheme, public_upload_root, camo_uri, camo_key) = audio;
    let (suffix_map, encodings, types, supported_audio_types, param_schemes) = mime;
    Media {
        realm_host,
        audio_previews,
        image_previews: image_previews.map(|previews| {
            previews
                .into_iter()
                .map(|(path, (width, height, content_type))| {
                    (
                        path,
                        ImageMetadata {
                            width,
                            height,
                            content_type,
                        },
                    )
                })
                .collect()
        }),
        audio_policy: AudioPolicy {
            mime: MimePolicy {
                suffix_map,
                encodings,
                types,
                supported_audio_types,
                param_schemes,
            },
            static_url,
            external_uri_scheme,
            public_upload_root,
            camo_uri,
            camo_key,
        },
    }
}

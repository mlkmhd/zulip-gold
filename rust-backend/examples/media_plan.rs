//! Fixed-policy graph and metadata protocol for native media differential tests.

use std::collections::{HashMap, HashSet};
use std::io::{self, BufRead};

use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_media::{AudioPolicy, ImageMetadata, MimePolicy};
use _zulip_backend::markdown_native_inline::{Media, Node, Pattern, Stashed, Text, run_with_media};

fn decode(text: &str) -> String {
    String::from_utf8(
        text.as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect(),
    )
    .unwrap()
}

fn encode(text: &str) -> String {
    text.bytes().map(|byte| format!("{byte:02x}")).collect()
}

fn text(value: Option<&Text>) -> String {
    value.map_or("-".into(), |text| {
        format!("{}{}", u8::from(text.atomic), encode(&text.value))
    })
}

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        let data = decode(fields[0]);
        let context: u8 = fields[1].parse().unwrap();
        let policy = Policy {
            allowed_schemes: ["http", "https", "file", "mailto"]
                .map(str::to_owned)
                .into(),
            netloc_schemes: ["http", "https", "file"].map(str::to_owned).into(),
            enable_file_links: false,
            realm_url: (context != 0).then(|| "https://chat.example".into()),
        };
        let media = Media {
            realm_host: (context != 0).then(|| "chat.example".into()),
            audio_previews: (context != 0).then(|| {
                if context == 1 {
                    HashSet::new()
                } else {
                    ["1/voice", "1/both", "1/sound.mp3?x#y"]
                        .map(str::to_owned)
                        .into()
                }
            }),
            image_previews: (context != 0).then(|| {
                if context == 1 {
                    HashMap::new()
                } else {
                    ["1/picture", "1/both"]
                        .into_iter()
                        .map(|id| {
                            (
                                id.into(),
                                ImageMetadata {
                                    width: "10".into(),
                                    height: "20".into(),
                                    content_type: Some("image/png".into()),
                                },
                            )
                        })
                        .collect()
                }
            }),
            audio_policy: AudioPolicy {
                mime: MimePolicy {
                    types: HashMap::from([
                        (".mp3".into(), "audio/mpeg".into()),
                        (".png".into(), "image/png".into()),
                    ]),
                    encodings: [".gz".into()].into(),
                    supported_audio_types: ["audio/mpeg".into()].into(),
                    param_schemes: ["", "http", "https"].map(str::to_owned).into(),
                    ..MimePolicy::default()
                },
                static_url: "/static/".into(),
                external_uri_scheme: "https://".into(),
                public_upload_root: None,
                camo_uri: "https://camo.example/".into(),
                camo_key: Some("secret".into()),
            },
        };
        let nodes = vec![
            Node {
                tag: "div".into(),
                lower_tag: "div".into(),
                text: None,
                tail: None,
                children: vec![1],
                attributes: vec![],
            },
            Node {
                tag: "p".into(),
                lower_tag: "p".into(),
                text: Some(Text {
                    value: data,
                    atomic: fields[3] == "1",
                }),
                tail: None,
                children: vec![],
                attributes: vec![],
            },
        ];
        let kinds: &[u8] = if fields[2] == "reverse" {
            &[11, 13, 12]
        } else {
            &[11, 12, 13]
        };
        let patterns: Vec<_> = kinds
            .iter()
            .map(|&kind| Pattern {
                kind,
                excludes: vec![],
                emoji: None,
                emoticons: None,
                unicode_emoji: None,
                links: Some(policy.clone()),
            })
            .collect();
        let Some(result) = run_with_media(
            nodes,
            &patterns,
            vec!["outer".into()],
            Some(&media),
            |_, _| Some(()),
        ) else {
            println!("delegate");
            continue;
        };
        let attachments = result
            .attachment_paths
            .iter()
            .map(|path| format!("p{}", encode(path)))
            .collect::<Vec<_>>()
            .join(",");
        let ancestors = result
            .ancestors
            .iter()
            .map(|tag| encode(tag))
            .collect::<Vec<_>>()
            .join(",");
        let parents = result
            .parent_order
            .iter()
            .map(|child| format!("{child}:{}", result.parents[*child].unwrap()))
            .collect::<Vec<_>>()
            .join(",");
        let stash = result
            .stash
            .iter()
            .map(|value| match value {
                Stashed::Node(node) => format!("n{node}"),
                Stashed::Text(value) => format!("t{}", text(Some(value))),
            })
            .collect::<Vec<_>>()
            .join(",");
        print!(
            "plan\t{}\t{attachments}\t{ancestors}\t{parents}\t{stash}",
            u8::from(result.has_image)
        );
        for node in &result.nodes {
            let children = node
                .children
                .iter()
                .map(usize::to_string)
                .collect::<Vec<_>>()
                .join(":");
            let attributes = node
                .attributes
                .iter()
                .map(|(key, value)| format!("{}:{}", encode(key), encode(value)))
                .collect::<Vec<_>>()
                .join(";");
            print!(
                "\t{},{},{},{},{children},{attributes}",
                encode(&node.tag),
                encode(&node.lower_tag),
                text(node.text.as_ref()),
                text(node.tail.as_ref())
            );
        }
        println!();
    }
}

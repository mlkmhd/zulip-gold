//! Hex UTF-8 syntax and field probes; the first line snapshots Unicode digits.

use std::collections::{HashMap, HashSet};
use std::io::{self, BufRead};

use _zulip_backend::markdown_channel_spans::channel_link;
use _zulip_backend::markdown_channel_syntax::{Kind, find};
use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_native_inline::{
    Channel, Channels, Inputs, Node, Pattern, Stashed, Text, run_with_inputs,
};

fn decode(value: &str) -> String {
    String::from_utf8(
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect(),
    )
    .unwrap()
}

fn encode(value: &str) -> String {
    value.bytes().map(|b| format!("{b:02x}")).collect()
}

fn optional(value: &str) -> Option<String> {
    (value != "-").then(|| decode(value))
}

fn plan(fields: &[&str], digits: &HashSet<char>) {
    let context: u8 = fields[2].parse().unwrap();
    let channels = Channels {
        names: if context == 0 {
            HashMap::new()
        } else {
            [
                ("dev tools", 42, false),
                ("empty", 17, true),
                ("dev tools>t@1", 99, false),
                ("<نام &>", 7, false),
                ("empty>no", 123, false),
            ]
            .into_iter()
            .map(|(name, id, empty_topic_only)| {
                (
                    name.to_owned(),
                    Channel {
                        id,
                        empty_topic_only,
                    },
                )
            })
            .collect()
        },
        topic_with: [
            (("dev tools".into(), "t".into()), 123),
            (("empty".into(), "".into()), 77),
        ]
        .into(),
        empty_topic_name: "<empty & **نام**>".into(),
        unicode_decimal: digits.clone(),
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
                value: decode(fields[1]),
                atomic: fields[4] == "1",
            }),
            tail: None,
            children: vec![],
            attributes: vec![],
        },
    ];
    let kinds = if fields[3] == "reverse" {
        [1, 14, 15, 16, 11, 3, 4, 7]
    } else {
        [1, 16, 15, 14, 11, 3, 4, 7]
    };
    let patterns: Vec<_> = kinds
        .into_iter()
        .map(|kind| Pattern {
            kind,
            excludes: if fields[5] == "1" && (14..=16).contains(&kind) {
                vec!["code".into()]
            } else {
                vec![]
            },
            emoji: None,
            emoticons: None,
            unicode_emoji: None,
            links: (kind == 11).then(|| Policy {
                allowed_schemes: ["http", "https", "mailto"].map(str::to_owned).into(),
                netloc_schemes: ["http", "https"].map(str::to_owned).into(),
                enable_file_links: false,
                realm_url: None,
            }),
        })
        .collect();
    let Some(result) = run_with_inputs(
        nodes,
        &patterns,
        vec![if fields[5] == "1" {
            "code".into()
        } else {
            "outer".into()
        }],
        Inputs {
            channels: Some(&channels),
            ..Inputs::default()
        },
        |_, _| Some(()),
    ) else {
        println!("delegate");
        return;
    };
    let text = |value: Option<&Text>| {
        value.map_or("-".into(), |value| {
            format!("{}{}", u8::from(value.atomic), encode(&value.value))
        })
    };
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
    assert!(result.attachment_paths.is_empty() && !result.has_image);
    print!("plan\t0\t\t{ancestors}\t{parents}\t{stash}");
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

fn main() {
    let mut digits = HashSet::new();
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        if fields[0] == "digits" {
            digits = decode(fields[1]).chars().collect();
            continue;
        }
        if fields[0] == "plan" {
            plan(&fields, &digits);
            continue;
        }
        if fields[0] == "fields" {
            let channel = decode(fields[2]);
            let topic = optional(fields[3]);
            let message = optional(fields[5]);
            let link = channel_link(
                fields[1].parse().unwrap(),
                &channel,
                topic.as_deref(),
                (fields[4] != "-").then(|| fields[4].parse().unwrap()),
                message.as_deref(),
            );
            let mut output = vec!["fields".to_owned(), link.attributes.len().to_string()];
            for (name, value) in link.attributes {
                output.extend([encode(name), encode(&value)]);
            }
            output.extend([
                encode(&link.text),
                u8::from(link.empty_topic).to_string(),
                link.tail.map(encode).unwrap_or_else(|| "-".to_owned()),
            ]);
            println!("{}", output.join("\t"));
            continue;
        }
        let kind = match fields[0] {
            "channel" => Kind::Channel,
            "topic" => Kind::Topic,
            "message" => Kind::Message,
            _ => panic!("unknown kind"),
        };
        let data = decode(fields[2]);
        match find(&data, fields[1].parse().unwrap(), kind, &digits) {
            Err(_) => println!("invalid"),
            Ok(None) => println!("miss"),
            Ok(Some(candidate)) => println!(
                "match\t{}\t{}\t{}\t{}\t{}",
                candidate.range.start,
                candidate.range.end,
                encode(&data[candidate.channel]),
                candidate
                    .topic
                    .map(|r| encode(&data[r]))
                    .unwrap_or_else(|| "-".to_owned()),
                candidate
                    .message
                    .map(|r| encode(&data[r]))
                    .unwrap_or_else(|| "-".to_owned()),
            ),
        }
    }
}

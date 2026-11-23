//! Fixed authorized snapshots for mention graph and notification differential tests.

use std::collections::HashMap;
use std::io::{self, BufRead};

use _zulip_backend::markdown_inline_mentions::UserPolicy;
use _zulip_backend::markdown_native_inline::{
    Inputs, MentionData, MentionGroupSnapshot, MentionUserSnapshot, Mentions, Node, Pattern,
    Stashed, Text, run_with_inputs,
};

fn decode(value: &str) -> String {
    String::from_utf8(
        value
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect(),
    )
    .unwrap()
}

fn encode(value: &str) -> String {
    value.bytes().map(|b| format!("{b:02x}")).collect()
}

fn plan(fields: &[&str], digits: &HashMap<char, u8>) {
    let context: u8 = fields[2].parse().unwrap();
    let user = |id, name: &str, active| MentionUserSnapshot {
        id,
        full_name: name.into(),
        is_active: active,
    };
    let mut mentions = Mentions {
        data: (context != 0).then(|| MentionData {
            users_by_name: if context == 1 {
                HashMap::new()
            } else {
                [
                    ("Alice", 42, "Alice"),
                    ("alice", 42, "Alice"),
                    ("ALICE", 42, "Alice"),
                    ("نام", 17, "نام <&**Bold**>"),
                    ("İΣ", 7, "İΣ"),
                ]
                .into_iter()
                .map(|(name, id, full)| (name.into(), Some(user(id, full, context != 3))))
                .collect()
            },
            users_by_id: if context == 1 {
                HashMap::new()
            } else {
                [(42, "Alice"), (17, "نام <&**Bold**>"), (7, "İΣ")]
                    .into_iter()
                    .map(|(id, full)| (id, user(id, full, context != 3)))
                    .collect()
            },
            groups_by_name: if context == 1 {
                HashMap::new()
            } else {
                [
                    ("group", 15, "Group <&**Bold**>", false),
                    ("GROUP", 15, "Group <&**Bold**>", false),
                    ("old", 99, "Old", true),
                    ("نام", 17, "گروه", false),
                ]
                .into_iter()
                .map(|(name, id, display, deactivated)| {
                    (
                        name.into(),
                        Some(MentionGroupSnapshot {
                            id,
                            display_name: display.into(),
                            deactivated: deactivated || context == 3,
                        }),
                    )
                })
                .collect()
            },
        }),
        user_policy: UserPolicy {
            unicode_decimal: digits.clone(),
            max_id_digits: fields[6].parse().unwrap(),
        },
    };
    if let Some(data) = mentions.data.as_mut() {
        for name in fields[7].split(',').filter(|name| !name.is_empty()) {
            data.users_by_name.entry(decode(name)).or_insert(None);
        }
        for name in fields[8].split(',').filter(|name| !name.is_empty()) {
            data.groups_by_name.entry(decode(name)).or_insert(None);
        }
    }
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
        [18, 17, 1, 2, 3, 4, 7]
    } else {
        [1, 2, 17, 18, 3, 4, 7]
    };
    let patterns: Vec<_> = kinds
        .into_iter()
        .map(|kind| Pattern {
            kind,
            excludes: if fields[5] == "1" && matches!(kind, 17 | 18) {
                vec!["code".into()]
            } else {
                vec![]
            },
            emoji: None,
            emoticons: None,
            unicode_emoji: None,
            links: None,
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
            mentions: Some(&mentions),
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
    let mut users: Vec<_> = result.mentions.users.into_iter().collect();
    let mut groups: Vec<_> = result.mentions.groups.into_iter().collect();
    users.sort();
    groups.sort();
    println!(
        "\tnotifications\t{}\t{}\t{}\t{}",
        users
            .iter()
            .map(i32::to_string)
            .collect::<Vec<_>>()
            .join(","),
        groups
            .iter()
            .map(i32::to_string)
            .collect::<Vec<_>>()
            .join(","),
        u8::from(result.mentions.channel),
        u8::from(result.mentions.topic)
    );
}

fn main() {
    let mut digits = HashMap::new();
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        if fields[0] == "digits" {
            digits = decode(fields[1])
                .chars()
                .zip(fields[2].bytes().map(|b| b - b'0'))
                .collect();
        } else {
            assert_eq!(fields[0], "plan");
            plan(&fields, &digits);
        }
    }
}

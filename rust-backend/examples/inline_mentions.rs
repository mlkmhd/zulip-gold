//! Hex UTF-8 protocol for mention lookup, display fields and notification effects.

use std::collections::HashMap;
use std::io::{self, BufRead};

use _zulip_backend::markdown_inline_mentions::{
    MentionNotification, MentionSpan, MentionUser, UserPolicy, UserReference, group_mention_span,
    prepare_user,
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
    value.bytes().map(|byte| format!("{byte:02x}")).collect()
}

fn output(span: Option<MentionSpan>) {
    let Some(span) = span else {
        println!("none");
        return;
    };
    let (kind, id) = match span.notification {
        Some(MentionNotification::User(id)) => ("user", id.to_string()),
        Some(MentionNotification::Group(id)) => ("group", id.to_string()),
        Some(MentionNotification::Channel) => ("stream", "-".to_owned()),
        Some(MentionNotification::Topic) => ("topic", "-".to_owned()),
        None => ("-", "-".to_owned()),
    };
    print!("span\t{}\t{kind}\t{id}", encode(&span.text));
    for (key, value) in span.attributes {
        print!("\t{}\t{}", encode(key), encode(&value));
    }
    println!();
}

fn main() {
    let mut decimal = HashMap::new();
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        let name = decode(fields[1]);
        if fields[0] == "digits" {
            decimal = name
                .chars()
                .zip(fields[2].bytes().map(|byte| byte - b'0'))
                .collect();
            continue;
        }
        let silent = fields[2] == "1";
        if fields[0] == "group" {
            output((fields[5] == "1").then(|| {
                group_mention_span(fields[4].parse().unwrap(), &name, silent, fields[3] == "1")
            }));
            continue;
        }
        let policy = UserPolicy {
            unicode_decimal: decimal.clone(),
            max_id_digits: fields[3].parse().unwrap(),
        };
        let users: Vec<_> = fields[6..]
            .chunks_exact(3)
            .map(|user| {
                (
                    user[0].parse::<i32>().unwrap(),
                    decode(user[1]),
                    user[2] == "1",
                )
            })
            .collect();
        match prepare_user(&name, silent, &policy, |reference| {
            let user = match reference {
                UserReference::Name(raw) => {
                    assert_eq!(raw, name);
                    fields[5].parse::<usize>().ok().map(|index| &users[index])
                }
                UserReference::Id(id) => users.iter().rfind(|user| user.0 == id),
            }?;
            Some(MentionUser {
                id: user.0,
                full_name: &user.1,
                is_active: user.2,
            })
        }) {
            Ok(span) => output(span),
            Err(_) => println!("delegate"),
        }
    }
}

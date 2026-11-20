//! Hex UTF-8 syntax and field probes; the first line snapshots Unicode digits.

use std::collections::HashSet;
use std::io::{self, BufRead};

use _zulip_backend::markdown_channel_spans::channel_link;
use _zulip_backend::markdown_channel_syntax::{Kind, find};

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

fn main() {
    let mut digits = HashSet::new();
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        if fields[0] == "digits" {
            digits = decode(fields[1]).chars().collect();
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

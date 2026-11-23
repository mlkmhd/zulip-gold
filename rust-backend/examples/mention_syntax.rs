//! Tab-separated kind, byte offset and hex UTF-8 text; one candidate per line.

use std::io::{self, BufRead};

use _zulip_backend::markdown_mention_syntax::{Kind, find};

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        let data = String::from_utf8(
            fields[2]
                .as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect(),
        )
        .unwrap();
        let kind = if fields[0] == "user" {
            Kind::User
        } else {
            Kind::Group
        };
        match find(&data, fields[1].parse().unwrap(), kind) {
            Err(_) => println!("invalid"),
            Ok(None) => println!("miss"),
            Ok(Some(candidate)) => println!(
                "match\t{}\t{}\t{}\t{}",
                candidate.range.start,
                candidate.range.end,
                data[candidate.name]
                    .bytes()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>(),
                u8::from(candidate.silent),
            ),
        }
    }
}

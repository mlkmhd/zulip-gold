//! Hex UTF-8 protocol for URL policy and explicit link comparisons.

use std::io::{self, BufRead};

use _zulip_backend::markdown_link_syntax::TextStash;
use _zulip_backend::markdown_links::{Policy, prepare, sanitize};

fn decode(text: &str) -> String {
    String::from_utf8(
        text.as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect(),
    )
    .unwrap()
}

fn encode(text: &str) -> String {
    text.bytes().map(|byte| format!("{byte:02x}")).collect()
}

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        let data = decode(fields[1]);
        let policy = Policy {
            allowed_schemes: fields[5].split(',').map(str::to_owned).collect(),
            netloc_schemes: fields[6].split(',').map(str::to_owned).collect(),
            enable_file_links: fields[3] == "1",
            realm_url: (fields[4] != "-").then(|| decode(fields[4])),
        };
        if fields[0] == "sanitize" {
            match sanitize(&data, &policy) {
                Ok(Some(url)) => println!("url\t{}", encode(&url)),
                Ok(None) => println!("-"),
                Err(_) => println!("delegate"),
            }
            continue;
        }
        let stash: TextStash = fields[8..]
            .chunks_exact(2)
            .map(|pair| (decode(pair[0]), decode(pair[1])))
            .collect();
        let stash = (fields[7] == "present").then_some(&stash);
        match prepare(&data, fields[2].parse().unwrap(), stash, &policy) {
            Ok(Some(link)) => println!(
                "link\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                link.end,
                encode(&link.href),
                link.title.as_deref().map_or("-".to_owned(), encode),
                encode(&link.text),
                u8::from(link.reply),
                link.reply_parts
                    .as_ref()
                    .map_or("-".to_owned(), |p| encode(&p.0)),
                link.reply_parts
                    .as_ref()
                    .map_or("-".to_owned(), |p| encode(&p.1)),
            ),
            Ok(None) => println!("-"),
            Err(_) => println!("delegate"),
        }
    }
}

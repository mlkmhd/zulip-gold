//! Tab-separated hex UTF-8 protocol for normalized link text comparisons.

use std::io::{self, BufRead};

use _zulip_backend::markdown_link_syntax::{
    ParseError, TextStash, parse, parse_image, prepare_text,
};

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

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        let data = decode(fields[2]);
        let start = fields[1].parse().unwrap();
        let stash: TextStash = fields[4..]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| (decode(pair[0]), decode(pair[1])))
            .collect();
        let stash = (fields[3] == "present").then_some(&stash);
        let parsed = match fields[0] {
            "link" => parse(&data, start),
            "image" => parse_image(&data, start),
            _ => panic!("unknown mode"),
        };
        match parsed {
            Ok(Some(link)) => {
                let text = prepare_text(&data, &link, stash).unwrap();
                println!(
                    "{}\t{}\t{}\t{}",
                    encode(&data[link.text]),
                    encode(&text.href),
                    text.title.as_deref().map_or("-".to_owned(), encode),
                    link.end
                );
            }
            Ok(None) => println!("-"),
            Err(ParseError::InvalidEnd) => println!("invalid-end"),
        }
    }
}

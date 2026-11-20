//! Line protocol for the differential: mode, byte offset, and hex UTF-8 input.

use std::io::{self, BufRead};

use _zulip_backend::markdown_link_syntax::{ParseError, parse, parse_image};

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let (mode, input) = line.split_once('\t').unwrap();
        let (start, encoded) = input.split_once('\t').unwrap();
        let bytes: Vec<u8> = encoded
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect();
        let data = String::from_utf8(bytes).unwrap();
        let start = start.parse().unwrap();
        let parsed = match mode {
            "link" => parse(&data, start),
            "image" => parse_image(&data, start),
            _ => panic!("unknown mode"),
        };
        match parsed {
            Ok(Some(link)) => {
                let (title_start, title_end) = link
                    .title
                    .map_or((-1, -1), |r| (r.start as isize, r.end as isize));
                println!(
                    "{} {} {} {} {title_start} {title_end} {}",
                    link.text.start, link.text.end, link.href.start, link.href.end, link.end
                );
            }
            Ok(None) => println!("-"),
            Err(ParseError::InvalidEnd) => println!("invalid-end"),
        }
    }
}

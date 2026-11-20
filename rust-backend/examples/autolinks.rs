//! Comma-separated TLDs and schemes, then file-links flag and hex UTF-8 per line.

use std::io::{self, BufRead};

use _zulip_backend::markdown_autolinks::{Matcher, Spec};

fn main() {
    let mut lines = io::stdin().lock().lines();
    let tlds: Vec<String> = lines
        .next()
        .unwrap()
        .unwrap()
        .split(',')
        .map(str::to_owned)
        .collect();
    let schemes: Vec<String> = lines
        .next()
        .unwrap()
        .unwrap()
        .split(',')
        .map(str::to_owned)
        .collect();
    let matchers = [false, true].map(|enable_file_links| {
        Matcher::new(&Spec {
            tlds: tlds.clone(),
            schemes: schemes.clone(),
            enable_file_links,
        })
        .unwrap()
    });
    for line in lines {
        let line = line.unwrap();
        let (files, encoded) = line.split_once('\t').unwrap();
        let data = String::from_utf8(
            encoded
                .as_bytes()
                .chunks_exact(2)
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect(),
        )
        .unwrap();
        let matcher = &matchers[usize::from(files == "1")];
        match matcher.find(&data, &mut (8 * 1024 * 1024)) {
            Some(Some(range)) => println!("{}\t{}", range.start, range.end),
            Some(None) => println!("miss"),
            None => println!("delegate"),
        }
    }
}

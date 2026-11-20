//! Hex UTF-8 protocol for image candidates and pending attachment effects.

use std::collections::HashMap;
use std::io::{self, BufRead};

use _zulip_backend::markdown_link_syntax::TextStash;
use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_media::{ImageMetadata, attachment_path, prepare_image};

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
        let host = (fields[5] != "-").then(|| decode(fields[5]));
        if fields[0] == "upload" {
            match attachment_path(&data, host.as_deref()) {
                Ok(Some(path)) => println!("path\t{}", encode(&path)),
                Ok(None) => println!("-"),
                Err(_) => println!("delegate"),
            }
            continue;
        }
        let policy = Policy {
            allowed_schemes: fields[6].split(',').map(str::to_owned).collect(),
            netloc_schemes: fields[7].split(',').map(str::to_owned).collect(),
            enable_file_links: fields[3] == "1",
            realm_url: (fields[4] != "-").then(|| decode(fields[4])),
        };
        let mut index = 9;
        let mut previews = HashMap::new();
        for _ in 0..fields[8].parse().unwrap_or(0) {
            previews.insert(
                decode(fields[index]),
                ImageMetadata {
                    width: decode(fields[index + 1]),
                    height: decode(fields[index + 2]),
                    content_type: (fields[index + 3] != "-").then(|| decode(fields[index + 3])),
                },
            );
            index += 4;
        }
        let stash_count = fields[index];
        index += 1;
        let stash: TextStash = fields[index..]
            .chunks_exact(2)
            .map(|pair| (decode(pair[0]), decode(pair[1])))
            .collect();
        match prepare_image(
            &data,
            fields[2].parse().unwrap(),
            (stash_count != "-").then_some(&stash),
            &policy,
            host.as_deref(),
            (fields[8] != "-").then_some(&previews),
        ) {
            Ok(outcome) => {
                print!(
                    "result\t{}",
                    outcome
                        .attachment_path
                        .as_deref()
                        .map_or("-".to_owned(), encode)
                );
                if let Some(image) = outcome.image {
                    print!("\t{}", image.end);
                    for (key, value) in image.attributes {
                        print!("\t{}\t{}", encode(&key), encode(&value));
                    }
                    println!();
                } else {
                    println!("\t-");
                }
            }
            Err(_) => println!("delegate"),
        }
    }
}

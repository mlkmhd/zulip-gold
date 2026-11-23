//! Hex UTF-8 protocol for image candidates and pending attachment effects.

use std::collections::HashMap;
use std::io::{self, BufRead};

use _zulip_backend::markdown_link_syntax::TextStash;
use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_media::{
    AudioPolicy, ImageMetadata, MimePolicy, attachment_path, camo_url, is_static_url, mime_type,
    prepare_audio, prepare_image,
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

fn read_map(fields: &[&str], index: &mut usize) -> HashMap<String, String> {
    let count: usize = fields[*index].parse().unwrap();
    *index += 1;
    (0..count)
        .map(|_| {
            let pair = (decode(fields[*index]), decode(fields[*index + 1]));
            *index += 2;
            pair
        })
        .collect()
}

fn main() {
    let mut audio_policy = None;
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        if fields[0] == "audio_policy" {
            let mut index = 6;
            audio_policy = Some(AudioPolicy {
                static_url: decode(fields[1]),
                external_uri_scheme: decode(fields[2]),
                public_upload_root: (fields[3] != "-").then(|| decode(fields[3])),
                camo_uri: decode(fields[4]),
                camo_key: (fields[5] != "-").then(|| decode(fields[5])),
                mime: MimePolicy {
                    suffix_map: read_map(&fields, &mut index),
                    encodings: read_map(&fields, &mut index).into_keys().collect(),
                    types: read_map(&fields, &mut index),
                    supported_audio_types: read_map(&fields, &mut index).into_keys().collect(),
                    param_schemes: read_map(&fields, &mut index).into_keys().collect(),
                },
            });
            continue;
        }
        let data = decode(fields[1]);
        if matches!(fields[0], "mime" | "static" | "camo") {
            let policy = audio_policy.as_ref().unwrap();
            match fields[0] {
                "mime" => match mime_type(&data, &policy.mime) {
                    Ok(Some(mime)) => println!("mime\t{}", encode(&mime)),
                    Ok(None) => println!("-"),
                    Err(_) => println!("delegate"),
                },
                "static" => {
                    let host = (fields[2] != "-").then(|| decode(fields[2]));
                    match is_static_url(&data, host.as_deref(), policy) {
                        Ok(value) => println!("{}", u8::from(value)),
                        Err(_) => println!("delegate"),
                    }
                }
                _ => match camo_url(&data, policy) {
                    Ok(url) => println!("camo\t{}", encode(&url)),
                    Err(_) => println!("delegate"),
                },
            }
            continue;
        }
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
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| (decode(pair[0]), decode(pair[1])))
            .collect();
        if fields[0] == "audio" {
            let previews = previews.into_keys().collect();
            match prepare_audio(
                &data,
                fields[2].parse().unwrap(),
                (stash_count != "-").then_some(&stash),
                &policy,
                host.as_deref(),
                (fields[8] != "-").then_some(&previews),
                audio_policy.as_ref().unwrap(),
            ) {
                Ok(outcome) => {
                    print!(
                        "result\t{}",
                        outcome
                            .attachment_path
                            .as_deref()
                            .map_or("-".to_owned(), encode)
                    );
                    if let Some(audio) = outcome.audio {
                        print!("\t{}", audio.end);
                        for (key, value) in audio.attributes {
                            print!("\t{}\t{}", encode(&key), encode(&value));
                        }
                        println!();
                    } else {
                        println!("\t-");
                    }
                }
                Err(_) => println!("delegate"),
            }
            continue;
        }
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

//! Tab-separated arena protocol for ElementTree text snapshot comparisons.

use std::io::{self, BufRead};

use _zulip_backend::markdown_native_inline::{Node, Stashed, Text, snapshot_text_stash};

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

fn text(value: &str) -> Option<Text> {
    (value != "-").then(|| Text {
        value: decode(value),
        atomic: false,
    })
}

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let mut fields = line.split('\t');
        let count = fields.next().unwrap().parse::<usize>().unwrap();
        let nodes = (0..count)
            .map(|_| Node {
                tag: "span".to_owned(),
                lower_tag: "span".to_owned(),
                text: text(fields.next().unwrap()),
                tail: text(fields.next().unwrap()),
                children: fields
                    .next()
                    .unwrap()
                    .split(',')
                    .filter(|s| !s.is_empty())
                    .map(|s| s.parse().unwrap())
                    .collect(),
                attributes: vec![],
            })
            .collect::<Vec<_>>();
        let stash = fields
            .map(|field| {
                if let Some(index) = field.strip_prefix("n:") {
                    Stashed::Node(index.parse().unwrap())
                } else {
                    Stashed::Text(text(field.strip_prefix("t:").unwrap()).unwrap())
                }
            })
            .collect::<Vec<_>>();
        match snapshot_text_stash(&nodes, &stash) {
            None => println!("reject"),
            Some(snapshot) => {
                let values = (0..stash.len())
                    .map(|i| {
                        snapshot[&format!("{i:04}")]
                            .bytes()
                            .map(|b| format!("{b:02x}"))
                            .collect::<String>()
                    })
                    .collect::<Vec<_>>();
                println!("ok\t{}", values.join("\t"));
            }
        }
    }
}

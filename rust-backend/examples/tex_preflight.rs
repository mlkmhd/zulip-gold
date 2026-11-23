//! Graph/stash transport for the reference-Python TeX preflight differential.

use std::io::{self, BufRead};

use _zulip_backend::markdown_links::Policy;
use _zulip_backend::markdown_native_inline::{Inputs, Node, Pattern, Stashed, Text};
use _zulip_backend::markdown_tex_preflight::preflight;

fn decode(value: &str) -> String {
    String::from_utf8(
        value
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect(),
    )
    .unwrap()
}

fn encode(value: &str) -> String {
    value.bytes().map(|b| format!("{b:02x}")).collect()
}

fn node(tag: &str, text: Option<&str>, children: Vec<usize>) -> Node {
    Node {
        tag: tag.into(),
        lower_tag: tag.into(),
        text: text.map(|value| Text {
            value: value.into(),
            atomic: false,
        }),
        tail: None,
        children,
        attributes: vec![],
    }
}

fn plan(fields: &[&str]) {
    let history = fields.last() == Some(&"history");
    let fields = if history {
        &fields[..fields.len() - 1]
    } else {
        fields
    };
    let data = decode(fields[0]);
    let mut nodes = vec![node("div", None, vec![1]), node("p", Some(&data), vec![])];
    match fields[2] {
        "1" => {
            nodes[1].tail = nodes[1].text.take();
            nodes[1].text = Some(Text {
                value: "$$leading$$ &amp;".into(),
                atomic: false,
            });
            nodes[0].children.push(2);
            nodes.push(node("p", Some("$$trailing$$"), vec![]));
        }
        "2" => {
            nodes[1].children.push(2);
            let mut child = node("span", Some("$$child$$"), vec![]);
            child.tail = Some(Text {
                value: "$$tail$$".into(),
                atomic: false,
            });
            nodes.push(child);
        }
        "3" => nodes[1].text.as_mut().unwrap().atomic = true,
        "4" => {
            nodes[1].children.push(2);
            let mut shared = node("span", Some("$$shared$$"), vec![]);
            shared.tail = Some(Text {
                value: "$$shared-tail$$".into(),
                atomic: false,
            });
            nodes.push(shared);
            nodes[0].children.push(3);
            nodes.push(node("p", Some("$$other$$"), vec![2]));
        }
        _ => {}
    }
    let kinds: &[u8] = match fields[1] {
        "reverse" => &[20, 11, 22, 1, 2, 3, 4, 5, 6, 7],
        "last" => &[1, 2, 11, 20, 3, 4, 5, 6, 7, 22],
        _ => &[1, 2, 22, 11, 20, 3, 4, 5, 6, 7],
    };
    let patterns: Vec<_> = kinds
        .iter()
        .map(|&kind| Pattern {
            kind,
            excludes: vec![],
            emoji: None,
            emoticons: None,
            unicode_emoji: None,
            links: (kind == 11).then(|| Policy {
                allowed_schemes: vec!["http".into(), "https".into(), "mailto".into()],
                netloc_schemes: vec!["http".into(), "https".into()],
                enable_file_links: false,
                realm_url: None,
            }),
        })
        .collect();
    let counter: usize = fields[3].parse().unwrap();
    let Some(checked) = preflight(
        nodes,
        &patterns,
        vec!["outer".into()],
        Inputs {
            html_counter: Some(counter),
            ..Inputs::default()
        },
        |_, _| panic!("all handlers are native"),
    ) else {
        println!("delegate");
        return;
    };
    let calls = checked
        .calls()
        .iter()
        .map(|body| format!("x{}", encode(body)))
        .collect::<Vec<_>>()
        .join(",");
    let mask: usize = fields[4].parse().unwrap();
    let prefix = fields.get(5).map(|field| field.parse::<usize>().unwrap());
    if prefix.is_some_and(|prefix| prefix >= checked.calls().len()) {
        println!("no-checkpoint");
        return;
    }
    let calls = if let Some(prefix) = prefix {
        calls
            .split(',')
            .take(prefix + 1)
            .collect::<Vec<_>>()
            .join(",")
    } else {
        calls
    };
    let results = (0..checked.calls().len())
        .take(prefix.unwrap_or(checked.calls().len()))
        .map(|index| {
            (mask & (1 << index) != 0).then(|| {
                if index == 0 {
                    String::new()
                } else {
                    format!("<math>{index} &amp; $$raw$$</math>")
                }
            })
        })
        .collect();
    let result = if prefix.is_some() {
        let mut progress = checked.begin();
        for result in results {
            progress = progress.next_call.unwrap().complete(result);
        }
        assert!(progress.next_call.is_some());
        assert!(progress.state.registry_used);
        progress.state
    } else {
        checked.into_plan(results).unwrap()
    };
    let text = |value: Option<&Text>| {
        value.map_or("-".into(), |value| {
            format!("{}{}", u8::from(value.atomic), encode(&value.value))
        })
    };
    let ancestors = result
        .ancestors
        .iter()
        .map(|tag| encode(tag))
        .collect::<Vec<_>>()
        .join(",");
    let parents = result
        .parent_order
        .iter()
        .map(|child| format!("{child}:{}", result.parents[*child].unwrap()))
        .collect::<Vec<_>>()
        .join(",");
    let stash = result
        .stash
        .iter()
        .map(|value| match value {
            Stashed::Node(node) => format!("n{node}"),
            Stashed::Text(value) => format!("t{}", text(Some(value))),
        })
        .collect::<Vec<_>>()
        .join(",");
    print!("plan\t0\t\t{ancestors}\t{parents}\t{stash}");
    for node in &result.nodes {
        let children = node
            .children
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join(":");
        let attributes = node
            .attributes
            .iter()
            .map(|(key, value)| format!("{}:{}", encode(key), encode(value)))
            .collect::<Vec<_>>()
            .join(";");
        print!(
            "\t{},{},{},{},{children},{attributes}",
            encode(&node.tag),
            encode(&node.lower_tag),
            text(node.text.as_ref()),
            text(node.tail.as_ref())
        );
    }
    let blocks = result
        .html_blocks
        .iter()
        .map(|block| format!("x{}", encode(block)))
        .collect::<Vec<_>>()
        .join(",");
    print!("\ttex\t{calls}\t{blocks}");
    if history {
        let history = result
            .ancestor_history
            .iter()
            .map(|values| {
                format!(
                    "x{}",
                    values
                        .iter()
                        .map(|value| encode(value))
                        .collect::<Vec<_>>()
                        .join(",")
                )
            })
            .collect::<Vec<_>>()
            .join(";");
        print!("\tancestors\t{history}");
    }
    println!();
}

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let fields: Vec<_> = line.split('\t').collect();
        assert!(matches!(fields.len(), 5 | 6) || (fields.len() == 7 && fields[6] == "history"));
        plan(&fields);
    }
}

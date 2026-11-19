#[derive(Debug)]
pub enum Tag {
    Element(String),
    Container,
    Comment,
    ProcessingInstruction,
}

#[derive(Debug)]
pub struct Node {
    pub tag: Tag,
    pub text: Option<String>,
    pub tail: Option<String>,
    pub attributes: Vec<(String, String)>,
    pub children: usize,
}

fn entity_length(text: &str) -> Option<usize> {
    let body = text.strip_prefix('&')?;
    let (body, numeric, hex) = if let Some(body) = body.strip_prefix('#') {
        if let Some(body) = body.strip_prefix(['x', 'X']) {
            (body, true, true)
        } else {
            (body, true, false)
        }
    } else {
        (body, false, false)
    };
    let length = body
        .chars()
        .take_while(|c| {
            if hex {
                c.is_ascii_hexdigit()
            } else if numeric {
                c.is_ascii_digit()
            } else {
                c.is_ascii_alphanumeric() || matches!(c, 'İ' | 'ı' | 'ſ' | 'K')
            }
        })
        .map(char::len_utf8)
        .sum::<usize>();
    (length > 0 && body[length..].starts_with(';')).then_some(text.len() - body.len() + length + 1)
}

fn escape(output: &mut String, mut text: &str, attribute: bool) {
    while let Some(c) = text.chars().next() {
        match c {
            '&' => {
                if let Some(length) = entity_length(text) {
                    output.push_str(&text[..length]);
                    text = &text[length..];
                    continue;
                }
                output.push_str("&amp;");
            }
            '<' => output.push_str("&lt;"),
            '>' => output.push_str("&gt;"),
            '"' if attribute => output.push_str("&quot;"),
            _ => output.push(c),
        }
        text = &text[c.len_utf8()..];
    }
}

fn write_node(
    output: &mut String,
    nodes: &[Node],
    index: &mut usize,
    depth: usize,
    empty_tags: &[String],
) -> Option<()> {
    if depth > 100 {
        return None;
    }
    let node = nodes.get(*index)?;
    *index += 1;
    let text = node.text.as_deref().unwrap_or("");
    match &node.tag {
        Tag::Comment | Tag::ProcessingInstruction => {
            if node.children != 0 || node.text.is_none() {
                return None;
            }
            let comment = matches!(node.tag, Tag::Comment);
            output.push_str(if comment { "<!--" } else { "<?" });
            escape(output, text, false);
            output.push_str(if comment { "-->" } else { "?>" });
        }
        Tag::Element(_) | Tag::Container => {
            let mut lower_tag = String::new();
            if let Tag::Element(tag) = &node.tag {
                lower_tag = tag.to_lowercase();
                output.push('<');
                output.push_str(tag);
                let mut attributes = node.attributes.iter().collect::<Vec<_>>();
                attributes.sort_unstable();
                for (key, value) in attributes {
                    let mut escaped = String::new();
                    escape(&mut escaped, value, true);
                    output.push(' ');
                    output.push_str(key);
                    if key != &escaped {
                        output.push_str("=\"");
                        output.push_str(&escaped);
                        output.push('"');
                    }
                }
                output.push('>');
            }
            if matches!(lower_tag.as_str(), "script" | "style") {
                output.push_str(text);
            } else {
                escape(output, text, false);
            }
            for _ in 0..node.children {
                write_node(output, nodes, index, depth + 1, empty_tags)?;
            }
            if let Tag::Element(tag) = &node.tag
                && !empty_tags.contains(&lower_tag)
            {
                output.push_str("</");
                output.push_str(tag);
                output.push('>');
            }
        }
    }
    escape(output, node.tail.as_deref().unwrap_or(""), false);
    Some(())
}

pub fn serialize(nodes: &[Node], empty_tags: &[String]) -> Option<String> {
    let mut output = String::new();
    let mut index = 0;
    write_node(&mut output, nodes, &mut index, 0, empty_tags)?;
    (index == nodes.len()).then_some(output)
}

#[cfg(test)]
mod tests {
    use super::{Node, Tag, escape, serialize};

    #[test]
    fn entities_and_attribute_quotes() {
        let text = "<&bogus; &#12; &#XfF; &İ; &ı; &ſ; &K; &é; &#; &#x; &; \"\n>";
        let mut output = String::new();
        escape(&mut output, text, true);
        assert_eq!(
            output,
            "&lt;&bogus; &#12; &#XfF; &İ; &ı; &ſ; &K; &amp;é; &amp;#; &amp;#x; &amp;; &quot;\n&gt;"
        );
    }

    #[test]
    fn html_empty_elements_keep_their_content_and_tail() {
        let nodes = [Node {
            tag: Tag::Element("BR".to_owned()),
            text: Some("<&>".to_owned()),
            tail: Some("tail &".to_owned()),
            attributes: vec![
                ("z".to_owned(), "\"".to_owned()),
                ("checked".to_owned(), "checked".to_owned()),
            ],
            children: 0,
        }];
        assert_eq!(
            serialize(&nodes, &["br".to_owned()]).as_deref(),
            Some("<BR checked z=\"&quot;\">&lt;&amp;&gt;tail &amp;")
        );
    }

    #[test]
    fn incomplete_trees_are_rejected() {
        assert_eq!(serialize(&[], &[]), None);
        let node = Node {
            tag: Tag::Container,
            text: None,
            tail: None,
            attributes: vec![],
            children: 1,
        };
        assert_eq!(serialize(&[node], &[]), None);
    }
}

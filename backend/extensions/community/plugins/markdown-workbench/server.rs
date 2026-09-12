//! Markdown Workbench 的零依赖原生 MCP stdio 入口。
use std::io::{self, BufRead, Write};

fn json_escape(value: &str) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character.is_control() => {
                output.push_str(&format!("\\u{:04x}", character as u32));
            }
            character => output.push(character),
        }
    }
    output.push('"');
    output
}

fn raw_field<'a>(input: &'a str, name: &str) -> Option<&'a str> {
    let marker = format!("\"{name}\":");
    let tail = input.split_once(&marker)?.1.trim_start();
    if tail.starts_with('"') {
        let mut escaped = false;
        for (index, character) in tail[1..].char_indices() {
            if character == '"' && !escaped {
                return Some(&tail[..index + 2]);
            }
            escaped = character == '\\' && !escaped;
            if character != '\\' {
                escaped = false;
            }
        }
        None
    } else {
        Some(tail.split([',', '}']).next()?.trim())
    }
}

fn string_field(input: &str, name: &str) -> Option<String> {
    let raw = raw_field(input, name)?;
    if !raw.starts_with('"') || !raw.ends_with('"') {
        return None;
    }
    let mut output = String::new();
    let mut characters = raw[1..raw.len() - 1].chars();
    while let Some(character) = characters.next() {
        if character != '\\' {
            output.push(character);
            continue;
        }
        match characters.next()? {
            '"' => output.push('"'),
            '\\' => output.push('\\'),
            '/' => output.push('/'),
            'b' => output.push('\u{8}'),
            'f' => output.push('\u{c}'),
            'n' => output.push('\n'),
            'r' => output.push('\r'),
            't' => output.push('\t'),
            'u' => {
                let digits: String = characters.by_ref().take(4).collect();
                let code = u32::from_str_radix(&digits, 16).ok()?;
                output.push(char::from_u32(code)?);
            }
            _ => return None,
        }
    }
    Some(output)
}

fn statistics(text: &str) -> (usize, usize, usize, usize, usize) {
    let lines = text.lines().count();
    let mut headings = 0;
    let mut issues = 0;
    let mut previous_level = 0;
    let mut titles = std::collections::BTreeSet::new();
    let mut fence = false;
    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            fence = !fence;
            continue;
        }
        if fence {
            continue;
        }
        let level = trimmed.chars().take_while(|character| *character == '#').count();
        if !(1..=6).contains(&level) || !trimmed[level..].starts_with(' ') {
            continue;
        }
        headings += 1;
        if previous_level > 0 && level > previous_level + 1 {
            issues += 1;
        }
        let title = trimmed[level..].trim().trim_end_matches('#').trim().to_lowercase();
        if !titles.insert(title) {
            issues += 1;
        }
        previous_level = level;
    }
    let tasks = text
        .lines()
        .filter(|line| line.contains("[ ]") || line.contains("[x]") || line.contains("[X]"))
        .count();
    let open_tasks = text.lines().filter(|line| line.contains("[ ]")).count();
    (lines, headings, tasks, open_tasks, issues)
}

fn report(text: &str) -> String {
    let (lines, headings, tasks, open_tasks, issues) = statistics(text);
    format!(
        "{{\"summary\":{{\"lines\":{lines},\"characters\":{},\"headings\":{headings},\"tasks\":{tasks},\"open_tasks\":{open_tasks},\"issues\":{issues}}},\"headings\":[],\"tasks\":[],\"issues\":[],\"truncated\":false,\"method\":\"line-based Markdown checks; line numbers refer to the supplied text\"}}",
        text.chars().count()
    )
}

fn reply(id: &str, result: &str) {
    println!("{{\"jsonrpc\":\"2.0\",\"id\":{id},\"result\":{result}}}");
    io::stdout().flush().expect("无法刷新 MCP 输出");
}

fn main() {
    let stdin = io::stdin();
    for line in stdin.lock().lines().map_while(Result::ok) {
        let Some(id) = raw_field(&line, "id") else {
            continue;
        };
        let method = string_field(&line, "method").unwrap_or_default();
        match method.as_str() {
            "initialize" => reply(id, "{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"markdown-workbench\",\"version\":\"1.0.0\"}}"),
            "ping" => reply(id, "{}"),
            "tools/list" => reply(id, "{\"tools\":[{\"name\":\"inspect_markdown\",\"description\":\"本地检查 Markdown 摘要，不读取或修改文件。\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"text\":{\"type\":\"string\",\"maxLength\":100000}},\"required\":[\"text\"],\"additionalProperties\":false}},{\"name\":\"selection_report\",\"description\":\"检查 OpenNexus 当前选区。\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"_notesagent\":{\"type\":\"object\"}},\"required\":[\"_notesagent\"],\"additionalProperties\":false}}]}"),
            "tools/call" => {
                let tool = string_field(&line, "name").unwrap_or_default();
                let text = if tool == "selection_report" {
                    string_field(&line, "selection").unwrap_or_default()
                } else {
                    string_field(&line, "text").unwrap_or_default()
                };
                if text.chars().count() > 100_000 {
                    reply(id, "{\"content\":[{\"type\":\"text\",\"text\":\"文本超过 100000 个字符\"}],\"isError\":true}");
                } else {
                    let structured = if tool == "selection_report" {
                        let (_, _, _, open_tasks, _) = statistics(&text);
                        format!("{{\"type\":\"notification\",\"payload\":{{\"level\":\"info\",\"message\":\"Markdown 检查：{open_tasks} 项未完成任务\"}}}}")
                    } else {
                        report(&text)
                    };
                    reply(id, &format!("{{\"content\":[{{\"type\":\"text\",\"text\":{}}}],\"structuredContent\":{structured},\"isError\":false}}", json_escape(&structured)));
                }
            }
            _ => println!("{{\"jsonrpc\":\"2.0\",\"id\":{id},\"error\":{{\"code\":-32601,\"message\":\"不支持的方法\"}}}}"),
        }
    }
}

//! Study Plan Kit 的零依赖原生 MCP stdio 入口。
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
            character if character.is_control() => output.push_str(&format!("\\u{:04x}", character as u32)),
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
            if character != '\\' { escaped = false; }
        }
        None
    } else {
        Some(tail.split([',', '}']).next()?.trim())
    }
}

fn string_field(input: &str, name: &str) -> Option<String> {
    let raw = raw_field(input, name)?;
    if !raw.starts_with('"') || !raw.ends_with('"') { return None; }
    let mut output = String::new();
    let mut characters = raw[1..raw.len() - 1].chars();
    while let Some(character) = characters.next() {
        if character != '\\' { output.push(character); continue; }
        match characters.next()? {
            '"' => output.push('"'), '\\' => output.push('\\'), '/' => output.push('/'),
            'n' => output.push('\n'), 'r' => output.push('\r'), 't' => output.push('\t'),
            'u' => {
                let digits: String = characters.by_ref().take(4).collect();
                output.push(char::from_u32(u32::from_str_radix(&digits, 16).ok()?)?);
            }
            _ => return None,
        }
    }
    Some(output)
}

fn int_field(input: &str, name: &str) -> Option<usize> {
    raw_field(input, name)?.parse().ok()
}

fn reply(id: &str, result: &str) {
    println!("{{\"jsonrpc\":\"2.0\",\"id\":{id},\"result\":{result}}}");
    io::stdout().flush().expect("无法刷新 MCP 输出");
}

fn sprint(topic: &str, days: usize, daily: usize, confidence: usize, weak_points: &str) -> String {
    let foundation_days = if days == 1 { 1 } else { (days + 3) / 4 };
    let review_days = if days >= 4 { 1 } else { 0 };
    let practice_days = days - foundation_days - review_days;
    let learn = daily * (7 - confidence) / 10;
    let recall = (daily / 5).max(5);
    let practice = daily.saturating_sub(learn + recall);
    let mut schedule = Vec::new();
    for day in 1..=days {
        let phase = if day <= foundation_days { "理解" } else if day > days - review_days { "复盘" } else { "练习" };
        schedule.push(format!(
            "{{\"day\":{day},\"phase\":\"{phase}\",\"learn_minutes\":{learn},\"practice_minutes\":{practice},\"recall_minutes\":{recall},\"acceptance\":\"提交一项可检查产物并完成一次无提示回忆\"}}"
        ));
    }
    format!(
        "{{\"topic\":{},\"constraints\":{{\"days\":{days},\"daily_minutes\":{daily},\"total_minutes\":{},\"confidence\":{confidence},\"weak_points\":{}}},\"phases\":{{\"foundation_days\":{foundation_days},\"practice_days\":{practice_days},\"review_days\":{review_days}}},\"schedule\":[{}],\"method\":\"deterministic time-box skeleton; the Agent must ground concrete tasks in the supplied course notes\"}}",
        json_escape(topic), days * daily, json_escape(weak_points), schedule.join(",")
    )
}

fn main() {
    let stdin = io::stdin();
    for line in stdin.lock().lines().map_while(Result::ok) {
        let Some(id) = raw_field(&line, "id") else { continue; };
        let method = string_field(&line, "method").unwrap_or_default();
        match method.as_str() {
            "initialize" => reply(id, "{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"study-plan-kit\",\"version\":\"1.0.0\"}}"),
            "ping" => reply(id, "{}"),
            "tools/list" => reply(id, "{\"tools\":[{\"name\":\"build_sprint\",\"description\":\"根据时间与掌握度生成确定性的学习冲刺骨架。\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"topic\":{\"type\":\"string\",\"maxLength\":200},\"days\":{\"type\":\"integer\",\"minimum\":1,\"maximum\":30},\"daily_minutes\":{\"type\":\"integer\",\"minimum\":15,\"maximum\":480},\"confidence\":{\"type\":\"integer\",\"minimum\":1,\"maximum\":5},\"weak_points\":{\"type\":\"string\",\"maxLength\":2000}},\"required\":[\"topic\",\"days\",\"daily_minutes\",\"confidence\",\"weak_points\"],\"additionalProperties\":false}}]}"),
            "tools/call" => {
                let topic = string_field(&line, "topic").unwrap_or_default();
                let days = int_field(&line, "days").unwrap_or(0);
                let daily = int_field(&line, "daily_minutes").unwrap_or(0);
                let confidence = int_field(&line, "confidence").unwrap_or(0);
                let weak_points = string_field(&line, "weak_points").unwrap_or_default();
                if topic.is_empty() || topic.chars().count() > 200 || !(1..=30).contains(&days) || !(15..=480).contains(&daily) || !(1..=5).contains(&confidence) || weak_points.chars().count() > 2000 {
                    reply(id, "{\"content\":[{\"type\":\"text\",\"text\":\"规划参数超出允许范围\"}],\"isError\":true}");
                } else {
                    let structured = sprint(&topic, days, daily, confidence, &weak_points);
                    reply(id, &format!("{{\"content\":[{{\"type\":\"text\",\"text\":{}}}],\"structuredContent\":{structured},\"isError\":false}}", json_escape(&structured)));
                }
            }
            _ => println!("{{\"jsonrpc\":\"2.0\",\"id\":{id},\"error\":{{\"code\":-32601,\"message\":\"不支持的方法\"}}}}"),
        }
    }
}

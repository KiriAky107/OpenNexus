//! Portable JSON Canvas ink. Rendering and storage share these hard bounds.
use serde_json::Value;
use std::collections::HashSet;

pub const KEY: &str = "opennexusHandwriting";
fn number(v: Option<&Value>, min: f64, max: f64) -> bool {
    v.and_then(Value::as_f64)
        .is_some_and(|n| n.is_finite() && n >= min && n <= max)
}
pub fn valid(v: &Value) -> bool {
    if !v.is_object()
        || v["version"].as_f64() != Some(1.0)
        || !matches!(v["mode"].as_str(), Some("ink" | "text"))
        || !matches!(v["paper"].as_str(), Some("plain" | "ruled" | "grid"))
        || !number(v.get("width"), 64.0, 4096.0)
        || !number(v.get("height"), 64.0, 4096.0)
        || !number(v.get("fontSize"), 12.0, 72.0)
        || !number(v.get("lineHeight"), 1.0, 3.0)
        || !number(v.get("spacing"), 0.0, 12.0)
        || !number(v.get("variation"), 0.0, 0.3)
        || !v["seed"].as_f64().is_some_and(|n| {
            n.is_finite() && (0.0..=2_147_483_647.0).contains(&n) && n.fract() == 0.0
        })
    {
        return false;
    }
    let Some(strokes) = v["strokes"].as_array() else {
        return false;
    };
    if strokes.len() > 1000 || (v["mode"] == "text" && !strokes.is_empty()) {
        return false;
    }
    let mut ids = HashSet::new();
    let mut count = 0;
    for stroke in strokes {
        let Some(id) = stroke["id"].as_str() else {
            return false;
        };
        let Some(color) = stroke["color"].as_str() else {
            return false;
        };
        let Some(points) = stroke["points"].as_array() else {
            return false;
        };
        count += points.len();
        if id.is_empty()
            || id.len() > 128
            || !ids.insert(id)
            || color.len() != 7
            || !color.starts_with('#')
            || !color[1..].bytes().all(|b| b.is_ascii_hexdigit())
            || !number(stroke.get("width"), 0.5, 24.0)
            || points.is_empty()
            || count > 20000
        {
            return false;
        }
        for p in points {
            if !number(p.get("x"), 0.0, v["width"].as_f64().unwrap_or_default())
                || !number(p.get("y"), 0.0, v["height"].as_f64().unwrap_or_default())
                || !number(p.get("pressure"), 0.0, 1.0)
            {
                return false;
            }
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn ink_bounds_and_bool_numbers_are_checked() {
        let mut ink = json!({"version":1,"mode":"ink","width":960,"height":640,"paper":"ruled","fontSize":28,"lineHeight":1.7,"spacing":1,"variation":0.08,"seed":42,"strokes":[{"id":"s","color":"#263746","width":3,"points":[{"x":0,"y":640,"pressure":0.5}]}]});
        assert!(valid(&ink));
        ink["strokes"][0]["points"][0]["x"] = json!(true);
        assert!(!valid(&ink));
        ink["strokes"][0]["points"][0]["x"] = json!(961);
        assert!(!valid(&ink));
    }
}

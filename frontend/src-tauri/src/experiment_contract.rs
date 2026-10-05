//! Persistent user-owned experiment source and input files.
use std::path::Path;

pub fn is_experiment_file(path: &str) -> bool {
    let mut parts = path.split('/');
    if !parts
        .next()
        .is_some_and(|part| part.eq_ignore_ascii_case("experiments"))
    {
        return false;
    }
    matches!(
        Path::new(path)
            .extension()
            .and_then(|value| value.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("py" | "json" | "csv")
    )
}

//! Offline verification primitives. Passing these checks does not authorize installation or execution.
use crate::workspace::{hash, HostError, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Cursor, Read},
};
use unicode_casefold::UnicodeCaseFold;
use unicode_normalization::UnicodeNormalization;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Release {
    pub schema_version: u32,
    pub namespace: String,
    pub package_id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub version: String,
    pub name: String,
    pub author_id: String,
    pub license: String,
    pub description: String,
    pub sha256: String,
    pub size: u64,
    pub platforms: Vec<String>,
    pub architectures: Vec<String>,
    pub min_app_version: String,
    pub max_app_version: Option<String>,
    pub dependencies: BTreeMap<String, String>,
    pub permissions: Vec<String>,
    pub changelog: String,
    pub published_at: String,
    pub key_id: String,
    pub signature: String,
}
fn bounded(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.chars().count())
}
fn identity(s: &str) -> bool {
    (2..=64).contains(&s.len())
        && s.as_bytes()[0].is_ascii_alphanumeric()
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
fn version(s: &str) -> bool {
    if s.len() > 80 {
        return false;
    }
    let (core, pre) = s
        .split_once('-')
        .map(|(a, b)| (a, Some(b)))
        .unwrap_or((s, None));
    let parts: Vec<_> = core.split('.').collect();
    parts.len() == 3
        && parts
            .iter()
            .all(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
        && pre.is_none_or(|v| {
            !v.is_empty()
                && v.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b".-".contains(&b))
        })
}
impl Release {
    pub fn validate(&self) -> Result<()> {
        let valid = self.schema_version == 1
            && identity(&self.namespace)
            && identity(&self.package_id)
            && matches!(
                self.kind.as_str(),
                "theme" | "skill" | "plugin" | "mcp" | "persona" | "template" | "model"
            )
            && version(&self.version)
            && bounded(&self.name, 1, 120)
            && bounded(&self.author_id, 1, 80)
            && bounded(&self.license, 1, 80)
            && !matches!(
                self.license.to_lowercase().as_str(),
                "unknown" | "none" | "unlicensed" | "tbd"
            )
            && bounded(&self.description, 0, 10000)
            && bounded(&self.changelog, 0, 10000)
            && self.sha256.len() == 64
            && self
                .sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            && (1..=10 * 1024 * 1024).contains(&self.size)
            && self.platforms.len() <= 12
            && self.architectures.len() <= 12
            && self
                .platforms
                .iter()
                .chain(&self.architectures)
                .all(|s| bounded(s, 1, 80))
            && version(&self.min_app_version)
            && self.max_app_version.as_ref().is_none_or(|s| version(s))
            && self.dependencies.len() <= 64
            && self
                .dependencies
                .iter()
                .all(|(k, v)| bounded(k, 1, 160) && bounded(v, 1, 256))
            && self.permissions.len() <= 64
            && self.permissions.iter().all(|v| bounded(v, 1, 256))
            && bounded(&self.published_at, 0, 40)
            && bounded(&self.key_id, 1, 80)
            && self
                .key_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
            && self.signature.len() <= 128;
        if valid {
            Ok(())
        } else {
            Err(HostError::new("EXTENSION_RELEASE_INVALID"))
        }
    }
    pub fn signed_payload(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut value =
            serde_json::to_value(self).map_err(|_| HostError::new("EXTENSION_RELEASE_INVALID"))?;
        value.as_object_mut().unwrap().remove("signature");
        fn canonical(v: &Value) -> String {
            match v {
                Value::Object(map) => {
                    let sorted: BTreeMap<_, _> = map.iter().collect();
                    format!(
                        "{{{}}}",
                        sorted
                            .iter()
                            .map(|(k, v)| format!(
                                "{}:{}",
                                serde_json::to_string(k).unwrap(),
                                canonical(v)
                            ))
                            .collect::<Vec<_>>()
                            .join(",")
                    )
                }
                Value::Array(items) => format!(
                    "[{}]",
                    items.iter().map(canonical).collect::<Vec<_>>().join(",")
                ),
                _ => v.to_string(),
            }
        }
        Ok(canonical(&value).into_bytes())
    }
    /// `pinned` must come from the Host trust store, never from the archive or a WebView assertion.
    pub fn verify(
        &self,
        pinned: &[u8; 32],
        key_id: &str,
        namespace: &str,
        revoked: bool,
        withdrawn: bool,
        archive: &[u8],
    ) -> Result<()> {
        self.validate()?;
        if revoked || withdrawn {
            return Err(HostError::new("EXTENSION_REVOKED"));
        }
        if key_id != self.key_id || namespace != self.namespace {
            return Err(HostError::new("EXTENSION_SIGNER_MISMATCH"));
        }
        let bytes = STANDARD
            .decode(&self.signature)
            .map_err(|_| HostError::new("EXTENSION_SIGNATURE_INVALID"))?;
        let signature = Signature::from_slice(&bytes)
            .map_err(|_| HostError::new("EXTENSION_SIGNATURE_INVALID"))?;
        let key = VerifyingKey::from_bytes(pinned)
            .map_err(|_| HostError::new("EXTENSION_SIGNATURE_INVALID"))?;
        key.verify_strict(&self.signed_payload()?, &signature)
            .map_err(|_| HostError::new("EXTENSION_SIGNATURE_INVALID"))?;
        if archive.len() as u64 != self.size || hash(archive) != self.sha256 {
            return Err(HostError::new("EXTENSION_ARCHIVE_MISMATCH"));
        }
        Ok(())
    }
}

#[derive(Debug)]
pub struct Inventory {
    pub files: BTreeMap<String, String>,
    pub expanded_size: u64,
    pub manifest: String,
}
// ZipArchive stores names in a map and can hide duplicate central entries. Inspect the
// bounded central directory before handing the archive to its decompressor.
fn directory(bytes: &[u8], max_entries: usize) -> Result<()> {
    let bad = || HostError::new("EXTENSION_ZIP_INVALID");
    let u16at = |p: usize| -> Result<usize> {
        Ok(u16::from_le_bytes(bytes.get(p..p + 2).ok_or_else(bad)?.try_into().unwrap()) as usize)
    };
    let u32at = |p: usize| -> Result<usize> {
        Ok(u32::from_le_bytes(bytes.get(p..p + 4).ok_or_else(bad)?.try_into().unwrap()) as usize)
    };
    let end = (bytes.len().saturating_sub(65557)..bytes.len().saturating_sub(21))
        .rev()
        .find(|p| {
            bytes.get(*p..*p + 4) == Some(b"PK\x05\x06")
                && u16at(*p + 20).is_ok_and(|n| *p + 22 + n == bytes.len())
        })
        .ok_or_else(bad)?;
    let count = u16at(end + 10)?;
    if u16at(end + 4)? != 0
        || u16at(end + 6)? != 0
        || u16at(end + 8)? != count
        || count == 0
        || count > max_entries
    {
        return Err(HostError::new("EXTENSION_ZIP_LIMIT"));
    }
    let mut pos = u32at(end + 16)?;
    let size = u32at(end + 12)?;
    if pos.checked_add(size) != Some(end) {
        return Err(bad());
    }
    let start = pos;
    let mut raw_names = BTreeSet::new();
    let mut ranges = Vec::new();
    for _ in 0..count {
        if bytes.get(pos..pos + 4) != Some(b"PK\x01\x02") {
            return Err(bad());
        }
        let length = u16at(pos + 28)?;
        let extra = u16at(pos + 30)?;
        let comment = u16at(pos + 32)?;
        let next = pos
            .checked_add(46 + length + extra + comment)
            .filter(|p| *p <= end)
            .ok_or_else(bad)?;
        let name = bytes.get(pos + 46..pos + 46 + length).ok_or_else(bad)?;
        if !raw_names.insert(name) {
            return Err(HostError::new("EXTENSION_ZIP_DUPLICATE"));
        }
        let local = u32at(pos + 42)?;
        if local >= start
            || bytes.get(local..local + 4) != Some(b"PK\x03\x04")
            || u16at(pos + 34)? != 0
            || u16at(local + 6)? != u16at(pos + 8)?
            || u16at(local + 8)? != u16at(pos + 10)?
        {
            return Err(bad());
        }
        let local_name = u16at(local + 26)?;
        let local_extra = u16at(local + 28)?;
        if bytes.get(local + 30..local + 30 + local_name) != Some(name) {
            return Err(bad());
        }
        let data_end = local
            .checked_add(30 + local_name + local_extra)
            .and_then(|p| p.checked_add(u32at(pos + 20).ok()?))
            .filter(|p| *p <= start)
            .ok_or_else(bad)?;
        ranges.push((local, data_end));
        pos = next;
    }
    if pos != end {
        return Err(bad());
    }
    ranges.sort_unstable();
    if ranges.windows(2).any(|pair| pair[0].1 > pair[1].0) {
        return Err(bad());
    }
    Ok(())
}
fn path(name: &str) -> Result<String> {
    if name.is_empty()
        || name.len() > 1024
        || name.nfc().collect::<String>() != name
        || name
            .chars()
            .any(|v| v.is_control() || "\\:<>|?*\"".contains(v))
    {
        return Err(HostError::new("EXTENSION_ZIP_PATH"));
    }
    let name = name.strip_suffix('/').unwrap_or(name);
    for part in name.split('/') {
        let stem = part.split('.').next().unwrap_or("").to_uppercase();
        if part.is_empty()
            || matches!(part, "." | "..")
            || part.ends_with(['.', ' '])
            || matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
            || ["COM", "LPT"].iter().any(|p| {
                stem.strip_prefix(p)
                    .is_some_and(|s| s.len() == 1 && b"123456789".contains(&s.as_bytes()[0]))
            })
        {
            return Err(HostError::new("EXTENSION_ZIP_PATH"));
        }
    }
    Ok(name.to_owned())
}
/// Checks all bytes (including CRC), without creating any package files.
/// Type-specific manifest schema/identity validation must follow before staging.
pub fn inspect(release: &Release, bytes: &[u8]) -> Result<Inventory> {
    release.validate()?;
    if bytes.len() as u64 != release.size || hash(bytes) != release.sha256 {
        return Err(HostError::new("EXTENSION_ARCHIVE_MISMATCH"));
    }
    let (compressed, expanded, entries) = if release.kind == "theme" {
        (5 * 1024 * 1024, 10 * 1024 * 1024, 100)
    } else {
        (10 * 1024 * 1024, 50 * 1024 * 1024, 2048)
    };
    if bytes.len() > compressed {
        return Err(HostError::new("EXTENSION_ZIP_LIMIT"));
    }
    directory(bytes, entries)?;
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| HostError::new("EXTENSION_ZIP_INVALID"))?;
    if archive.len() > entries {
        return Err(HostError::new("EXTENSION_ZIP_LIMIT"));
    }
    let mut names = BTreeMap::new();
    let mut total = 0u64;
    let mut files = BTreeMap::new();
    for index in 0..archive.len() {
        let mut file = archive
            .by_index(index)
            .map_err(|_| HostError::new("EXTENSION_ZIP_INVALID"))?;
        let name = path(file.name())?;
        // Include current Rust Unicode case mappings as well as full multi-character folds.
        let folded: String = name
            .case_fold()
            .flat_map(char::to_uppercase)
            .flat_map(char::to_lowercase)
            .nfc()
            .collect();
        if names.insert(folded, file.is_dir()).is_some() {
            return Err(HostError::new("EXTENSION_ZIP_DUPLICATE"));
        }
        let mode = file.unix_mode().unwrap_or(0) & 0o170000;
        if file.encrypted()
            || !matches!(
                file.compression(),
                zip::CompressionMethod::Stored | zip::CompressionMethod::Deflated
            )
            || !matches!(mode, 0 | 0o100000 | 0o040000)
            || (mode == 0o040000 && !file.is_dir())
            || (mode == 0o100000 && file.is_dir())
        {
            return Err(HostError::new("EXTENSION_ZIP_UNSAFE"));
        }
        total = total
            .checked_add(file.size())
            .ok_or_else(|| HostError::new("EXTENSION_ZIP_LIMIT"))?;
        if total > expanded {
            return Err(HostError::new("EXTENSION_ZIP_LIMIT"));
        }
        if file.is_dir() {
            if file.size() != 0 {
                return Err(HostError::new("EXTENSION_ZIP_UNSAFE"));
            }
            continue;
        }
        use sha2::{Digest, Sha256};
        let expected = file.size();
        let mut count = 0u64;
        let mut digest = Sha256::new();
        let mut buffer = [0u8; 65536];
        loop {
            let read = file
                .read(&mut buffer)
                .map_err(|_| HostError::new("EXTENSION_ZIP_INVALID"))?;
            if read == 0 {
                break;
            }
            count += read as u64;
            if count > expected {
                return Err(HostError::new("EXTENSION_ZIP_LIMIT"));
            }
            digest.update(&buffer[..read]);
        }
        if count != expected {
            return Err(HostError::new("EXTENSION_ZIP_INVALID"));
        }
        files.insert(name, format!("{:x}", digest.finalize()));
    }

    for (name, is_dir) in &names {
        if !is_dir
            && names
                .range(format!("{name}/")..)
                .next()
                .is_some_and(|(next, _)| next.starts_with(&format!("{name}/")))
        {
            return Err(HostError::new("EXTENSION_ZIP_PREFIX"));
        }
    }
    let required = match release.kind.as_str() {
        "theme" => "theme.yaml",
        "skill" => "skill.yaml",
        "plugin" => "plugin.yaml",
        "mcp" => "mcp.json",
        "persona" => "persona.json",
        "template" => "template.json",
        _ => "model.json",
    };
    let candidates: Vec<_> = files
        .keys()
        .filter(|name| name.as_str() == required || name.ends_with(&format!("/{required}")))
        .collect();
    if candidates.len() != 1 {
        return Err(HostError::new("EXTENSION_MANIFEST_INVALID"));
    }
    Ok(Inventory {
        manifest: candidates[0].clone(),
        files,
        expanded_size: total,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    fn fixture() -> (Release, Vec<u8>, [u8; 32]) {
        let mut data: Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        for field in ["release_id", "withdrawn", "download_path"] {
            data["release"].as_object_mut().unwrap().remove(field);
        }
        (
            serde_json::from_value(data["release"].clone()).unwrap(),
            STANDARD
                .decode(data["archive_base64"].as_str().unwrap())
                .unwrap(),
            STANDARD
                .decode(data["key"]["public_key"].as_str().unwrap())
                .unwrap()
                .try_into()
                .unwrap(),
        )
    }
    fn archive(items: &[(&str, &[u8])], method: zip::CompressionMethod) -> Vec<u8> {
        let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for (name, body) in items {
            writer
                .start_file(
                    *name,
                    zip::write::SimpleFileOptions::default()
                        .compression_method(method)
                        .unix_permissions(0o644),
                )
                .unwrap();
            writer.write_all(body).unwrap();
        }
        writer.finish().unwrap().into_inner()
    }
    fn check(bytes: &[u8], kind: &str) -> Result<Inventory> {
        let mut r = fixture().0;
        r.kind = kind.into();
        r.size = bytes.len() as u64;
        r.sha256 = hash(bytes);
        inspect(&r, bytes)
    }
    #[test]
    fn python_signature_binds_all_metadata_archive_and_signer() {
        let (release, bytes, key) = fixture();
        release
            .verify(&key, "test-key", "examples", false, false, &bytes)
            .unwrap();
        let inventory = inspect(&release, &bytes).unwrap();
        assert_eq!(inventory.manifest, "persona.json");
        let value = serde_json::to_value(&release).unwrap();
        for field in value
            .as_object()
            .unwrap()
            .keys()
            .filter(|k| k.as_str() != "signature")
        {
            let mut changed = value.clone();
            match &mut changed[field] {
                Value::String(v) => v.push('x'),
                Value::Number(v) => *v = serde_json::Number::from(v.as_u64().unwrap() + 1),
                Value::Array(v) => v.push(Value::String("tampered".into())),
                Value::Object(v) => {
                    v.insert("tampered".into(), Value::String("1.0.0".into()));
                }
                Value::Null => changed[field] = Value::String("9.0.0".into()),
                _ => panic!(),
            }
            let changed: Release = serde_json::from_value(changed).unwrap();
            assert!(
                changed
                    .verify(&key, "test-key", "examples", false, false, &bytes)
                    .is_err(),
                "unsigned field {field}"
            );
        }
        for (revoked, withdrawn) in [(true, false), (false, true)] {
            assert_eq!(
                release
                    .verify(&key, "test-key", "examples", revoked, withdrawn, &bytes)
                    .unwrap_err()
                    .code,
                "EXTENSION_REVOKED"
            );
        }
        assert!(release
            .verify(&[0; 32], "test-key", "examples", false, false, &bytes)
            .is_err());
        assert!(release
            .verify(&key, "other", "examples", false, false, &bytes)
            .is_err());
        let mut changed = bytes.clone();
        changed[0] ^= 1;
        assert!(release
            .verify(&key, "test-key", "examples", false, false, &changed)
            .is_err());
    }
    #[test]
    fn zip_rejects_traversal_aliases_links_duplicates_and_corrupt_local_headers() {
        for name in [
            "../outside",
            "/absolute",
            "a/../outside",
            "a\\b",
            "C:ads",
            "CON.txt",
            "a./b",
            "a /b",
            "a//b",
            "a\u{0001}b",
            "a\u{0301}.txt",
        ] {
            let bytes = archive(
                &[("persona.json", b"{}"), (name, b"outside")],
                zip::CompressionMethod::Stored,
            );
            assert!(check(&bytes, "persona").is_err(), "unsafe path {name:?}");
        }
        for (one, two) in [
            ("A.txt", "a.txt"),
            ("Straße", "STRASSE"),
            ("ა", "Ა"),
            ("a", "a/b"),
        ] {
            let bytes = archive(
                &[("persona.json", b"{}"), (one, b"1"), (two, b"2")],
                zip::CompressionMethod::Stored,
            );
            assert!(check(&bytes, "persona").is_err());
        }
        let mut duplicate = archive(
            &[("persona.json", b"{}"), ("A.txt", b"1"), ("B.txt", b"2")],
            zip::CompressionMethod::Stored,
        );
        for p in 0..duplicate.len() - 5 {
            if &duplicate[p..p + 5] == b"B.txt" {
                duplicate[p] = b'A';
            }
        }
        assert_eq!(
            check(&duplicate, "persona").unwrap_err().code,
            "EXTENSION_ZIP_DUPLICATE"
        );
        let original = archive(&[("persona.json", b"{}")], zip::CompressionMethod::Stored);
        let central = original
            .windows(4)
            .position(|b| b == b"PK\x01\x02")
            .unwrap();
        let mut link = original.clone();
        link[central + 38..central + 42].copy_from_slice(&(0o120777u32 << 16).to_le_bytes());
        assert!(check(&link, "persona").is_err());
        let mut encrypted = original.clone();
        encrypted[6] |= 1;
        encrypted[central + 8] |= 1;
        assert!(check(&encrypted, "persona").is_err());
        let mut mismatch = original.clone();
        mismatch[30] = b'X';
        assert!(check(&mismatch, "persona").is_err());
        let mut corrupt = original;
        let body = 30 + "persona.json".len();
        corrupt[body] ^= 1;
        assert!(check(&corrupt, "persona").is_err());
    }
    #[test]
    fn compressed_limits_are_inclusive_for_both_categories() {
        for (kind, manifest, limit) in [
            ("theme", "theme.yaml", 5 * 1024 * 1024),
            ("plugin", "plugin.yaml", 10 * 1024 * 1024),
        ] {
            let overhead = archive(
                &[(manifest, b"{}"), ("payload", b"")],
                zip::CompressionMethod::Stored,
            )
            .len();
            for extra in [0, 1] {
                let payload = vec![0; limit - overhead + extra];
                let bytes = archive(
                    &[(manifest, b"{}"), ("payload", &payload)],
                    zip::CompressionMethod::Stored,
                );
                assert_eq!(bytes.len(), limit + extra);
                assert_eq!(check(&bytes, kind).is_ok(), extra == 0);
            }
        }
    }
    #[test]
    fn category_entry_and_expanded_limits_accept_boundary_and_reject_next_byte() {
        for (kind, manifest, limit, count) in [
            ("theme", "theme.yaml", 10 * 1024 * 1024, 100),
            ("plugin", "plugin.yaml", 50 * 1024 * 1024, 2048),
        ] {
            let names: Vec<_> = (1..count + 1).map(|i| format!("entry-{i}")).collect();
            let mut items = vec![(manifest, b"{}".as_slice())];
            items.extend(
                names
                    .iter()
                    .take(count - 1)
                    .map(|s| (s.as_str(), b"".as_slice())),
            );
            assert!(check(&archive(&items, zip::CompressionMethod::Stored), kind).is_ok());
            items.push((names.last().unwrap(), b""));
            assert!(check(&archive(&items, zip::CompressionMethod::Stored), kind).is_err());
            let payload = vec![0; limit - 2];
            let bytes = archive(
                &[(manifest, b"{}"), ("payload", &payload)],
                zip::CompressionMethod::Deflated,
            );
            assert_eq!(check(&bytes, kind).unwrap().expanded_size, limit as u64);
            let payload = vec![0; limit - 1];
            let bytes = archive(
                &[(manifest, b"{}"), ("payload", &payload)],
                zip::CompressionMethod::Deflated,
            );
            assert_eq!(check(&bytes, kind).unwrap_err().code, "EXTENSION_ZIP_LIMIT");
        }
    }
}

//! Bounded immutable output bytes. A manifest is data, never import authority.
#[cfg(windows)]
use crate::experiment_policy::ValidatedLimits;
use crate::workspace::{HostError, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Cursor;

const MAX_PATH: usize = 1024;
const MAX_DEPTH: usize = 16;
const MAX_FILES: usize = 256;
const MAX_FILE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_PIXELS: u64 = 4_194_304;
const MAX_DECODED: usize = 32 * 1024 * 1024;

fn invalid() -> HostError {
    HostError::new("EXPERIMENT_OUTPUT_INVALID")
}
fn exceeded() -> HostError {
    HostError::new("EXPERIMENT_OUTPUT_LIMIT_EXCEEDED")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum OutputKind {
    Text,
    Markdown,
    Json,
    Csv,
    Png,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct OutputManifest {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub kind: OutputKind,
}
#[derive(Debug)]
pub(crate) struct ValidatedOutput {
    manifest: OutputManifest,
    content: Vec<u8>,
}
impl ValidatedOutput {
    pub(crate) fn manifest(&self) -> &OutputManifest {
        &self.manifest
    }
    pub(crate) fn content(&self) -> &[u8] {
        &self.content
    }
    pub(crate) fn restore(manifest: &OutputManifest, content: Vec<u8>, limit: u64) -> Result<Self> {
        let validated = validate(&manifest.path, content, limit)?.ok_or_else(invalid)?;
        if &validated.manifest != manifest {
            return Err(invalid());
        }
        Ok(validated)
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SkippedReason {
    UnsupportedType,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SkippedOutput {
    pub path: String,
    pub bytes: u64,
    pub reason: SkippedReason,
}
#[derive(Debug, Default)]
pub(crate) struct CollectedOutputs {
    pub(crate) files: Vec<ValidatedOutput>,
    pub(crate) skipped: Vec<SkippedOutput>,
    pub(crate) total_bytes: u64,
}

fn valid_component(name: &str) -> bool {
    let stem = name.split('.').next().unwrap_or("").to_ascii_lowercase();
    !name.is_empty()
        && name.len() <= 255
        && name != "."
        && name != ".."
        && !name.ends_with(['.', ' '])
        && !name
            .chars()
            .any(|c| c.is_control() || "\\/:*?\"<>|".contains(c))
        && !matches!(stem.as_str(), "con" | "prn" | "aux" | "nul")
        && !(stem.len() == 4
            && (stem.starts_with("com") || stem.starts_with("lpt"))
            && matches!(stem.as_bytes()[3], b'1'..=b'9'))
        && !["com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"].contains(&stem.as_str())
}
fn valid_path(path: &str) -> bool {
    path.len() <= MAX_PATH
        && path.split('/').count() <= MAX_DEPTH
        && path.split('/').all(valid_component)
}

fn validate(path: &str, content: Vec<u8>, limit: u64) -> Result<Option<ValidatedOutput>> {
    if !valid_path(path) {
        return Err(invalid());
    }
    if content.len() as u64 > limit.min(MAX_FILE_BYTES) {
        return Err(exceeded());
    }
    let suffix = path.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    let kind = match suffix.as_str() {
        "txt" => OutputKind::Text,
        "md" => OutputKind::Markdown,
        "json" => OutputKind::Json,
        "csv" => OutputKind::Csv,
        "png" => OutputKind::Png,
        _ => return Ok(None),
    };
    if kind == OutputKind::Png {
        validate_png(&content)?;
    } else {
        let text = std::str::from_utf8(&content).map_err(|_| invalid())?;
        let text = text.strip_prefix('\u{feff}').unwrap_or(text);
        if text.contains('\0') {
            return Err(invalid());
        }
        if kind == OutputKind::Json {
            serde_json::from_str::<serde_json::Value>(text).map_err(|_| invalid())?;
        }
        if kind == OutputKind::Csv {
            validate_csv(text)?;
        }
    }
    Ok(Some(ValidatedOutput {
        manifest: OutputManifest {
            path: path.into(),
            bytes: content.len() as u64,
            sha256: format!("{:x}", Sha256::digest(&content)),
            kind,
        },
        content,
    }))
}

fn validate_csv(text: &str) -> Result<()> {
    // RFC-style commas/quotes with CRLF and embedded newlines. Preview can be
    // truncated later, but validation never treats a bounded prefix as a file.
    let mut quoted = false;
    let mut closed = false;
    let mut start = true;
    let mut cell_bytes = 0usize;
    let mut fields = 1usize;
    let mut rows = 1usize;
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        cell_bytes += c.len_utf8();
        if cell_bytes > 64 * 1024 {
            return Err(exceeded());
        }
        if quoted {
            if c == '"' {
                if chars.peek() == Some(&'"') {
                    chars.next();
                    cell_bytes += 1;
                    if cell_bytes > 64 * 1024 {
                        return Err(exceeded());
                    }
                } else {
                    quoted = false;
                    closed = true;
                }
            }
            continue;
        }
        if c == ',' {
            fields += 1;
            start = true;
            closed = false;
            cell_bytes = 0;
        } else if c == '\n' || c == '\r' {
            if c == '\r' && chars.peek() == Some(&'\n') {
                chars.next();
            }
            rows += 1;
            fields = 1;
            start = true;
            closed = false;
            cell_bytes = 0;
        } else if c == '"' && start {
            quoted = true;
            start = false;
        } else if c == '"' || closed {
            return Err(invalid());
        } else {
            start = false;
        }
        if fields > 256 || rows > 100_000 {
            return Err(exceeded());
        }
    }
    if quoted {
        return Err(invalid());
    }
    Ok(())
}

fn validate_png(bytes: &[u8]) -> Result<()> {
    if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(invalid());
    }
    let mut offset = 8usize;
    let mut chunks = 0usize;
    let mut ended = false;
    while offset < bytes.len() {
        chunks += 1;
        if chunks > 4096 || offset + 12 > bytes.len() {
            return Err(invalid());
        }
        let length = u32::from_be_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let end = offset
            .checked_add(12)
            .and_then(|n| n.checked_add(length))
            .ok_or_else(invalid)?;
        if end > bytes.len() || ended {
            return Err(invalid());
        }
        let kind = &bytes[offset + 4..offset + 8];
        // Single-image PNG only. No text, ICC/zlib metadata, APNG or unknown
        // chunks reach a WebView image decoder or an imported image.
        if !matches!(
            kind,
            b"IHDR" | b"PLTE" | b"tRNS" | b"IDAT" | b"IEND" | b"sRGB" | b"gAMA" | b"cHRM" | b"pHYs"
        ) {
            return Err(invalid());
        }
        if crc32fast::hash(&bytes[offset + 4..end - 4])
            != u32::from_be_bytes(bytes[end - 4..end].try_into().unwrap())
        {
            return Err(invalid());
        }
        if chunks == 1 {
            if kind != b"IHDR" || length != 13 {
                return Err(invalid());
            }
            let width = u32::from_be_bytes(bytes[offset + 8..offset + 12].try_into().unwrap());
            let height = u32::from_be_bytes(bytes[offset + 12..offset + 16].try_into().unwrap());
            if width == 0
                || height == 0
                || width > 4096
                || height > 4096
                || u64::from(width) * u64::from(height) > MAX_PIXELS
            {
                return Err(exceeded());
            }
        }
        if kind == b"IEND" {
            if length != 0 {
                return Err(invalid());
            }
            ended = true;
        }
        offset = end;
    }
    if !ended {
        return Err(invalid());
    }
    let mut decoder =
        png::Decoder::new_with_limits(Cursor::new(bytes), png::Limits { bytes: MAX_DECODED });
    decoder.ignore_checksums(false);
    decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut reader = decoder.read_info().map_err(|_| invalid())?;
    let size = reader
        .output_buffer_size()
        .filter(|n| *n <= MAX_DECODED)
        .ok_or_else(exceeded)?;
    let mut output = vec![0u8; size];
    reader.next_frame(&mut output).map_err(|_| invalid())?;
    reader.finish().map_err(|_| invalid())?;
    Ok(())
}

#[cfg(windows)]
pub(crate) fn collect(
    profile: &crate::extension_container::Profile,
    limits: &ValidatedLimits,
) -> Result<CollectedOutputs> {
    collect_directory(&profile.folder()?.join("Temp"), limits)
}
#[cfg(windows)]
fn collect_directory(root: &std::path::Path, limits: &ValidatedLimits) -> Result<CollectedOutputs> {
    use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
    use cap_std::fs::{Dir, OpenOptions as CapOptions, OpenOptionsExt};
    use std::{io::Read, os::windows::fs::OpenOptionsExt as StdOptionsExt};
    use windows_sys::Win32::Storage::FileSystem::*;
    let failed = || HostError::new("EXPERIMENT_OUTPUT_INSPECTION_FAILED");
    let file = std::fs::OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(root)
        .map_err(|_| failed())?;
    let metadata = crate::experiment_disk::inspect_object(&file)?;
    if !metadata.is_dir() {
        return Err(failed());
    }
    crate::experiment_disk::reject_named_streams(&file)?;
    let mut pending = vec![(Dir::from_std_file(file), String::new(), 0usize)];
    let mut result = CollectedOutputs::default();
    let mut names = std::collections::BTreeSet::new();
    let mut objects = 1u32;
    let mut options = CapOptions::new();
    options
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .follow(FollowSymlinks::No)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
    while let Some((directory, parent, depth)) = pending.pop() {
        for entry in directory.entries().map_err(|_| failed())? {
            objects += 1;
            if objects > limits.objects() || depth >= MAX_DEPTH {
                return Err(exceeded());
            }
            let entry = entry.map_err(|_| failed())?;
            let name = entry.file_name().into_string().map_err(|_| failed())?;
            if !valid_component(&name) {
                return Err(invalid());
            }
            let path = if parent.is_empty() {
                name
            } else {
                format!("{parent}/{name}")
            };
            if !valid_path(&path) {
                return Err(invalid());
            }
            use unicode_casefold::UnicodeCaseFold;
            use unicode_normalization::UnicodeNormalization;
            if !names.insert(
                path.case_fold()
                    .collect::<String>()
                    .nfc()
                    .collect::<String>(),
            ) {
                return Err(invalid());
            }
            let mut file = entry.open_with(&options).map_err(|_| failed())?.into_std();
            let metadata = crate::experiment_disk::inspect_object(&file)?;
            crate::experiment_disk::reject_named_streams(&file)?;
            if metadata.is_dir() {
                pending.push((Dir::from_std_file(file), path, depth + 1));
                continue;
            }
            if result.files.len() + result.skipped.len() >= MAX_FILES {
                return Err(exceeded());
            }
            result.total_bytes = result
                .total_bytes
                .checked_add(metadata.len())
                .ok_or_else(exceeded)?;
            if result.total_bytes > limits.output_bytes() || metadata.len() > MAX_FILE_BYTES {
                return Err(exceeded());
            }
            let mut content = Vec::with_capacity(metadata.len() as usize);
            file.by_ref()
                .take(metadata.len() + 1)
                .read_to_end(&mut content)
                .map_err(|_| failed())?;
            if content.len() as u64 != metadata.len() {
                return Err(failed());
            }
            match validate(&path, content, limits.output_bytes())? {
                Some(output) => result.files.push(output),
                None => result.skipped.push(SkippedOutput {
                    path,
                    bytes: metadata.len(),
                    reason: SkippedReason::UnsupportedType,
                }),
            }
        }
    }
    result
        .files
        .sort_by(|a, b| a.manifest.path.cmp(&b.manifest.path));
    result.skipped.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn png_bytes() -> Vec<u8> {
        let mut output = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut output, 2, 1);
            encoder.set_color(png::ColorType::Rgb);
            encoder.set_depth(png::BitDepth::Eight);
            let mut writer = encoder.write_header().unwrap();
            writer.write_image_data(&[255, 0, 0, 0, 255, 0]).unwrap();
        }
        output
    }
    fn chunk(kind: &[u8; 4], content: &[u8]) -> Vec<u8> {
        let mut bytes = (content.len() as u32).to_be_bytes().to_vec();
        bytes.extend_from_slice(kind);
        bytes.extend_from_slice(content);
        bytes.extend_from_slice(&crc32fast::hash(&bytes[4..]).to_be_bytes());
        bytes
    }
    #[test]
    fn validates_complete_text_structured_data_and_immutable_provenance() {
        for (name, content) in [
            ("结果/report.md", "# 中文\r\nresult"),
            (
                "data.csv",
                "name,value\r\n\"中文,引号\",7\r\n\"multi\nline\",11",
            ),
            ("data.json", "{\"value\":18}"),
            ("output.txt", "中文"),
        ] {
            let value = validate(name, content.as_bytes().to_vec(), 1024)
                .unwrap()
                .unwrap();
            assert_eq!(value.content(), content.as_bytes());
            assert!(
                ValidatedOutput::restore(value.manifest(), value.content.clone(), 1024).is_ok()
            );
            assert!(ValidatedOutput::restore(value.manifest(), b"changed".to_vec(), 1024).is_err());
        }
        for (name, content) in [
            ("x.csv", b"\"unclosed".as_slice()),
            ("x.csv", b"a\"b"),
            ("x.json", b"{} garbage"),
            ("x.md", b"bad\xff"),
            ("x.txt", b"a\0b"),
        ] {
            assert!(validate(name, content.to_vec(), 1024).is_err());
        }
        for name in [
            "../report.md",
            "CON.txt",
            "a/b:stream.txt",
            "/root.txt",
            "a\\b.txt",
            "trailing./x.txt",
        ] {
            assert!(validate(name, b"x".to_vec(), 1024).is_err());
        }
        assert!(
            validate("page.html", b"<script>bad()</script>".to_vec(), 1024)
                .unwrap()
                .is_none()
        );
        assert!(validate("x.txt", vec![0; 1025], 1024).is_err());
    }
    #[test]
    fn rejects_png_bombs_metadata_corruption_and_invalid_compression() {
        let valid = png_bytes();
        assert!(validate_png(&valid).is_ok());
        let mut damaged = valid.clone();
        damaged[20] ^= 1;
        assert!(validate_png(&damaged).is_err());
        let mut bomb = valid.clone();
        bomb[16..20].copy_from_slice(&u32::MAX.to_be_bytes());
        let crc = crc32fast::hash(&bomb[12..29]);
        bomb[29..33].copy_from_slice(&crc.to_be_bytes());
        assert!(validate_png(&bomb).is_err());
        let mut metadata = valid[..33].to_vec();
        metadata.extend(chunk(b"zTXt", b"compressed text"));
        metadata.extend_from_slice(&valid[33..]);
        assert!(validate_png(&metadata).is_err());
        let mut compressed = valid[..33].to_vec();
        compressed.extend(chunk(b"IDAT", b"not zlib"));
        compressed.extend(chunk(b"IEND", b""));
        assert!(validate_png(&compressed).is_err());
        let mut trailing = valid.clone();
        trailing.push(0);
        assert!(validate_png(&trailing).is_err());
        assert!(validate_png(&valid[..valid.len() - 1]).is_err());
    }
    #[test]
    #[cfg(windows)]
    fn collects_pinned_snapshots_and_rejects_hidden_or_external_content() {
        let root = tempfile::tempdir().unwrap();
        let limits = crate::experiment_policy::ExecutionLimits {
            output_mib: 1,
            ..Default::default()
        }
        .validate()
        .unwrap();
        std::fs::create_dir(root.path().join("结果")).unwrap();
        std::fs::write(root.path().join("结果/report.md"), "# 中文\r\n").unwrap();
        std::fs::write(root.path().join("image.png"), png_bytes()).unwrap();
        std::fs::write(root.path().join("blocked.html"), b"<script>x</script>").unwrap();
        let result = collect_directory(root.path(), &limits).unwrap();
        assert_eq!(result.files.len(), 2);
        assert_eq!(result.skipped.len(), 1);
        assert_eq!(result.skipped[0].reason, SkippedReason::UnsupportedType);
        std::fs::write(root.path().join("结果/report.md"), b"updated").unwrap();
        assert_eq!(result.files[1].content(), "# 中文\r\n".as_bytes());
        std::fs::write(root.path().join("image.png:hidden"), b"hidden").unwrap();
        assert!(collect_directory(root.path(), &limits).is_err());
        std::fs::remove_file(root.path().join("image.png:hidden")).unwrap();
        std::fs::write(root.path().join("结果:hidden"), b"directory stream").unwrap();
        assert!(collect_directory(root.path(), &limits).is_err());
        std::fs::remove_file(root.path().join("结果:hidden")).unwrap();
        let outside = tempfile::tempdir().unwrap();
        std::fs::write(outside.path().join("sentinel.txt"), b"unchanged").unwrap();
        std::fs::hard_link(
            outside.path().join("sentinel.txt"),
            root.path().join("link.txt"),
        )
        .unwrap();
        assert!(collect_directory(root.path(), &limits).is_err());
        std::fs::remove_file(root.path().join("link.txt")).unwrap();
        let command = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(root.path().join("junction"))
            .arg(outside.path())
            .output()
            .unwrap();
        assert!(command.status.success());
        assert!(collect_directory(root.path(), &limits).is_err());
        std::fs::remove_dir(root.path().join("junction")).unwrap();
        std::fs::write(root.path().join("large.bin"), vec![0; 1024 * 1024 + 1]).unwrap();
        assert_eq!(
            collect_directory(root.path(), &limits).unwrap_err().code,
            "EXPERIMENT_OUTPUT_LIMIT_EXCEEDED"
        );
        assert_eq!(
            std::fs::read(outside.path().join("sentinel.txt")).unwrap(),
            b"unchanged"
        );
    }
    #[test]
    #[cfg(windows)]
    fn inspects_outputs_while_filesystem_guards_are_alive_and_bounds_object_floods() {
        let profile = crate::extension_container::Profile::create().unwrap();
        let folder = profile.folder().unwrap();
        let scratch = folder.join("Temp");
        std::fs::create_dir_all(&scratch).unwrap();
        let guard = crate::experiment_filesystem::Filesystem::restrict(&profile, &scratch).unwrap();
        std::fs::write(scratch.join("data.csv"), b"a,b\r\n1,2").unwrap();
        let limits = crate::experiment_policy::ExecutionLimits {
            objects: 32,
            ..Default::default()
        }
        .validate()
        .unwrap();
        let actual = collect(&profile, &limits).unwrap();
        assert_eq!(actual.files.len(), 1);
        assert_eq!(actual.files[0].content(), b"a,b\r\n1,2");
        for index in 0..32 {
            std::fs::create_dir(scratch.join(format!("empty-{index}"))).unwrap();
        }
        assert_eq!(
            collect(&profile, &limits).unwrap_err().code,
            "EXPERIMENT_OUTPUT_LIMIT_EXCEEDED"
        );
        drop(guard);
        let root = folder.parent().unwrap();
        profile.remove().unwrap();
        assert!(!root.exists());
    }
}

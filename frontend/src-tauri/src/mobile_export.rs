//! Frozen portable content exports. No draft, database, history or credential backup.
use crate::workspace::{HostError, Result, Workspace};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use uuid::Uuid;
use zip::write::SimpleFileOptions;

const MAX_FILES: usize = 2000;
const MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_CONTENT_BYTES: u64 = 256 * 1024 * 1024;
const MAX_ARCHIVE_BYTES: u64 = MAX_CONTENT_BYTES + 8 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ExportReceipt {
    pub ticket: String,
    pub filename: String,
    pub sha256: String,
    pub size: u64,
    pub file_count: usize,
    pub content_bytes: u64,
}

fn invalid() -> HostError {
    HostError::new("MOBILE_EXPORT_INVALID")
}
fn zip_error(_: zip::result::ZipError) -> HostError {
    HostError::new("MOBILE_EXPORT_FAILED")
}

fn directory(private_root: &Path, create: bool) -> Result<PathBuf> {
    let path = private_root.join("exports");
    if path.exists() {
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err(HostError::new("UNSAFE_PATH"));
        }
    } else if create {
        fs::create_dir(&path)?;
    } else {
        return Err(HostError::new("FILE_NOT_FOUND"));
    }
    Ok(path)
}

pub fn export_path(private_root: &Path, ticket: &str) -> Result<PathBuf> {
    if Uuid::parse_str(ticket).map_err(|_| invalid())?.to_string() != ticket {
        return Err(invalid());
    }
    let path = directory(private_root, false)?.join(format!("{ticket}.zip"));
    let metadata = fs::symlink_metadata(&path)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() > MAX_ARCHIVE_BYTES
    {
        return Err(invalid());
    }
    Ok(path)
}

fn file_hash(path: &Path) -> Result<(String, u64)> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut bytes = 0;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        bytes += read as u64;
        if bytes > MAX_ARCHIVE_BYTES {
            return Err(invalid());
        }
        hasher.update(&buffer[..read]);
    }
    Ok((format!("{:x}", hasher.finalize()), bytes))
}

pub fn verify_export(private_root: &Path, ticket: &str) -> Result<ExportReceipt> {
    let path = export_path(private_root, ticket)?;
    let metadata = path.with_extension("json");
    if fs::symlink_metadata(&metadata)?.file_type().is_symlink()
        || fs::metadata(&metadata)?.len() > 4096
    {
        return Err(invalid());
    }
    let receipt: ExportReceipt =
        serde_json::from_slice(&fs::read(metadata)?).map_err(|_| invalid())?;
    if receipt.ticket != ticket
        || receipt.filename != format!("OpenNexus-content-{ticket}.zip")
        || receipt.file_count > MAX_FILES
        || receipt.content_bytes > MAX_CONTENT_BYTES
    {
        return Err(invalid());
    }
    let (sha, size) = file_hash(&path)?;
    if sha != receipt.sha256 || size != receipt.size {
        return Err(HostError::new("MOBILE_EXPORT_CHANGED"));
    }
    Ok(receipt)
}

fn included(path: &str) -> bool {
    !path
        .split('/')
        .any(|part| part.starts_with('.') || part.eq_ignore_ascii_case("opennexus-records"))
        && path.rsplit('.').next().is_some_and(|extension| {
            ["md", "canvas", "png", "jpg", "jpeg", "gif", "webp"]
                .contains(&extension.to_ascii_lowercase().as_str())
        })
}

/// The engine mutex binds the complete snapshot to the native-owned Vault.
pub fn prepare_export(ws: &mut Workspace, private_root: &Path) -> Result<ExportReceipt> {
    let mut entries = ws.tree()?;
    entries.retain(|entry| !entry.deleted && !entry.is_folder && included(&entry.path));
    entries.sort_by(|a, b| a.path.cmp(&b.path));
    if entries.len() > MAX_FILES {
        return Err(HostError::new("MOBILE_EXPORT_TOO_LARGE"));
    }
    let root = directory(private_root, true)?;
    let ticket = Uuid::new_v4().to_string();
    let path = root.join(format!("{ticket}.zip"));
    // Only this call creates this ticket. A missing metadata file can never confirm a partial ZIP.
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    let result = (|| {
        let mut writer = zip::ZipWriter::new(file);
        let mut manifest = Vec::new();
        let options = SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated)
            .unix_permissions(0o600);
        let mut total = 0;
        for entry in &entries {
            let mut input = File::open(ws.resolve(&entry.path)?)?;
            if input.metadata()?.len() > MAX_FILE_BYTES {
                return Err(HostError::new("MOBILE_EXPORT_TOO_LARGE"));
            }
            writer
                .start_file(format!("vault/{}", entry.path), options)
                .map_err(zip_error)?;
            let mut digest = Sha256::new();
            let mut size = 0;
            let mut buffer = [0u8; 64 * 1024];
            loop {
                let read = input.read(&mut buffer)?;
                if read == 0 {
                    break;
                }
                size += read as u64;
                total += read as u64;
                if size > MAX_FILE_BYTES || total > MAX_CONTENT_BYTES {
                    return Err(HostError::new("MOBILE_EXPORT_TOO_LARGE"));
                }
                writer.write_all(&buffer[..read])?;
                digest.update(&buffer[..read]);
            }
            let sha = format!("{:x}", digest.finalize());
            if sha != entry.hash {
                return Err(HostError::new("REVISION_CONFLICT"));
            }
            manifest.push(
                serde_json::json!({"path":format!("vault/{}",entry.path),"sha256":sha,"size":size}),
            );
        }
        writer
            .start_file("OpenNexus-export.json", options)
            .map_err(zip_error)?;
        let bytes = serde_json::to_vec_pretty(&serde_json::json!({"format":"opennexus-content-export","schema":1,"scope":"markdown-canvas-images","files":manifest})).map_err(|_| invalid())?;
        writer.write_all(&bytes)?;
        writer.finish().map_err(zip_error)?.sync_all()?;
        let (sha256, size) = file_hash(&path)?;
        let receipt = ExportReceipt {
            ticket: ticket.clone(),
            filename: format!("OpenNexus-content-{ticket}.zip"),
            sha256,
            size,
            file_count: entries.len(),
            content_bytes: total,
        };
        let mut metadata = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path.with_extension("json"))?;
        metadata.write_all(&serde_json::to_vec(&receipt).map_err(|_| invalid())?)?;
        metadata.sync_all()?;
        Ok(receipt)
    })();
    if result.is_err() {
        // Exact newly owned ticket only; neither Vault content nor prior exports are removed.
        let _ = fs::remove_file(&path);
        let _ = fs::remove_file(path.with_extension("json"));
    }
    result
}

/// Lists only native-sealed metadata. An interrupted preparation is never shown as saved.
pub fn list_exports(private_root: &Path, offset: usize, limit: usize) -> Result<serde_json::Value> {
    if !(1..=100).contains(&limit) {
        return Err(HostError::new("MOBILE_PAGE_INVALID"));
    }
    let root = match directory(private_root, false) {
        Ok(root) => root,
        Err(error) if error.code == "FILE_NOT_FOUND" => {
            return Ok(serde_json::json!({"items":[],"total":0}))
        }
        Err(error) => return Err(error),
    };
    let mut receipts = Vec::new();
    for item in fs::read_dir(root)? {
        let item = item?;
        let path = item.path();
        if path.extension().is_none_or(|extension| extension != "json")
            || item.file_type()?.is_symlink()
            || item.metadata()?.len() > 4096
        {
            continue;
        }
        let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        if Uuid::parse_str(stem).is_err() {
            continue;
        }
        let receipt: ExportReceipt = match serde_json::from_slice(&fs::read(&path)?) {
            Ok(receipt) => receipt,
            Err(_) => continue,
        };
        if receipt.ticket != stem || receipt.filename != format!("OpenNexus-content-{stem}.zip") {
            continue;
        }
        receipts.push(receipt);
    }
    receipts.sort_by(|a, b| a.ticket.cmp(&b.ticket));
    Ok(
        serde_json::json!({"total":receipts.len(),"items":receipts.into_iter().skip(offset).take(limit).collect::<Vec<_>>()}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mobile_workspace::MobileWorkspace;

    #[test]
    fn mobile_export_keeps_markdown_canvas_image_bytes_paths_and_excludes_private_state() {
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        let text = "# 中文\r\n![图](../attachments/aa/pixel.png)\r\n😀\n";
        let canvas = b"{\"nodes\":[],\"edges\":[],\"future\":{\"kept\":true}}\r\n";
        let image = b"\x89PNG\r\n\x1a\noriginal-image-bytes";
        engine
            .workspace
            .write("notes/a.md", "", text.as_bytes(), "local")
            .unwrap();
        engine
            .workspace
            .write("boards/a.canvas", "", canvas, "local")
            .unwrap();
        engine
            .workspace
            .write("attachments/aa/pixel.png", "", image, "local")
            .unwrap();
        engine
            .drafts
            .create_with_content(&engine.workspace, "uncommitted.md", "private unsaved text")
            .unwrap();
        let before: i64 = engine
            .workspace
            .db
            .query_row("SELECT COUNT(*) FROM outbox", [], |r| r.get(0))
            .unwrap();
        let receipt = prepare_export(&mut engine.workspace, temp.path()).unwrap();
        assert_eq!(receipt.file_count, 3);
        assert_eq!(
            verify_export(temp.path(), &receipt.ticket).unwrap(),
            receipt
        );
        let mut archive = zip::ZipArchive::new(
            File::open(export_path(temp.path(), &receipt.ticket).unwrap()).unwrap(),
        )
        .unwrap();
        assert_eq!(archive.len(), 4);
        for (path, expected) in [
            ("vault/notes/a.md", text.as_bytes()),
            ("vault/boards/a.canvas", canvas.as_slice()),
            ("vault/attachments/aa/pixel.png", image.as_slice()),
        ] {
            let mut actual = Vec::new();
            archive
                .by_name(path)
                .unwrap()
                .read_to_end(&mut actual)
                .unwrap();
            assert_eq!(actual, expected);
        }
        let mut metadata = String::new();
        archive
            .by_name("OpenNexus-export.json")
            .unwrap()
            .read_to_string(&mut metadata)
            .unwrap();
        assert!(!metadata.contains(".ainote"));
        assert!(!metadata.contains("private unsaved text"));
        assert!(!metadata.contains("sqlite"));
        let manifest: serde_json::Value = serde_json::from_str(&metadata).unwrap();
        assert_eq!(manifest["files"].as_array().unwrap().len(), 3);
        let after: i64 = engine
            .workspace
            .db
            .query_row("SELECT COUNT(*) FROM outbox", [], |r| r.get(0))
            .unwrap();
        assert_eq!(before, after);
        assert_eq!(list_exports(temp.path(), 0, 20).unwrap()["total"], 1);
    }

    #[test]
    fn mobile_export_read_requires_an_original_sealed_ticket_and_unchanged_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        engine
            .workspace
            .write("a.md", "", b"saved", "local")
            .unwrap();
        let first = prepare_export(&mut engine.workspace, temp.path()).unwrap();
        let original = fs::read(export_path(temp.path(), &first.ticket).unwrap()).unwrap();
        engine
            .workspace
            .write("a.md", &crate::workspace::hash(b"saved"), b"newer", "local")
            .unwrap();
        let second = prepare_export(&mut engine.workspace, temp.path()).unwrap();
        assert_ne!(first.ticket, second.ticket);
        assert_eq!(verify_export(temp.path(), &first.ticket).unwrap(), first);
        assert_eq!(
            fs::read(export_path(temp.path(), &first.ticket).unwrap()).unwrap(),
            original
        );
        assert!(export_path(temp.path(), "../vault/a.md").is_err());
        assert!(verify_export(temp.path(), &Uuid::new_v4().to_string()).is_err());
        fs::write(
            export_path(temp.path(), &second.ticket).unwrap(),
            b"tampered",
        )
        .unwrap();
        assert_eq!(
            verify_export(temp.path(), &second.ticket).unwrap_err().code,
            "MOBILE_EXPORT_CHANGED"
        );
    }
}

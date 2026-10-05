//! Bounded immutable inputs for a future one-time run confirmation. No launch.
use crate::{
    experiment_contract::is_experiment_file,
    experiment_policy::{ExecutionLimits, ValidatedLimits},
    experiment_runtime::RUNTIME_ID,
    workspace::{hash, Entry, HostError, Result, Workspace},
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
use cap_std::fs::{Dir, OpenOptions};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Read,
};

pub const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;
pub const MAX_TOTAL_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_FILES: usize = 32;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelectedFile {
    pub file_id: String,
    pub path: String,
    pub hash: String,
    pub revision: i64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunRequest {
    pub vault_id: String,
    pub operation_id: String,
    pub runtime_id: String,
    pub entry: SelectedFile,
    pub inputs: Vec<SelectedFile>,
    pub limits: ExecutionLimits,
}
pub struct ValidatedRequest {
    request: RunRequest,
    limits: ValidatedLimits,
}
fn invalid() -> HostError {
    HostError::new("EXPERIMENT_REQUEST_INVALID")
}
fn changed() -> HostError {
    HostError::new("EXPERIMENT_INPUT_CHANGED")
}
fn unsafe_file() -> HostError {
    HostError::new("EXPERIMENT_INPUT_UNSAFE")
}
fn valid_id(value: &str) -> bool {
    uuid::Uuid::parse_str(value).is_ok_and(|id| !id.is_nil() && id.to_string() == value)
}
fn valid_path(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 1024
        && is_experiment_file(value)
        && value.split('/').all(|part| {
            let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
            let device = matches!(
                stem.as_str(),
                "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
            ) || ["COM", "LPT"].iter().any(|prefix| {
                stem.strip_prefix(prefix).is_some_and(|end| {
                    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"].contains(&end)
                })
            });
            !part.is_empty()
                && part != "."
                && part != ".."
                && !part.ends_with(['.', ' '])
                && part.encode_utf16().count() <= 255
                && !device
                && ![".ainote", ".git", "opennexus-records"]
                    .iter()
                    .any(|name| part.eq_ignore_ascii_case(name))
                && !part
                    .chars()
                    .any(|c| c.is_control() || "\\:*?\"<>|".contains(c))
        })
}
impl RunRequest {
    pub fn validate(&self) -> Result<ValidatedRequest> {
        if !valid_id(&self.vault_id)
            || !valid_id(&self.operation_id)
            || self.runtime_id != RUNTIME_ID
            || self.inputs.len() >= MAX_FILES
            || !self.entry.path.to_ascii_lowercase().ends_with(".py")
        {
            return Err(invalid());
        }
        let mut ids = BTreeSet::new();
        let mut paths = BTreeSet::new();
        for file in std::iter::once(&self.entry).chain(&self.inputs) {
            if !valid_id(&file.file_id)
                || !valid_path(&file.path)
                || file.revision <= 0
                || file.hash.len() != 64
                || !file
                    .hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                || !ids.insert(&file.file_id)
                || !paths.insert(file.path.to_lowercase())
            {
                return Err(invalid());
            }
        }
        let limits = self.limits.validate()?;
        let mut request = self.clone();
        request.inputs.sort_by(|a, b| a.path.cmp(&b.path));
        Ok(ValidatedRequest { request, limits })
    }
}
impl ValidatedRequest {
    pub fn request(&self) -> &RunRequest {
        &self.request
    }
    pub fn limits(&self) -> &ValidatedLimits {
        &self.limits
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InputSummary {
    pub request: RunRequest,
    pub fingerprint: String,
    pub sizes: BTreeMap<String, usize>,
    pub total_bytes: usize,
}
impl InputSummary {
    pub(crate) fn validate(&self) -> Result<ValidatedRequest> {
        let request = self.request.validate()?;
        if self.fingerprint != fingerprint(request.request())?
            || self.sizes.len() != self.request.inputs.len() + 1
            || self
                .sizes
                .values()
                .any(|size| *size > MAX_FILE_BYTES as usize)
            || self.total_bytes != self.sizes.values().sum::<usize>()
            || self.total_bytes > MAX_TOTAL_BYTES
            || std::iter::once(&self.request.entry)
                .chain(&self.request.inputs)
                .any(|file| !self.sizes.contains_key(&file.path))
        {
            return Err(HostError::new("EXPERIMENT_RECORD_CORRUPT"));
        }
        Ok(request)
    }
}
fn fingerprint(request: &RunRequest) -> Result<String> {
    let mut binding = b"opennexus-experiment-inputs-v1\0".to_vec();
    binding.extend(serde_json::to_vec(request).map_err(|_| invalid())?);
    Ok(hash(&binding))
}
/// Only Host-read, hash-checked bytes can construct this owner. Client metadata
/// alone cannot construct an approved snapshot or modify bytes after review.
pub struct PreparedInputs {
    summary: InputSummary,
    bytes: BTreeMap<String, Vec<u8>>,
}
impl PreparedInputs {
    pub fn prepare(ws: &mut Workspace, request: &ValidatedRequest) -> Result<Self> {
        if ws.vault_id != request.request.vault_id {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        let root = open_root(&ws.root)?;
        let entries: BTreeMap<_, _> = ws
            .scan()?
            .into_iter()
            .map(|entry| (entry.file_id.clone(), entry))
            .collect();
        let mut bytes = BTreeMap::new();
        let mut sizes = BTreeMap::new();
        let mut total_bytes = 0;
        for selected in std::iter::once(&request.request.entry).chain(&request.request.inputs) {
            let entry = entries.get(&selected.file_id).ok_or_else(changed)?;
            match_selection(entry, selected)?;
            let content = read_selected(&root, &selected.path)?;
            if hash(&content) != selected.hash {
                return Err(changed());
            }
            if std::str::from_utf8(&content).is_err() {
                return Err(HostError::new("EXPERIMENT_INPUT_NOT_UTF8"));
            }
            total_bytes += content.len();
            if total_bytes > MAX_TOTAL_BYTES {
                return Err(HostError::new("EXPERIMENT_INPUT_TOO_LARGE"));
            }
            sizes.insert(selected.path.clone(), content.len());
            bytes.insert(selected.path.clone(), content);
        }
        // Bind the exact entry, sorted input identities/revisions/hashes, vault,
        // runtime, limits and operation. This digest is not execution authority.
        Ok(Self {
            summary: InputSummary {
                request: request.request.clone(),
                fingerprint: fingerprint(&request.request)?,
                sizes,
                total_bytes,
            },
            bytes,
        })
    }
    pub fn summary(&self) -> &InputSummary {
        &self.summary
    }
    pub fn files(&self) -> impl Iterator<Item = (&str, &[u8])> {
        self.bytes
            .iter()
            .map(|(path, bytes)| (path.as_str(), bytes.as_slice()))
    }
    /// Restore only from Host storage; every byte is rechecked. A summary alone
    /// cannot reconstruct this owner, and restoring it does not authorize a run.
    pub(crate) fn restore(summary: InputSummary, bytes: BTreeMap<String, Vec<u8>>) -> Result<Self> {
        summary.validate()?;
        if bytes.len() != summary.sizes.len()
            || std::iter::once(&summary.request.entry)
                .chain(&summary.request.inputs)
                .any(|file| {
                    bytes.get(&file.path).is_none_or(|bytes| {
                        bytes.len() != summary.sizes[&file.path]
                            || hash(bytes) != file.hash
                            || std::str::from_utf8(bytes).is_err()
                    })
                })
        {
            return Err(HostError::new("EXPERIMENT_RECORD_CORRUPT"));
        }
        Ok(Self { summary, bytes })
    }
}
fn match_selection(entry: &Entry, selected: &SelectedFile) -> Result<()> {
    if entry.deleted
        || entry.is_folder
        || entry.path != selected.path
        || entry.hash != selected.hash
        || entry.revision != selected.revision
    {
        return Err(changed());
    }
    Ok(())
}
fn options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options
        .read(true)
        .follow(FollowSymlinks::No)
        .maybe_dir(true);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        // A replaced FIFO must not block before we inspect the opened object.
        options.custom_flags(libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.share_mode(windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ);
    }
    options
}
fn checked(file: &std::fs::File, directory: bool) -> Result<()> {
    let metadata = file.metadata().map_err(|_| unsafe_file())?;
    if metadata.is_dir() != directory || (!directory && !metadata.is_file()) {
        return Err(unsafe_file());
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::*;
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || (!directory && info.nNumberOfLinks != 1)
        {
            return Err(unsafe_file());
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if !directory && metadata.nlink() != 1 {
            return Err(unsafe_file());
        }
    }
    Ok(())
}
fn open_root(path: &std::path::Path) -> Result<Dir> {
    // The root is captured by Host Workspace, never from a renderer parameter.
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::*;
        let file = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .map_err(|_| unsafe_file())?;
        checked(&file, true)?;
        Ok(Dir::from_std_file(file))
    }
    #[cfg(not(windows))]
    {
        // Open the final component relative to an already captured parent,
        // rejecting a symlink even if it replaces the original workspace root.
        let parent = path.parent().ok_or_else(unsafe_file)?;
        let name = path.file_name().ok_or_else(unsafe_file)?;
        let parent = Dir::open_ambient_dir(parent, cap_std::ambient_authority())
            .map_err(|_| unsafe_file())?;
        let file = parent
            .open_with(name, &options())
            .map_err(|_| unsafe_file())?
            .into_std();
        checked(&file, true)?;
        Ok(Dir::from_std_file(file))
    }
}
fn read_selected(root: &Dir, path: &str) -> Result<Vec<u8>> {
    let parts: Vec<_> = path.split('/').collect();
    let mut parents = vec![root.try_clone().map_err(|_| unsafe_file())?];
    for part in &parts[..parts.len() - 1] {
        let file = parents
            .last()
            .unwrap()
            .open_with(part, &options())
            .map_err(|_| unsafe_file())?
            .into_std();
        checked(&file, true)?;
        parents.push(Dir::from_std_file(file));
    }
    let mut file = parents
        .last()
        .unwrap()
        .open_with(parts.last().unwrap(), &options())
        .map_err(|_| unsafe_file())?
        .into_std();
    checked(&file, false)?;
    let size = file.metadata().map_err(|_| unsafe_file())?.len();
    if size > MAX_FILE_BYTES {
        return Err(HostError::new("EXPERIMENT_INPUT_TOO_LARGE"));
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_FILE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| unsafe_file())?;
    if bytes.len() > MAX_FILE_BYTES as usize {
        return Err(HostError::new("EXPERIMENT_INPUT_TOO_LARGE"));
    }
    if bytes.len() as u64 != size {
        return Err(changed());
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    fn selection(entry: Entry) -> SelectedFile {
        SelectedFile {
            file_id: entry.file_id,
            path: entry.path,
            hash: entry.hash,
            revision: entry.revision,
        }
    }
    fn setup() -> (tempfile::TempDir, Workspace, RunRequest) {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("experiments")).unwrap();
        fs::write(
            root.path().join("experiments/中文 #%.py"),
            "# 中文\r\nprint('计算')\r\n",
        )
        .unwrap();
        fs::write(
            root.path().join("experiments/a.csv"),
            "name,value\r\n中文,7\r\n",
        )
        .unwrap();
        fs::write(root.path().join("experiments/b.json"), "{\"value\":11}").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let request = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry: selection(ws.read("experiments/中文 #%.py").unwrap().entry),
            inputs: vec![
                selection(ws.read("experiments/a.csv").unwrap().entry),
                selection(ws.read("experiments/b.json").unwrap().entry),
            ],
            limits: ExecutionLimits::default(),
        };
        (root, ws, request)
    }
    #[test]
    fn rejects_unstructured_launch_and_unsafe_selectors() {
        let (_root, _ws, request) = setup();
        assert!(request.validate().is_ok());
        for path in [
            "../main.py",
            "experiments/../main.py",
            "experiments//main.py",
            "experiments/CON.py",
            "experiments/COM¹.py",
            "experiments/dir./main.py",
            "experiments/x.py:stream",
            "experiments\\x.py",
            "experiments/.ainote/main.py",
            "experiments/.git/main.py",
            "experiments/opennexus-records/x.py",
            "experiments/x.json",
            "experiments/x\n.py",
            "settings.py",
        ] {
            let mut p = request.clone();
            p.entry.path = path.into();
            assert!(p.validate().is_err(), "accepted {path:?}");
        }
        for field in ["command", "shell", "environment", "executable", "network"] {
            let mut p = serde_json::to_value(&request).unwrap();
            p[field] = serde_json::json!(true);
            assert!(serde_json::from_value::<RunRequest>(p).is_err());
        }
        let mut p = request.clone();
        p.runtime_id = "user-python".into();
        assert!(p.validate().is_err());
        let mut p = request.clone();
        p.entry.hash = "g".repeat(64);
        assert!(p.validate().is_err());
        let mut p = request.clone();
        p.entry.revision = 0;
        assert!(p.validate().is_err());
        let mut p = request.clone();
        p.inputs.push(p.entry.clone());
        assert!(p.validate().is_err());
        let mut p = request.clone();
        p.inputs[0].path = p.entry.path.to_uppercase();
        assert!(p.validate().is_err());
        let mut p = request.clone();
        p.inputs = vec![p.inputs[0].clone(); MAX_FILES];
        assert!(p.validate().is_err());
        let mut p = request;
        p.limits.processes = 999;
        assert!(p.validate().is_err());
    }
    #[test]
    fn binds_exact_bytes_and_metadata_independently_of_input_order() {
        let (root, mut ws, mut request) = setup();
        let first = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        let contents: BTreeMap<_, _> = first.files().collect();
        assert_eq!(
            contents["experiments/中文 #%.py"],
            "# 中文\r\nprint('计算')\r\n".as_bytes()
        );
        assert_eq!(
            contents["experiments/a.csv"],
            "name,value\r\n中文,7\r\n".as_bytes()
        );
        assert_eq!(
            first.summary.total_bytes,
            contents.values().map(|bytes| bytes.len()).sum::<usize>()
        );
        request.inputs.reverse();
        let reordered = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        assert_eq!(first.summary.fingerprint, reordered.summary.fingerprint);
        request.limits.wall_seconds += 1;
        let changed_limits =
            PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        assert_ne!(
            first.summary.fingerprint,
            changed_limits.summary.fingerprint
        );
        request.operation_id = uuid::Uuid::new_v4().to_string();
        let new_run = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        assert_ne!(
            new_run.summary.fingerprint,
            changed_limits.summary.fingerprint
        );
        fs::write(root.path().join(&request.entry.path), "changed").unwrap();
        assert_eq!(
            PreparedInputs::prepare(&mut ws, &request.validate().unwrap())
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_CHANGED"
        );
        assert_eq!(
            first
                .files()
                .find(|(path, _)| *path == request.entry.path)
                .unwrap()
                .1,
            "# 中文\r\nprint('计算')\r\n".as_bytes()
        );
    }
    #[test]
    fn rejects_other_vault_stale_path_revision_or_hash_and_non_utf8() {
        let (root, mut ws, request) = setup();
        let mut p = request.clone();
        p.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            PreparedInputs::prepare(&mut ws, &p.validate().unwrap())
                .err()
                .unwrap()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        for p in [
            {
                let mut p = request.clone();
                p.entry.hash = "a".repeat(64);
                p
            },
            {
                let mut p = request.clone();
                p.entry.revision += 1;
                p
            },
            {
                let mut p = request.clone();
                p.entry.path = "experiments/moved.py".into();
                p
            },
            {
                let mut p = request.clone();
                p.entry.file_id = uuid::Uuid::new_v4().to_string();
                p
            },
        ] {
            assert_eq!(
                PreparedInputs::prepare(&mut ws, &p.validate().unwrap())
                    .err()
                    .unwrap()
                    .code,
                "EXPERIMENT_INPUT_CHANGED"
            );
        }
        fs::write(root.path().join(&request.inputs[0].path), [0xff]).unwrap();
        let entries = ws.scan().unwrap();
        let mut p = request;
        p.inputs[0] = selection(
            entries
                .into_iter()
                .find(|entry| entry.path == p.inputs[0].path)
                .unwrap(),
        );
        assert_eq!(
            PreparedInputs::prepare(&mut ws, &p.validate().unwrap())
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_NOT_UTF8"
        );
    }
    #[test]
    fn bounds_individual_and_total_snapshot_bytes_and_rejects_hardlinks() {
        let (root, mut ws, mut request) = setup();
        let directory = open_root(&ws.root).unwrap();
        fs::write(
            root.path().join("experiments/too-large.json"),
            vec![b'x'; MAX_FILE_BYTES as usize + 1],
        )
        .unwrap();
        assert_eq!(
            read_selected(&directory, "experiments/too-large.json")
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_TOO_LARGE"
        );
        fs::remove_file(root.path().join("experiments/too-large.json")).unwrap();
        fs::hard_link(
            root.path().join(&request.entry.path),
            root.path().join("experiments/alias.py"),
        )
        .unwrap();
        assert_eq!(
            read_selected(&directory, &request.entry.path)
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_UNSAFE"
        );
        fs::remove_file(root.path().join("experiments/alias.py")).unwrap();
        for index in 0..8 {
            fs::write(
                root.path().join(format!("experiments/data{index}.json")),
                vec![b'x'; MAX_FILE_BYTES as usize],
            )
            .unwrap();
        }
        request.inputs = ws
            .scan()
            .unwrap()
            .into_iter()
            .filter(|entry| entry.path.starts_with("experiments/data"))
            .map(selection)
            .collect();
        assert_eq!(
            PreparedInputs::prepare(&mut ws, &request.validate().unwrap())
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_TOO_LARGE"
        );
    }
    #[cfg(unix)]
    #[test]
    fn refuses_symlinks_and_nonblocking_fifo_reads() {
        use std::{ffi::CString, os::unix::fs::symlink};
        let (root, ws, _request) = setup();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("private.json"), "private").unwrap();
        symlink(outside.path(), root.path().join("experiments/escape")).unwrap();
        symlink(
            outside.path().join("private.json"),
            root.path().join("experiments/linked.json"),
        )
        .unwrap();
        let directory = open_root(&ws.root).unwrap();
        for path in ["experiments/escape/private.json", "experiments/linked.json"] {
            assert_eq!(
                read_selected(&directory, path).err().unwrap().code,
                "EXPERIMENT_INPUT_UNSAFE"
            );
        }
        let fifo = CString::new(
            root.path()
                .join("experiments/fifo.json")
                .as_os_str()
                .as_encoded_bytes(),
        )
        .unwrap();
        assert_eq!(unsafe { libc::mkfifo(fifo.as_ptr(), 0o600) }, 0);
        assert_eq!(
            read_selected(&directory, "experiments/fifo.json")
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_UNSAFE"
        );
    }
    #[cfg(windows)]
    #[test]
    fn refuses_junction_in_selected_ancestors() {
        use std::os::windows::process::CommandExt;
        let (root, ws, _request) = setup();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("private.json"), "private").unwrap();
        let junction = root.path().join("experiments").join("escape");
        let output = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(outside.path())
            .creation_flags(0x08000000)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "junction fixture unavailable: {:?} {:?}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let directory = open_root(&ws.root).unwrap();
        let result = read_selected(&directory, "experiments/escape/private.json");
        fs::remove_dir(junction).unwrap();
        assert_eq!(result.err().unwrap().code, "EXPERIMENT_INPUT_UNSAFE");
        assert_eq!(
            fs::read_to_string(outside.path().join("private.json")).unwrap(),
            "private"
        );
    }
}

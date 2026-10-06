//! Independently confirmed artifact imports. Each file uses the existing Host
//! journal; its provenance commits with identity, revision and sync outbox.
use crate::{
    experiment_input::{InputSummary, SelectedFile, MAX_FILES},
    experiment_outputs::{
        OutputKind, OutputManifest, OutputReport, OutputSummary, ValidatedOutput,
    },
    workspace::{hash, Entry, HostError, Result, Workspace},
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub(crate) const SCHEMA: &str = "
    CREATE TABLE IF NOT EXISTS experiment_imports (
      operation_id TEXT PRIMARY KEY,vault_id TEXT NOT NULL,run_id TEXT NOT NULL,
      request_fingerprint TEXT NOT NULL,fingerprint TEXT NOT NULL,plan TEXT,
      state TEXT NOT NULL,created_ms INTEGER NOT NULL,confirmed_ms INTEGER);
    CREATE TABLE IF NOT EXISTS experiment_import_items (
      import_id TEXT NOT NULL,ordinal INTEGER NOT NULL,write_id TEXT UNIQUE NOT NULL,
      file_id TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,error TEXT,
      PRIMARY KEY(import_id,ordinal));
    CREATE INDEX IF NOT EXISTS experiment_import_file ON experiment_import_items(file_id,state);
    CREATE INDEX IF NOT EXISTS experiment_import_run ON experiment_imports(run_id,created_ms,operation_id);
    CREATE INDEX IF NOT EXISTS experiment_import_history ON experiment_imports(vault_id,created_ms,operation_id);";
const MAX_PLAN: usize = 128 * 1024;
const MAX_RECEIPT: usize = 4096;
const MAX_METADATA: i64 = 16 * 1024 * 1024;
const MAX_RECORDS: i64 = 4096;

fn error(code: &str) -> HostError {
    HostError::new(code)
}
fn corrupt() -> HostError {
    error("EXPERIMENT_IMPORT_CORRUPT")
}
fn invalid() -> HostError {
    error("EXPERIMENT_IMPORT_INVALID")
}
fn changed() -> HostError {
    error("EXPERIMENT_IMPORT_TARGET_CHANGED")
}
fn valid_id(value: &str) -> bool {
    uuid::Uuid::parse_str(value).is_ok_and(|id| !id.is_nil() && id.to_string() == value)
}
fn now() -> Result<i64> {
    i64::try_from(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| corrupt())?
            .as_millis(),
    )
    .map_err(|_| corrupt())
}
fn digest<T: Serialize>(kind: &[u8], value: &T) -> Result<String> {
    let mut bytes = kind.to_vec();
    bytes.extend(serde_json::to_vec(value).map_err(|_| invalid())?);
    Ok(hash(&bytes))
}
fn name_key(value: &str) -> String {
    use unicode_casefold::UnicodeCaseFold;
    use unicode_normalization::UnicodeNormalization;
    value.case_fold().collect::<String>().nfc().collect()
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub output_path: String,
    pub destination: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportRequest {
    pub vault_id: String,
    pub operation_id: String,
    pub run_id: String,
    pub selections: Vec<Selection>,
}
impl ImportRequest {
    pub(crate) fn canonical(&self) -> Result<Self> {
        if !valid_id(&self.vault_id)
            || !valid_id(&self.operation_id)
            || !valid_id(&self.run_id)
            || self.operation_id == self.run_id
            || !(1..=MAX_FILES).contains(&self.selections.len())
        {
            return Err(invalid());
        }
        let mut source = BTreeSet::new();
        let mut targets = BTreeSet::new();
        for item in &self.selections {
            if !crate::experiment_outputs::valid_path(&item.output_path)
                || !crate::experiment_outputs::valid_path(&item.destination)
                || item.destination.split('/').any(|part| {
                    [".ainote", ".git", "opennexus-records"]
                        .iter()
                        .any(|name| part.eq_ignore_ascii_case(name))
                })
                || !source.insert(name_key(&item.output_path))
                || !targets.insert(name_key(&item.destination))
            {
                return Err(invalid());
            }
        }
        let mut request = self.clone();
        request
            .selections
            .sort_by(|a, b| a.destination.cmp(&b.destination));
        Ok(request)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportItemPlan {
    pub output: OutputManifest,
    pub target: SelectedFile,
    pub write_id: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportPlan {
    pub request: ImportRequest,
    pub source: InputSummary,
    pub execution: RunEvidence,
    pub items: Vec<ImportItemPlan>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunEvidence {
    pub created_ms: i64,
    pub approval_id: Option<String>,
    pub approved_ms: Option<i64>,
    pub outcome: crate::experiment_store::Outcome,
    pub exit_code: Option<u32>,
    pub elapsed_ms: u64,
}
fn compatible(output: &OutputManifest, path: &str) -> bool {
    let extension = path.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    match output.kind {
        OutputKind::Text | OutputKind::Markdown => extension == "md",
        OutputKind::Png => extension == "png",
        OutputKind::Json => {
            extension == "json" && crate::experiment_contract::is_experiment_file(path)
        }
        OutputKind::Csv => {
            extension == "csv" && crate::experiment_contract::is_experiment_file(path)
        }
    }
}
impl ImportPlan {
    fn validate(&self) -> Result<()> {
        let canonical = self.request.canonical().map_err(|_| corrupt())?;
        if serde_json::to_vec(&canonical).map_err(|_| corrupt())?
            != serde_json::to_vec(&self.request).map_err(|_| corrupt())?
            || self.source.request.vault_id != self.request.vault_id
            || self.source.request.operation_id != self.request.run_id
            || self.items.len() != self.request.selections.len()
            || self.execution.created_ms < 0
            || self.execution.approved_ms.is_some_and(|ms| ms < 0)
            || self
                .execution
                .approval_id
                .as_ref()
                .is_some_and(|id| !valid_id(id))
        {
            return Err(corrupt());
        }
        let output_budget = self.source.validate()?.limits().output_bytes();
        let mut ids = BTreeSet::new();
        let mut target_ids = BTreeSet::new();
        let mut total = 0u64;
        for (item, choice) in self.items.iter().zip(&self.request.selections) {
            if item.output.path != choice.output_path
                || item.target.path != choice.destination
                || !compatible(&item.output, &item.target.path)
                || !valid_id(&item.write_id)
                || !valid_id(&item.target.file_id)
                || item.write_id == self.request.operation_id
                || !ids.insert(&item.write_id)
                || !target_ids.insert(&item.target.file_id)
                || item.target.revision < 0
                || (item.target.hash.is_empty() != (item.target.revision == 0))
                || (!item.target.hash.is_empty()
                    && (item.target.hash.len() != 64
                        || !item
                            .target
                            .hash
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))))
            {
                return Err(corrupt());
            }
            total = total.checked_add(item.output.bytes).ok_or_else(corrupt)?;
        }
        OutputReport::Collected {
            summary: OutputSummary {
                files: self.items.iter().map(|i| i.output.clone()).collect(),
                skipped: vec![],
                total_bytes: total,
            },
        }
        .validate(output_budget)
        .map_err(|_| corrupt())
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ImportState {
    AwaitingConfirmation,
    Approved,
    Completed,
    Partial,
    Failed,
    Cancelled,
    Rejected,
}
impl ImportState {
    fn parse(value: &str) -> Result<Self> {
        serde_json::from_value(serde_json::Value::String(value.into())).map_err(|_| corrupt())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportItemStatus {
    pub state: String,
    pub entry: Option<Entry>,
    pub error: Option<String>,
}
#[derive(Debug, Clone, Serialize)]
pub struct ImportRecord {
    pub plan: ImportPlan,
    pub fingerprint: String,
    pub state: ImportState,
    pub created_ms: i64,
    pub confirmed_ms: Option<i64>,
    pub items: Vec<ImportItemStatus>,
    pub atomic_scope: &'static str,
}
#[derive(Debug, Serialize)]
pub struct ArtifactOrigin {
    pub import_id: String,
    pub run_id: String,
    pub source: InputSummary,
    pub execution: RunEvidence,
    pub output: OutputManifest,
    pub imported: Entry,
    pub current_path: Option<String>,
    pub created_ms: i64,
}
#[derive(Debug, Serialize)]
pub struct OriginPage {
    pub items: Vec<ArtifactOrigin>,
    pub next_cursor: Option<crate::experiment_store::HistoryCursor>,
}
#[derive(Debug, Serialize)]
pub struct ImportHistoryItem {
    pub operation_id: String,
    pub run_id: String,
    pub entry: SelectedFile,
    pub fingerprint: String,
    pub state: ImportState,
    pub created_ms: i64,
    pub files: usize,
    pub committed: usize,
}
#[derive(Debug, Serialize)]
pub struct ImportHistoryPage {
    pub items: Vec<ImportHistoryItem>,
    pub next_cursor: Option<crate::experiment_store::HistoryCursor>,
}
#[derive(Debug, Serialize)]
pub struct TargetPreview {
    pub target: SelectedFile,
    pub bytes: u64,
    pub preview: Option<crate::experiment_preview::TextPreview>,
}
type StoredImportRow = (
    String,
    String,
    String,
    String,
    Option<String>,
    String,
    i64,
    Option<i64>,
);
fn load(conn: &Connection, vault: &str, operation: &str) -> Result<Option<ImportRecord>> {
    let row:Option<StoredImportRow>=conn.query_row(
        "SELECT vault_id,run_id,request_fingerprint,fingerprint,CASE WHEN length(CAST(plan AS BLOB))<=?2 THEN plan END,state,created_ms,confirmed_ms FROM experiment_imports WHERE operation_id=?1",
        params![operation,MAX_PLAN],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?,r.get(7)?))).optional()?;
    let Some((owner, run_id, request_fp, fingerprint, plan, state, created_ms, confirmed_ms)) = row
    else {
        return Ok(None);
    };
    if owner != vault {
        return Err(error("VAULT_PERMISSION_CHANGED"));
    }
    if state == "forgotten" {
        return Err(error("EXPERIMENT_IMPORT_FORGOTTEN"));
    }
    let plan: ImportPlan =
        serde_json::from_str(&plan.ok_or_else(corrupt)?).map_err(|_| corrupt())?;
    plan.validate()?;
    if plan.request.operation_id != operation
        || plan.request.run_id != run_id
        || plan.request.vault_id != vault
        || created_ms < 0
        || confirmed_ms.is_some_and(|ms| ms < 0)
        || request_fp != digest(b"opennexus-import-request-v1\0", &plan.request)?
        || fingerprint != digest(b"opennexus-import-plan-v1\0", &plan)?
    {
        return Err(corrupt());
    }
    let state = ImportState::parse(&state)?;
    if matches!(
        state,
        ImportState::Approved
            | ImportState::Completed
            | ImportState::Partial
            | ImportState::Failed
            | ImportState::Cancelled
    ) && confirmed_ms.is_none()
    {
        return Err(corrupt());
    }
    if matches!(
        state,
        ImportState::AwaitingConfirmation | ImportState::Rejected
    ) && confirmed_ms.is_some()
    {
        return Err(corrupt());
    }
    let mut statement=conn.prepare("SELECT ordinal,write_id,file_id,state,CASE WHEN length(CAST(receipt AS BLOB))<=?2 THEN receipt END,error FROM experiment_import_items WHERE import_id=?1 ORDER BY ordinal")?;
    let rows = statement.query_map(params![operation, MAX_RECEIPT], |r| {
        Ok((
            r.get::<_, usize>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, Option<String>>(4)?,
            r.get::<_, Option<String>>(5)?,
        ))
    })?;
    let mut items = Vec::new();
    for row in rows {
        let (ordinal, write_id, file_id, state, receipt, code) = row?;
        let item = plan.items.get(ordinal).ok_or_else(corrupt)?;
        if ordinal != items.len()
            || item.write_id != write_id
            || item.target.file_id != file_id
            || !["pending", "committed", "failed", "cancelled", "rejected"]
                .contains(&state.as_str())
            || code.as_ref().is_some_and(|c| {
                c.is_empty()
                    || c.len() > 96
                    || !c
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
            })
        {
            return Err(corrupt());
        }
        let entry: Option<Entry> = receipt
            .map(|json| serde_json::from_str(&json).map_err(|_| corrupt()))
            .transpose()?;
        if state == "committed" {
            let entry = entry.as_ref().ok_or_else(corrupt)?;
            if entry.path != item.target.path
                || entry.file_id != file_id
                || entry.hash != item.output.sha256
                || entry.revision <= 0
                || entry.deleted
                || entry.is_folder
                || confirmed_ms.is_none()
            {
                return Err(corrupt());
            }
        } else if entry.is_some() {
            return Err(corrupt());
        }
        items.push(ImportItemStatus {
            state,
            entry,
            error: code,
        });
    }
    if items.len() != plan.items.len()
        || (state == ImportState::Completed && items.iter().any(|i| i.state != "committed"))
    {
        return Err(corrupt());
    }
    Ok(Some(ImportRecord {
        plan,
        fingerprint,
        state,
        created_ms,
        confirmed_ms,
        items,
        atomic_scope: "file",
    }))
}
fn quota(conn: &Connection) -> Result<()> {
    let (count,bytes):(i64,i64)=conn.query_row("SELECT COUNT(*) FILTER(WHERE state!='forgotten'),COALESCE(SUM(COALESCE(length(CAST(plan AS BLOB)),0)+length(operation_id)+length(vault_id)+length(run_id)+length(fingerprint)+length(request_fingerprint)+64),0) FROM experiment_imports",[],|r|Ok((r.get(0)?,r.get(1)?)))?;
    let items:i64=conn.query_row("SELECT COALESCE(SUM(72+COALESCE(length(CAST(receipt AS BLOB)),0)+COALESCE(length(error),0)+CASE WHEN state='pending' THEN 4096 ELSE 0 END),0) FROM experiment_import_items",[],|r|r.get(0))?;
    if count > MAX_RECORDS || bytes + items > MAX_METADATA {
        return Err(error("EXPERIMENT_IMPORT_QUOTA"));
    }
    Ok(())
}
/// Reserved child identifiers belong to this independently confirmed import;
/// ordinary note/sync writes cannot repurpose them before creating a journal.
pub(crate) fn check_new_write(
    conn: &Connection,
    vault: &str,
    write_id: &str,
    path: &str,
    expected: &str,
    digest: &str,
    authorization: (&str, Option<&str>),
) -> Result<()> {
    let (origin, identity) = authorization;
    if conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM experiment_imports WHERE operation_id=?1)",
        [write_id],
        |r| r.get::<_, bool>(0),
    )? {
        return Err(error("OPERATION_PAYLOAD_CONFLICT"));
    }
    let binding: Option<(String, usize)> = conn
        .query_row(
            "SELECT import_id,ordinal FROM experiment_import_items WHERE write_id=?1",
            [write_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    let Some((id, ordinal)) = binding else {
        return Ok(());
    };
    let record = load(conn, vault, &id)?.ok_or_else(corrupt)?;
    let item = &record.plan.items[ordinal];
    if record.state != ImportState::Approved
        || record.confirmed_ms.is_none()
        || record.items[ordinal].state != "pending"
        || item.target.path != path
        || item.target.hash != expected
        || item.output.sha256 != digest
        || origin != "local"
        || identity != Some(item.target.file_id.as_str())
    {
        return Err(error("EXPERIMENT_IMPORT_AUTH_REQUIRED"));
    }
    Ok(())
}
pub(crate) fn conflict_write(conn: &Connection, write_id: &str) -> Result<()> {
    let id: Option<String> = conn
        .query_row(
            "SELECT import_id FROM experiment_import_items WHERE write_id=?1",
            [write_id],
            |r| r.get(0),
        )
        .optional()?;
    let Some(id) = id else { return Ok(()) };
    conn.execute("UPDATE experiment_import_items SET state='failed',error='RECOVERY_CONFLICT' WHERE write_id=?1 AND state='pending'",[write_id])?;
    conn.execute("UPDATE experiment_imports SET state=CASE WHEN EXISTS(SELECT 1 FROM experiment_import_items WHERE import_id=?1 AND state='committed') THEN 'partial' ELSE 'failed' END WHERE operation_id=?1 AND state='approved'",[id])?;
    Ok(())
}
fn target(ws: &Workspace, path: &str) -> Result<SelectedFile> {
    check_destination(ws, path)?;
    let resolved = ws.resolve(path)?;
    let current = ws.entry(path)?;
    if resolved.exists() {
        let entry = current
            .filter(|e| !e.deleted && !e.is_folder)
            .ok_or_else(changed)?;
        if !resolved.is_file() || crate::payloads::hash_file(&resolved)? != entry.hash {
            return Err(changed());
        }
        Ok(SelectedFile {
            file_id: entry.file_id,
            path: path.into(),
            hash: entry.hash,
            revision: entry.revision,
        })
    } else {
        Ok(SelectedFile {
            file_id: current
                .map(|e| e.file_id)
                .unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
            path: path.into(),
            hash: String::new(),
            revision: 0,
        })
    }
}
#[cfg_attr(not(windows), allow(dead_code))] // Native trusted user route is Windows-only.
pub(crate) fn check_target(ws: &Workspace, expected: &SelectedFile) -> Result<()> {
    check_destination(ws, &expected.path)?;
    let resolved = ws.resolve(&expected.path)?;
    let current = ws.entry(&expected.path)?;
    if expected.hash.is_empty() {
        if resolved.exists() || current.is_some_and(|e| !e.deleted || e.file_id != expected.file_id)
        {
            return Err(changed());
        }
    } else {
        let entry = current
            .filter(|e| !e.deleted && !e.is_folder)
            .ok_or_else(changed)?;
        if entry.file_id != expected.file_id
            || entry.hash != expected.hash
            || entry.revision != expected.revision
            || !resolved.is_file()
            || crate::payloads::hash_file(&resolved)? != expected.hash
        {
            return Err(changed());
        }
    }
    Ok(())
}
fn check_destination(ws: &Workspace, path: &str) -> Result<()> {
    ws.resolve(path)?;
    let mut prefix = ws.root.clone();
    let parts: Vec<_> = path.split('/').collect();
    for (index, part) in parts.iter().enumerate() {
        prefix.push(part);
        match std::fs::symlink_metadata(&prefix) {
            Ok(meta) => {
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    if meta.file_attributes() & 0x400 != 0 {
                        return Err(error("UNSAFE_PATH"));
                    }
                }
                if meta.file_type().is_symlink()
                    || (index + 1 < parts.len() && !meta.is_dir())
                    || (index + 1 == parts.len() && !meta.is_file())
                {
                    return Err(error("UNSAFE_PATH"));
                }
                if index + 1 == parts.len() {
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::MetadataExt;
                        if meta.nlink() != 1 {
                            return Err(error("UNSAFE_PATH"));
                        }
                    }
                    #[cfg(windows)]
                    {
                        use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
                        use windows_sys::Win32::Storage::FileSystem::*;
                        let file = std::fs::OpenOptions::new()
                            .access_mode(FILE_READ_ATTRIBUTES)
                            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
                            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
                            .open(&prefix)?;
                        let mut info = BY_HANDLE_FILE_INFORMATION::default();
                        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) }
                            == 0
                            || info.nNumberOfLinks != 1
                            || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
                        {
                            return Err(error("UNSAFE_PATH"));
                        }
                    }
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}
/// Runs inside the ordinary write's SQLite transaction, including recovery.
pub(crate) fn complete_write(conn: &Connection, write_id: &str, entry: &Entry) -> Result<()> {
    let binding: Option<(String, usize)> = conn
        .query_row(
            "SELECT import_id,ordinal FROM experiment_import_items WHERE write_id=?1",
            [write_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    let Some((id, ordinal)) = binding else {
        return Ok(());
    };
    let vault: String = conn.query_row(
        "SELECT vault_id FROM experiment_imports WHERE operation_id=?1",
        [&id],
        |r| r.get(0),
    )?;
    let record = load(conn, &vault, &id)?.ok_or_else(corrupt)?;
    let item = &record.plan.items[ordinal];
    if record.confirmed_ms.is_none()
        || record.items[ordinal].state != "pending"
        || entry.file_id != item.target.file_id
        || entry.path != item.target.path
        || entry.hash != item.output.sha256
    {
        return Err(corrupt());
    }
    let encoded = serde_json::to_string(entry).map_err(|_| corrupt())?;
    if encoded.len() > MAX_RECEIPT {
        return Err(corrupt());
    }
    conn.execute("UPDATE experiment_import_items SET state='committed',receipt=?2,error=NULL WHERE write_id=?1 AND state='pending'",params![write_id,encoded])?;
    let (pending,failed):(i64,i64)=conn.query_row("SELECT SUM(state='pending'),SUM(state='failed') FROM experiment_import_items WHERE import_id=?1",[&id],|r|Ok((r.get(0)?,r.get(1)?)))?;
    if pending == 0 && failed == 0 && record.state == ImportState::Approved {
        conn.execute(
            "UPDATE experiment_imports SET state='completed' WHERE operation_id=?1",
            [&id],
        )?;
    }
    quota(conn)
}

impl Workspace {
    pub fn experiment_import_target_preview(
        &mut self,
        operation: &str,
        output_path: &str,
    ) -> Result<TargetPreview> {
        use std::io::Read;
        let record = self
            .experiment_import_record(operation)?
            .ok_or_else(invalid)?;
        let item = record
            .plan
            .items
            .iter()
            .find(|i| i.output.path == output_path)
            .ok_or_else(invalid)?;
        self.scan()?;
        check_target(self, &item.target)?;
        if item.target.hash.is_empty() {
            return Ok(TargetPreview {
                target: item.target.clone(),
                bytes: 0,
                preview: None,
            });
        }
        let file = std::fs::File::open(self.resolve(&item.target.path)?)?;
        let bytes = file.metadata()?.len();
        let mut preview = None;
        if item.output.kind != OutputKind::Png {
            let mut prefix = Vec::new();
            file.take(64 * 1024 + 1).read_to_end(&mut prefix)?;
            let text = String::from_utf8_lossy(&prefix);
            let mut bounded = crate::experiment_preview::text_preview(&text);
            bounded.truncated |= bytes > prefix.len() as u64;
            preview = Some(bounded);
        }
        check_target(self, &item.target)?;
        Ok(TargetPreview {
            target: item.target.clone(),
            bytes,
            preview,
        })
    }
    pub fn experiment_import_history(
        &self,
        run_id: &str,
        limit: usize,
        cursor: Option<&crate::experiment_store::HistoryCursor>,
    ) -> Result<ImportHistoryPage> {
        self.import_history(Some(run_id), limit, cursor)
    }
    pub fn experiment_all_import_history(
        &self,
        limit: usize,
        cursor: Option<&crate::experiment_store::HistoryCursor>,
    ) -> Result<ImportHistoryPage> {
        self.import_history(None, limit, cursor)
    }
    fn import_history(
        &self,
        run_id: Option<&str>,
        limit: usize,
        cursor: Option<&crate::experiment_store::HistoryCursor>,
    ) -> Result<ImportHistoryPage> {
        if run_id.is_some_and(|id| !valid_id(id)) || !(1..=50).contains(&limit) {
            return Err(invalid());
        }
        if let Some(c) = cursor {
            if c.vault_id != self.vault_id {
                return Err(error("VAULT_PERMISSION_CHANGED"));
            }
            if c.created_ms < 0 || !valid_id(&c.operation_id) {
                return Err(invalid());
            }
        }
        let mut stmt = self.db.prepare("SELECT operation_id FROM experiment_imports WHERE vault_id=?1 AND (?2 IS NULL OR run_id=?2) AND state!='forgotten' AND (?3 IS NULL OR created_ms<?3 OR (created_ms=?3 AND operation_id<?4)) ORDER BY created_ms DESC,operation_id DESC LIMIT ?5")?;
        let ids = stmt
            .query_map(
                params![
                    self.vault_id,
                    run_id,
                    cursor.map(|c| c.created_ms),
                    cursor.map(|c| c.operation_id.as_str()),
                    limit + 1
                ],
                |r| r.get::<_, String>(0),
            )?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let more = ids.len() > limit;
        let mut items = Vec::new();
        for id in ids.into_iter().take(limit) {
            let record = self.experiment_import_record(&id)?.ok_or_else(corrupt)?;
            items.push(ImportHistoryItem {
                operation_id: id,
                run_id: record.plan.request.run_id,
                entry: record.plan.source.request.entry,
                fingerprint: record.fingerprint,
                state: record.state,
                created_ms: record.created_ms,
                files: record.items.len(),
                committed: record
                    .items
                    .iter()
                    .filter(|i| i.state == "committed")
                    .count(),
            });
        }
        let next_cursor = if more {
            items
                .last()
                .map(|i| crate::experiment_store::HistoryCursor {
                    vault_id: self.vault_id.clone(),
                    created_ms: i.created_ms,
                    operation_id: i.operation_id.clone(),
                })
        } else {
            None
        };
        Ok(ImportHistoryPage { items, next_cursor })
    }
    pub fn experiment_import_record(&self, operation: &str) -> Result<Option<ImportRecord>> {
        load(&self.db, &self.vault_id, operation)
    }
    pub fn experiment_import_prepare(&mut self, request: &ImportRequest) -> Result<ImportRecord> {
        let request = request.canonical()?;
        if request.vault_id != self.vault_id {
            return Err(error("VAULT_PERMISSION_CHANGED"));
        }
        if let Some(old) = self.experiment_import_record(&request.operation_id)? {
            if digest(b"opennexus-import-request-v1\0", &request)?
                != digest(b"opennexus-import-request-v1\0", &old.plan.request)?
            {
                return Err(error("OPERATION_PAYLOAD_CONFLICT"));
            }
            return Ok(old);
        }
        if self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM experiment_import_items WHERE write_id=?1)",
            [&request.operation_id],
            |r| r.get::<_, bool>(0),
        )? || self.operation(&request.operation_id)?.is_some()
            || self.experiment_record(&request.operation_id)?.is_some()
        {
            return Err(error("OPERATION_PAYLOAD_CONFLICT"));
        }
        let record = self
            .experiment_record(&request.run_id)?
            .ok_or_else(|| error("EXPERIMENT_OUTPUT_NOT_FOUND"))?;
        let result = record
            .result
            .as_ref()
            .ok_or_else(|| error("EXPERIMENT_OUTPUT_NOT_FOUND"))?;
        let execution = RunEvidence {
            created_ms: record.created_ms,
            approval_id: record.approval_id.clone(),
            approved_ms: record.approved_ms,
            outcome: result.outcome,
            exit_code: result.exit_code,
            elapsed_ms: result.elapsed_ms,
        };
        self.scan()?;
        let mut items = Vec::new();
        for choice in &request.selections {
            let output = self.experiment_output(&request.run_id, &choice.output_path)?;
            if !compatible(output.manifest(), &choice.destination) {
                return Err(error("EXPERIMENT_IMPORT_TARGET_UNSUPPORTED"));
            }
            items.push(ImportItemPlan {
                output: output.manifest().clone(),
                target: target(self, &choice.destination)?,
                write_id: uuid::Uuid::new_v4().to_string(),
            });
        }
        let plan = ImportPlan {
            request,
            source: record.summary,
            execution,
            items,
        };
        plan.validate()?;
        let encoded = serde_json::to_string(&plan).map_err(|_| invalid())?;
        if encoded.len() > MAX_PLAN {
            return Err(error("EXPERIMENT_IMPORT_QUOTA"));
        }
        let fingerprint = digest(b"opennexus-import-plan-v1\0", &plan)?;
        let tx = self.db.transaction()?;
        tx.execute("INSERT INTO experiment_imports VALUES(?1,?2,?3,?4,?5,?6,'awaiting_confirmation',?7,NULL)",params![plan.request.operation_id,plan.request.vault_id,plan.request.run_id,digest(b"opennexus-import-request-v1\0",&plan.request)?,fingerprint,encoded,now()?])?;
        for (ordinal, item) in plan.items.iter().enumerate() {
            tx.execute(
                "INSERT INTO experiment_import_items VALUES(?1,?2,?3,?4,'pending',NULL,NULL)",
                params![
                    plan.request.operation_id,
                    ordinal,
                    item.write_id,
                    item.target.file_id
                ],
            )?;
        }
        quota(&tx)?;
        tx.commit()?;
        self.experiment_import_record(&plan.request.operation_id)?
            .ok_or_else(corrupt)
    }
    /// Trusted user confirmation only, separate from run/file-write approval.
    #[cfg_attr(not(windows), allow(dead_code))]
    pub(crate) fn experiment_import_approve(
        &mut self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<ImportRecord> {
        let record = self
            .experiment_import_record(operation)?
            .ok_or_else(invalid)?;
        if record.fingerprint != fingerprint {
            return Err(error("OPERATION_PAYLOAD_CONFLICT"));
        }
        if record.state != ImportState::AwaitingConfirmation {
            return Ok(record);
        }
        self.scan()?;
        for item in &record.plan.items {
            check_target(self, &item.target)?;
        }
        for item in &record.plan.items {
            let output = self.experiment_output(&record.plan.request.run_id, &item.output.path)?;
            if output.manifest() != &item.output {
                return Err(corrupt());
            }
            self.store_payload(&item.write_id, output.content())?;
        }
        for item in &record.plan.items {
            check_target(self, &item.target)?;
        }
        self.db.execute("UPDATE experiment_imports SET state='approved',confirmed_ms=?3 WHERE operation_id=?1 AND fingerprint=?2 AND state='awaiting_confirmation'",params![operation,fingerprint,now()?])?;
        self.experiment_import_record(operation)?
            .ok_or_else(corrupt)
    }
    /// One atomic file per call, allowing cancellation/vault transitions between
    /// files. Recovery may settle a previously accepted file; never starts others.
    #[cfg_attr(not(windows), allow(dead_code))]
    pub(crate) fn experiment_import_next(&mut self, operation: &str) -> Result<ImportRecord> {
        self.recover()?;
        let record = self
            .experiment_import_record(operation)?
            .ok_or_else(invalid)?;
        if record.state != ImportState::Approved {
            return Ok(record);
        }
        let ordinal = record
            .items
            .iter()
            .position(|item| item.state == "pending")
            .ok_or_else(corrupt)?;
        let item = &record.plan.items[ordinal];
        let result = (|| {
            self.scan()?;
            check_target(self, &item.target)?;
            let bytes = self.payload(&item.write_id, &[])?;
            let output = ValidatedOutput::restore(
                &item.output,
                bytes,
                record.plan.source.validate()?.limits().output_bytes(),
            )
            .map_err(|_| corrupt())?;
            self.write_with_identity(
                &item.target.path,
                &item.target.hash,
                output.content(),
                "local",
                &item.write_id,
                Some(&item.target.file_id),
            )
        })();
        if let Err(failure) = result {
            let accepted: bool = self.db.query_row(
                "SELECT EXISTS(SELECT 1 FROM journal WHERE operation_id=?1 AND state='pending')",
                [&item.write_id],
                |r| r.get(0),
            )?;
            if accepted {
                self.db.execute("UPDATE experiment_import_items SET error=?2 WHERE write_id=?1 AND state='pending'",params![item.write_id,failure.code])?;
            } else {
                let tx = self.db.transaction()?;
                tx.execute("UPDATE experiment_import_items SET state='failed',error=?2 WHERE write_id=?1 AND state='pending'",params![item.write_id,failure.code])?;
                tx.execute("UPDATE experiment_imports SET state=CASE WHEN EXISTS(SELECT 1 FROM experiment_import_items WHERE import_id=?1 AND state='committed') THEN 'partial' ELSE 'failed' END WHERE operation_id=?1 AND state='approved'",[operation])?;
                tx.commit()?;
            }
        }
        self.experiment_import_record(operation)?
            .ok_or_else(corrupt)
    }
    pub fn experiment_import_cancel(
        &mut self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<ImportRecord> {
        let record = self
            .experiment_import_record(operation)?
            .ok_or_else(invalid)?;
        if record.fingerprint != fingerprint {
            return Err(error("OPERATION_PAYLOAD_CONFLICT"));
        }
        if !matches!(
            record.state,
            ImportState::AwaitingConfirmation | ImportState::Approved
        ) {
            return Ok(record);
        }
        let state = if record.state == ImportState::AwaitingConfirmation {
            "rejected"
        } else {
            "cancelled"
        };
        let tx = self.db.transaction()?;
        tx.execute(
            "UPDATE experiment_imports SET state=?2 WHERE operation_id=?1",
            params![operation, state],
        )?;
        tx.execute("UPDATE experiment_import_items SET state=?2,error=NULL WHERE import_id=?1 AND state='pending' AND NOT EXISTS(SELECT 1 FROM journal j WHERE j.operation_id=write_id AND j.state='pending')",params![operation,state])?;
        tx.commit()?;
        self.experiment_import_record(operation)?
            .ok_or_else(corrupt)
    }
    pub fn experiment_artifact_origins(
        &self,
        file_id: &str,
        limit: usize,
        cursor: Option<&crate::experiment_store::HistoryCursor>,
    ) -> Result<OriginPage> {
        if !valid_id(file_id) || !(1..=50).contains(&limit) {
            return Err(invalid());
        }
        if let Some(cursor) = cursor {
            if cursor.vault_id != self.vault_id {
                return Err(error("VAULT_PERMISSION_CHANGED"));
            }
            if cursor.created_ms < 0 || !valid_id(&cursor.operation_id) {
                return Err(invalid());
            }
        }
        let mut statement=self.db.prepare("SELECT i.import_id,i.ordinal FROM experiment_import_items i JOIN experiment_imports r ON r.operation_id=i.import_id WHERE i.file_id=?1 AND i.state='committed' AND (?2 IS NULL OR r.created_ms<?2 OR (r.created_ms=?2 AND r.operation_id<?3)) ORDER BY r.created_ms DESC,r.operation_id DESC LIMIT ?4")?;
        let rows = statement
            .query_map(
                params![
                    file_id,
                    cursor.map(|c| c.created_ms),
                    cursor.map(|c| c.operation_id.as_str()),
                    limit + 1
                ],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, usize>(1)?)),
            )?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let more = rows.len() > limit;
        let mut result = Vec::new();
        for (id, ordinal) in rows.into_iter().take(limit) {
            let record = self.experiment_import_record(&id)?.ok_or_else(corrupt)?;
            let imported = record
                .items
                .get(ordinal)
                .and_then(|i| i.entry.clone())
                .ok_or_else(corrupt)?;
            let current_path = match self.path_for_id(file_id) {
                Ok(path) => Some(path),
                Err(e) if e.code == "FILE_NOT_FOUND" => None,
                Err(e) => return Err(e),
            };
            result.push(ArtifactOrigin {
                import_id: id,
                run_id: record.plan.request.run_id,
                source: record.plan.source,
                execution: record.plan.execution,
                output: record.plan.items[ordinal].output.clone(),
                imported,
                current_path,
                created_ms: record.created_ms,
            });
        }
        let next_cursor = if more {
            result
                .last()
                .map(|item| crate::experiment_store::HistoryCursor {
                    vault_id: self.vault_id.clone(),
                    created_ms: item.created_ms,
                    operation_id: item.import_id.clone(),
                })
        } else {
            None
        };
        Ok(OriginPage {
            items: result,
            next_cursor,
        })
    }
    pub fn experiment_import_forget(&mut self, operation: &str, fingerprint: &str) -> Result<()> {
        let forgotten:Option<(String,String)>=self.db.query_row("SELECT vault_id,fingerprint FROM experiment_imports WHERE operation_id=?1 AND state='forgotten'",[operation],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        if let Some((vault, old)) = forgotten {
            if vault != self.vault_id {
                return Err(error("VAULT_PERMISSION_CHANGED"));
            }
            return if old == fingerprint {
                Ok(())
            } else {
                Err(error("OPERATION_PAYLOAD_CONFLICT"))
            };
        }
        let record = self
            .experiment_import_record(operation)?
            .ok_or_else(invalid)?;
        if record.fingerprint != fingerprint {
            return Err(error("OPERATION_PAYLOAD_CONFLICT"));
        }
        if matches!(record.state,ImportState::AwaitingConfirmation|ImportState::Approved)
            || self.db.query_row("SELECT EXISTS(SELECT 1 FROM experiment_import_items i JOIN journal j ON j.operation_id=i.write_id WHERE i.import_id=?1 AND j.state='pending')",[operation],|r|r.get::<_,bool>(0))? {return Err(error("EXPERIMENT_IMPORT_BUSY"));}
        let tx = self.db.transaction()?;
        tx.execute(
            "DELETE FROM experiment_import_items WHERE import_id=?1",
            [operation],
        )?;
        tx.execute(
            "UPDATE experiment_imports SET state='forgotten',plan=NULL WHERE operation_id=?1",
            [operation],
        )?;
        quota(&tx)?;
        tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_input::{PreparedInputs, RunRequest},
        experiment_log::CaptureSnapshot,
        experiment_outputs::CollectedOutputs,
        experiment_policy::ExecutionLimits,
        experiment_runtime::RUNTIME_ID,
        experiment_store::{Outcome, RunResult},
    };
    fn setup(outputs: CollectedOutputs) -> (tempfile::TempDir, Workspace, ImportRequest) {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(
            root.path().join("experiments/source.py"),
            b"# retained source\r\n",
        )
        .unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let source = ws.read("experiments/source.py").unwrap().entry;
        let run = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry: SelectedFile {
                file_id: source.file_id,
                path: source.path,
                hash: source.hash,
                revision: source.revision,
            },
            inputs: vec![],
            limits: ExecutionLimits::default(),
        };
        let prepared = PreparedInputs::prepare(&mut ws, &run.validate().unwrap()).unwrap();
        let record = ws.experiment_prepare(prepared).unwrap();
        ws.experiment_approve(&run.operation_id, &record.summary.fingerprint)
            .unwrap();
        let claimed = ws.experiment_claim(&run.operation_id).unwrap();
        ws.experiment_running(&claimed).unwrap();
        let mut stream = crate::experiment_log::LogBuffer::new(8192).snapshot();
        stream.complete = true;
        let result = RunResult {
            outcome: Outcome::Completed,
            exit_code: Some(0),
            elapsed_ms: 1,
            error: None,
            user_cpu_ticks: Some(10),
            peak_memory_bytes: Some(1000),
            final_disk_bytes: Some(outputs.total_bytes),
            logs: CaptureSnapshot {
                stdout: stream.clone(),
                stderr: stream,
            },
            outputs: None,
        };
        ws.experiment_finish_outputs(&claimed, result, Some(&outputs))
            .unwrap();
        let request = ImportRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            run_id: run.operation_id,
            selections: vec![Selection {
                output_path: "report.md".into(),
                destination: "results/report.md".into(),
            }],
        };
        (root, ws, request)
    }
    fn outputs() -> CollectedOutputs {
        let mut outputs =
            CollectedOutputs::fixture("report.md", "# 中文报告\r\nvalue:18\r\n".as_bytes());
        let csv = CollectedOutputs::fixture("table.csv", b"value\r\n18\r\n");
        outputs.total_bytes += csv.total_bytes;
        outputs.files.extend(csv.files);
        outputs
    }
    fn pair(request: &mut ImportRequest) {
        request.selections.push(Selection {
            output_path: "table.csv".into(),
            destination: "experiments/results/table.csv".into(),
        });
    }
    #[test]
    fn recoverable_import_history_and_bounded_overwrite_review_survive_restart_and_run_forget() {
        let (root, mut ws, mut request) = setup(outputs());
        let old = format!(
            "<script>preserve()</script>\r\n{}",
            "old line\r\n".repeat(500)
        );
        std::fs::create_dir(root.path().join("results")).unwrap();
        std::fs::write(root.path().join("results/report.md"), &old).unwrap();
        let first = ws.experiment_import_prepare(&request).unwrap();
        let before = ws
            .experiment_import_target_preview(&request.operation_id, "report.md")
            .unwrap();
        assert_eq!(before.target.file_id, first.plan.items[0].target.file_id);
        assert_eq!(before.bytes, old.len() as u64);
        let text = before.preview.unwrap();
        assert!(text.text.starts_with("<script>preserve()</script>\r\n"));
        assert!(text.truncated && text.lines_shown <= 200);
        assert_eq!(
            std::fs::read_to_string(root.path().join("results/report.md")).unwrap(),
            old
        );
        let mut ids = vec![request.operation_id.clone()];
        for _ in 0..2 {
            request.operation_id = uuid::Uuid::new_v4().to_string();
            ws.experiment_import_prepare(&request).unwrap();
            ids.push(request.operation_id.clone());
        }
        ws.db
            .execute("UPDATE experiment_imports SET created_ms=8888", [])
            .unwrap();
        let run = ws.experiment_record(&request.run_id).unwrap().unwrap();
        ws.experiment_forget(&request.run_id, &run.summary.fingerprint)
            .unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let page = ws
            .experiment_import_history(&request.run_id, 2, None)
            .unwrap();
        assert!(page
            .items
            .iter()
            .all(|i| i.state == ImportState::AwaitingConfirmation
                && i.files == 1
                && i.committed == 0));
        let cursor = page.next_cursor.unwrap();
        let last = ws
            .experiment_import_history(&request.run_id, 2, Some(&cursor))
            .unwrap();
        assert!(last.next_cursor.is_none());
        let actual = page
            .items
            .into_iter()
            .chain(last.items)
            .map(|i| i.operation_id)
            .collect::<Vec<_>>();
        ids.sort();
        ids.reverse();
        assert_eq!(actual, ids);
        let all = ws.experiment_all_import_history(50, None).unwrap();
        assert_eq!(
            all.items
                .iter()
                .map(|i| i.operation_id.clone())
                .collect::<Vec<_>>(),
            actual
        );
        assert!(all
            .items
            .iter()
            .all(|i| i.run_id == request.run_id && i.entry.path == run.summary.request.entry.path));
        let mut foreign = cursor;
        foreign.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            ws.experiment_import_history(&request.run_id, 2, Some(&foreign))
                .unwrap_err()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        assert!(ws
            .experiment_import_history(&request.run_id, 51, None)
            .is_err());
        ws.experiment_import_cancel(&first.plan.request.operation_id, &first.fingerprint)
            .unwrap();
        ws.experiment_import_forget(&first.plan.request.operation_id, &first.fingerprint)
            .unwrap();
        assert_eq!(
            ws.experiment_import_history(&request.run_id, 50, None)
                .unwrap()
                .items
                .len(),
            2
        );
        std::fs::write(root.path().join("results/report.md"), "manual edit").unwrap();
        assert_eq!(
            ws.experiment_import_target_preview(&request.operation_id, "report.md")
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_TARGET_CHANGED"
        );
        assert_eq!(
            std::fs::read_to_string(root.path().join("results/report.md")).unwrap(),
            "manual edit"
        );
        let outside = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(outside.path(), b"outside sentinel").unwrap();
        std::fs::remove_file(root.path().join("results/report.md")).unwrap();
        std::fs::hard_link(outside.path(), root.path().join("results/report.md")).unwrap();
        assert_eq!(
            ws.experiment_import_target_preview(&request.operation_id, "report.md")
                .unwrap_err()
                .code,
            "UNSAFE_PATH"
        );
        assert_eq!(std::fs::read(outside.path()).unwrap(), b"outside sentinel");
    }
    #[test]
    fn independent_confirmation_imports_exact_bytes_and_preserves_durable_origins_after_run_forget()
    {
        let (root, mut ws, mut request) = setup(outputs());
        pair(&mut request);
        let preview = ws.experiment_import_prepare(&request).unwrap();
        assert_eq!(preview.state, ImportState::AwaitingConfirmation);
        assert_eq!(
            ws.experiment_import_next(&request.operation_id)
                .unwrap()
                .state,
            ImportState::AwaitingConfirmation
        );
        assert_eq!(ws.pending_count().unwrap(), 0);
        assert!(!root.path().join("results/report.md").exists());
        assert_eq!(
            ws.experiment_import_approve(&request.operation_id, "wrong")
                .unwrap_err()
                .code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
        let approved = ws
            .experiment_import_approve(&request.operation_id, &preview.fingerprint)
            .unwrap();
        assert!(approved.confirmed_ms.is_some());
        let first = ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(first.state, ImportState::Approved);
        assert_eq!(
            first
                .items
                .iter()
                .filter(|i| i.state == "committed")
                .count(),
            1
        );
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let done = ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(done.state, ImportState::Completed);
        assert_eq!(ws.pending_count().unwrap(), 2);
        assert_eq!(
            std::fs::read(root.path().join("results/report.md")).unwrap(),
            "# 中文报告\r\nvalue:18\r\n".as_bytes()
        );
        assert_eq!(
            std::fs::read(root.path().join("experiments/results/table.csv")).unwrap(),
            b"value\r\n18\r\n"
        );
        let before = ws.pending_count().unwrap();
        request.selections.reverse();
        assert_eq!(
            ws.experiment_import_prepare(&request).unwrap().fingerprint,
            done.fingerprint
        );
        assert_eq!(
            ws.experiment_import_approve(&request.operation_id, &done.fingerprint)
                .unwrap()
                .confirmed_ms,
            approved.confirmed_ms
        );
        assert_eq!(
            ws.experiment_import_next(&request.operation_id)
                .unwrap()
                .state,
            ImportState::Completed
        );
        assert_eq!(ws.pending_count().unwrap(), before);
        let item = done.items[1].entry.as_ref().unwrap();
        ws.rename(&item.path, "results/moved.md", &item.hash)
            .unwrap();
        let run = ws.experiment_record(&request.run_id).unwrap().unwrap();
        ws.experiment_forget(&request.run_id, &run.summary.fingerprint)
            .unwrap();
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        let origins = ws
            .experiment_artifact_origins(&item.file_id, 50, None)
            .unwrap()
            .items;
        assert_eq!(origins.len(), 1);
        assert_eq!(origins[0].source.fingerprint, run.summary.fingerprint);
        assert_eq!(origins[0].execution.approval_id, run.approval_id);
        assert_eq!(origins[0].execution.approved_ms, run.approved_ms);
        assert_eq!(origins[0].execution.exit_code, Some(0));
        assert_eq!(
            origins[0].source.request.entry.hash,
            hash(b"# retained source\r\n")
        );
        assert_eq!(origins[0].current_path.as_deref(), Some("results/moved.md"));
        assert_eq!(origins[0].output.sha256, item.hash);
        assert_eq!(
            std::fs::read(root.path().join("results/moved.md")).unwrap(),
            "# 中文报告\r\nvalue:18\r\n".as_bytes()
        );
    }
    #[test]
    fn full_review_preflight_checks_identity_revision_and_vault_before_any_destination_write() {
        let (root, mut ws, mut request) = setup(outputs());
        pair(&mut request);
        let existing = ws
            .write("results/report.md", "", b"manual original", "local")
            .unwrap();
        let preview = ws.experiment_import_prepare(&request).unwrap();
        let same_bytes = ws
            .write(&existing.path, &existing.hash, b"manual original", "local")
            .unwrap();
        assert!(same_bytes.revision > existing.revision);
        let before = ws.pending_count().unwrap();
        assert_eq!(
            ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_TARGET_CHANGED"
        );
        assert_eq!(ws.pending_count().unwrap(), before);
        assert!(!root.path().join("experiments/results/table.csv").exists());
        let mut second = request.clone();
        second.operation_id = uuid::Uuid::new_v4().to_string();
        let second_preview = ws.experiment_import_prepare(&second).unwrap();
        ws.rename(&existing.path, "results/old.md", &existing.hash)
            .unwrap();
        let replacement = ws
            .write(&existing.path, "", b"manual original", "local")
            .unwrap();
        assert_ne!(replacement.file_id, existing.file_id);
        assert_eq!(replacement.hash, existing.hash);
        assert_eq!(
            ws.experiment_import_approve(&second.operation_id, &second_preview.fingerprint)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_TARGET_CHANGED"
        );
        let original = ws.vault_id.clone();
        ws.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
                .unwrap_err()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        assert_eq!(
            ws.experiment_import_prepare(&request).unwrap_err().code,
            "VAULT_PERMISSION_CHANGED"
        );
        ws.vault_id = original;
        assert_eq!(
            std::fs::read(root.path().join(&existing.path)).unwrap(),
            b"manual original"
        );
    }
    #[test]
    fn later_conflicts_are_truthful_partial_results_and_never_replay_committed_files() {
        let (root, mut ws, mut request) = setup(outputs());
        pair(&mut request);
        let preview = ws.experiment_import_prepare(&request).unwrap();
        ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
            .unwrap();
        let first = ws.experiment_import_next(&request.operation_id).unwrap();
        let committed = first.items[0].entry.clone().unwrap();
        let edited = ws
            .write(
                &committed.path,
                &committed.hash,
                b"later user edit",
                "local",
            )
            .unwrap();
        let second_target = &preview.plan.items[1].target.path;
        let user = ws
            .write(second_target, "", b"user created second file", "local")
            .unwrap();
        let partial = ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(partial.state, ImportState::Partial);
        assert_eq!(partial.items[0].state, "committed");
        assert_eq!(partial.items[1].state, "failed");
        assert_eq!(
            partial.items[1].error.as_deref(),
            Some("EXPERIMENT_IMPORT_TARGET_CHANGED")
        );
        let before = ws.pending_count().unwrap();
        assert_eq!(
            ws.experiment_import_next(&request.operation_id)
                .unwrap()
                .state,
            ImportState::Partial
        );
        assert_eq!(ws.pending_count().unwrap(), before);
        assert_eq!(
            std::fs::read(root.path().join(&edited.path)).unwrap(),
            b"later user edit"
        );
        assert_eq!(
            std::fs::read(root.path().join(&user.path)).unwrap(),
            b"user created second file"
        );
        assert_eq!(
            ws.experiment_import_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .items[0]
                .entry
                .as_ref()
                .unwrap()
                .hash,
            committed.hash
        );
    }
    #[test]
    fn file_identity_outbox_and_provenance_commit_together_and_restart_settles_only_accepted_work()
    {
        let (root, mut ws, mut request) = setup(outputs());
        pair(&mut request);
        let preview = ws.experiment_import_prepare(&request).unwrap();
        ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
            .unwrap();
        ws.db.execute_batch("CREATE TRIGGER stop_provenance BEFORE UPDATE ON experiment_import_items WHEN NEW.state='committed' BEGIN SELECT RAISE(ABORT,'synthetic commit failure'); END").unwrap();
        let interrupted = ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(interrupted.state, ImportState::Approved);
        assert!(interrupted.items.iter().all(|item| item.entry.is_none()));
        assert_eq!(
            interrupted.items[0].error.as_deref(),
            Some("DATABASE_ERROR")
        );
        let item = &preview.plan.items[0];
        assert_eq!(
            hash(&std::fs::read(root.path().join(&item.target.path)).unwrap()),
            item.output.sha256
        );
        assert!(ws.entry(&item.target.path).unwrap().is_none());
        assert_eq!(ws.pending_count().unwrap(), 0);
        assert!(ws
            .experiment_artifact_origins(&item.target.file_id, 50, None)
            .unwrap()
            .items
            .is_empty());
        let cancelled = ws
            .experiment_import_cancel(&request.operation_id, &preview.fingerprint)
            .unwrap();
        assert_eq!(cancelled.state, ImportState::Cancelled);
        assert_eq!(cancelled.items[0].state, "pending");
        assert_eq!(cancelled.items[1].state, "cancelled");
        assert_eq!(
            ws.experiment_import_forget(&request.operation_id, &preview.fingerprint)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_BUSY"
        );
        ws.db.execute_batch("DROP TRIGGER stop_provenance").unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let recovered = ws
            .experiment_import_record(&request.operation_id)
            .unwrap()
            .unwrap();
        assert_eq!(recovered.state, ImportState::Cancelled);
        assert_eq!(recovered.items[0].state, "committed");
        assert_eq!(recovered.items[1].state, "cancelled");
        assert_eq!(ws.pending_count().unwrap(), 1);
        assert_eq!(
            recovered.items[0].entry.as_ref().unwrap().file_id,
            item.target.file_id
        );
        assert_eq!(
            ws.experiment_artifact_origins(&item.target.file_id, 50, None)
                .unwrap()
                .items
                .len(),
            1
        );
        assert!(!root
            .path()
            .join(&preview.plan.items[1].target.path)
            .exists());
        ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(ws.pending_count().unwrap(), 1);
        ws.experiment_import_forget(&request.operation_id, &preview.fingerprint)
            .unwrap();
        ws.experiment_import_forget(&request.operation_id, &preview.fingerprint)
            .unwrap();
        assert!(root.path().join(&item.target.path).is_file());
        assert_eq!(
            ws.experiment_import_prepare(&request).unwrap_err().code,
            "EXPERIMENT_IMPORT_FORGOTTEN"
        );
    }
    #[test]
    fn schema_seventeen_backup_preserves_files_outputs_identity_and_sync_queue() {
        let (root, mut ws, request) = setup(outputs());
        let note = ws.write("note.md", "", b"user content", "local").unwrap();
        ws.db.execute_batch("DROP TABLE experiment_import_items; DROP TABLE experiment_imports; PRAGMA user_version=17;").unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(
            ws.db
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            18
        );
        assert_eq!(ws.read("note.md").unwrap().entry.file_id, note.file_id);
        assert_eq!(ws.pending_count().unwrap(), 1);
        assert_eq!(
            ws.experiment_output(&request.run_id, "table.csv")
                .unwrap()
                .content(),
            b"value\r\n18\r\n"
        );
        let backup = std::fs::read_dir(root.path().join(".ainote"))
            .unwrap()
            .map(|e| e.unwrap().path())
            .find(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("host-schema17-")
            })
            .unwrap();
        let previous = Connection::open(backup).unwrap();
        assert_eq!(
            previous
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            17
        );
        assert_eq!(
            previous
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='experiment_imports'",
                    [],
                    |r| r.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(
            previous
                .query_row("SELECT COUNT(*) FROM outbox", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
        drop(previous);
        ws.db.execute_batch("PRAGMA user_version=19;").unwrap();
        drop(ws);
        assert_eq!(
            Workspace::open(root.path()).err().unwrap().code,
            "SCHEMA_INCOMPATIBLE"
        );
    }
    #[test]
    fn invalid_targets_duplicate_choices_corrupt_plans_and_wrong_operation_payloads_fail_closed() {
        let (root, mut ws, request) = setup(outputs());
        for path in [
            "../outside.md",
            ".git/config.md",
            "opennexus-records/persona/default.json",
            "results/executable.py",
            "results/table.csv",
            "results/picture.png",
            "results/report.md/child.md",
        ] {
            let mut candidate = request.clone();
            candidate.selections[0].destination = path.into();
            if path.ends_with("child.md") {
                std::fs::create_dir_all(root.path().join("results")).unwrap();
                std::fs::write(root.path().join("results/report.md"), b"parent file").unwrap();
            }
            assert!(
                ws.experiment_import_prepare(&candidate).is_err(),
                "accepted {path}"
            );
        }
        std::fs::remove_file(root.path().join("results/report.md")).unwrap();
        let mut duplicate = request.clone();
        duplicate.selections.push(request.selections[0].clone());
        assert!(ws.experiment_import_prepare(&duplicate).is_err());
        let preview = ws.experiment_import_prepare(&request).unwrap();
        let mut different = request.clone();
        different.selections[0].destination = "another.md".into();
        assert_eq!(
            ws.experiment_import_prepare(&different).unwrap_err().code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
        let mut alias = request.clone();
        alias.operation_id = preview.plan.items[0].write_id.clone();
        assert_eq!(
            ws.experiment_import_prepare(&alias).unwrap_err().code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
        let rejected = ws
            .experiment_import_cancel(&request.operation_id, &preview.fingerprint)
            .unwrap();
        assert_eq!(rejected.state, ImportState::Rejected);
        assert_eq!(
            ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
                .unwrap()
                .state,
            ImportState::Rejected
        );
        assert!(!root.path().join("results/report.md").exists());
        let mut corrupted = preview.plan.clone();
        corrupted.items[0].target.path = "secret.md".into();
        ws.db
            .execute(
                "UPDATE experiment_imports SET plan=?2 WHERE operation_id=?1",
                params![
                    request.operation_id,
                    serde_json::to_string(&corrupted).unwrap()
                ],
            )
            .unwrap();
        assert_eq!(
            ws.experiment_import_record(&request.operation_id)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_CORRUPT"
        );
        assert_eq!(ws.pending_count().unwrap(), 0);
    }
    #[test]
    fn pending_metadata_reservations_are_bounded_and_explicit_forget_reclaims_capacity() {
        let mut outputs = CollectedOutputs::default();
        for index in 0..32 {
            let output = CollectedOutputs::fixture(&format!("{index}.md"), b"report");
            outputs.total_bytes += output.total_bytes;
            outputs.files.extend(output.files);
        }
        let (_root, mut ws, mut request) = setup(outputs);
        let parent = vec!["x".repeat(200); 4].join("/");
        request.selections = (0..32)
            .map(|index| Selection {
                output_path: format!("{index}.md"),
                destination: format!("{parent}/{index}.md"),
            })
            .collect();
        let mut prepared = Vec::new();
        loop {
            request.operation_id = uuid::Uuid::new_v4().to_string();
            match ws.experiment_import_prepare(&request) {
                Ok(record) => prepared.push(record),
                Err(e) => {
                    assert_eq!(e.code, "EXPERIMENT_IMPORT_QUOTA");
                    break;
                }
            }
            assert!(prepared.len() < 100);
        }
        assert!(!prepared.is_empty());
        assert!(ws
            .experiment_import_record(&request.operation_id)
            .unwrap()
            .is_none());
        assert_eq!(ws.pending_count().unwrap(), 0);
        let first = &prepared[0];
        ws.experiment_import_cancel(&first.plan.request.operation_id, &first.fingerprint)
            .unwrap();
        ws.experiment_import_forget(&first.plan.request.operation_id, &first.fingerprint)
            .unwrap();
        ws.experiment_import_prepare(&request).unwrap();
        assert_eq!(
            ws.experiment_import_record(&first.plan.request.operation_id)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_FORGOTTEN"
        );
    }
    #[test]
    fn ordinary_writes_cannot_repurpose_reserved_import_or_child_identifiers() {
        let (root, mut ws, request) = setup(outputs());
        let preview = ws.experiment_import_prepare(&request).unwrap();
        let item = &preview.plan.items[0];
        let bytes = ws
            .experiment_output(&request.run_id, &item.output.path)
            .unwrap()
            .content()
            .to_vec();
        assert_eq!(
            ws.write_operation(&item.target.path, "", &bytes, "local", &item.write_id)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_AUTH_REQUIRED"
        );
        assert_eq!(
            ws.write_operation(
                "ordinary.md",
                "",
                b"plain note",
                "local",
                &request.operation_id
            )
            .unwrap_err()
            .code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
        assert!(!root.path().join(&item.target.path).exists());
        assert!(!root.path().join("ordinary.md").exists());
        assert_eq!(ws.pending_count().unwrap(), 0);
        ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
            .unwrap();
        assert_eq!(
            ws.write_operation(&item.target.path, "", &bytes, "local", &item.write_id)
                .unwrap_err()
                .code,
            "EXPERIMENT_IMPORT_AUTH_REQUIRED"
        );
        assert_eq!(
            ws.write_with_identity(
                &item.target.path,
                "",
                &bytes,
                "remote",
                &item.write_id,
                Some(&item.target.file_id)
            )
            .unwrap_err()
            .code,
            "EXPERIMENT_IMPORT_AUTH_REQUIRED"
        );
        assert!(!root.path().join(&item.target.path).exists());
        let done = ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(done.state, ImportState::Completed);
        assert_eq!(done.atomic_scope, "file");
    }
    #[test]
    fn restart_conflict_preserves_later_user_bytes_and_reports_failure_without_a_retry_click() {
        let (root, mut ws, request) = setup(outputs());
        let preview = ws.experiment_import_prepare(&request).unwrap();
        ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
            .unwrap();
        ws.db.execute_batch("CREATE TRIGGER stop_receipt BEFORE UPDATE ON experiment_import_items WHEN NEW.state='committed' BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END").unwrap();
        ws.experiment_import_next(&request.operation_id).unwrap();
        let path = &preview.plan.items[0].target.path;
        std::fs::write(
            root.path().join(path),
            b"user changed after accepted intent",
        )
        .unwrap();
        ws.db.execute_batch("DROP TRIGGER stop_receipt").unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let failed = ws
            .experiment_import_record(&request.operation_id)
            .unwrap()
            .unwrap();
        assert_eq!(failed.state, ImportState::Failed);
        assert_eq!(failed.items[0].error.as_deref(), Some("RECOVERY_CONFLICT"));
        assert!(failed.items[0].entry.is_none());
        assert_eq!(ws.pending_count().unwrap(), 0);
        ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(
            std::fs::read(root.path().join(path)).unwrap(),
            b"user changed after accepted intent"
        );
    }
    #[test]
    fn same_file_origins_page_equal_timestamps_with_a_vault_bound_cursor() {
        let (_root, mut ws, mut request) = setup(outputs());
        let mut ids = Vec::new();
        let mut file_id = String::new();
        for _ in 0..5 {
            request.operation_id = uuid::Uuid::new_v4().to_string();
            let preview = ws.experiment_import_prepare(&request).unwrap();
            ws.db
                .execute(
                    "UPDATE experiment_imports SET created_ms=7777 WHERE operation_id=?1",
                    [&request.operation_id],
                )
                .unwrap();
            ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
                .unwrap();
            let done = ws.experiment_import_next(&request.operation_id).unwrap();
            file_id = done.items[0].entry.as_ref().unwrap().file_id.clone();
            ids.push(request.operation_id.clone());
        }
        ids.sort();
        ids.reverse();
        let one = ws.experiment_artifact_origins(&file_id, 2, None).unwrap();
        let two = ws
            .experiment_artifact_origins(&file_id, 2, one.next_cursor.as_ref())
            .unwrap();
        let three = ws
            .experiment_artifact_origins(&file_id, 2, two.next_cursor.as_ref())
            .unwrap();
        let actual = one
            .items
            .iter()
            .chain(&two.items)
            .chain(&three.items)
            .map(|item| item.import_id.clone())
            .collect::<Vec<_>>();
        assert_eq!(actual, ids);
        assert!(three.next_cursor.is_none());
        let mut foreign = one.next_cursor.unwrap();
        foreign.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            ws.experiment_artifact_origins(&file_id, 2, Some(&foreign))
                .unwrap_err()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        assert!(ws.experiment_artifact_origins(&file_id, 0, None).is_err());
        assert!(ws.experiment_artifact_origins(&file_id, 51, None).is_err());
    }
    #[test]
    fn approved_png_import_survives_run_retention_and_keeps_original_image_bytes() {
        let mut png_bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut png_bytes, 2, 1);
            encoder.set_color(png::ColorType::Rgb);
            encoder.set_depth(png::BitDepth::Eight);
            let mut writer = encoder.write_header().unwrap();
            writer.write_image_data(&[255, 0, 0, 0, 255, 0]).unwrap();
            writer.finish().unwrap();
        }
        let (root, mut ws, mut request) = setup(CollectedOutputs::fixture("plot.png", &png_bytes));
        request.selections[0] = Selection {
            output_path: "plot.png".into(),
            destination: "attachments/plot.png".into(),
        };
        let preview = ws.experiment_import_prepare(&request).unwrap();
        ws.experiment_import_approve(&request.operation_id, &preview.fingerprint)
            .unwrap();
        let source = ws.experiment_record(&request.run_id).unwrap().unwrap();
        ws.experiment_forget(&request.run_id, &source.summary.fingerprint)
            .unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let done = ws.experiment_import_next(&request.operation_id).unwrap();
        assert_eq!(done.state, ImportState::Completed);
        assert_eq!(
            std::fs::read(root.path().join("attachments/plot.png")).unwrap(),
            png_bytes
        );
        let imported = done.items[0].entry.as_ref().unwrap();
        assert!(ws
            .tree()
            .unwrap()
            .iter()
            .any(|file| file.file_id == imported.file_id && file.path == "attachments/plot.png"));
        let origin = ws
            .experiment_artifact_origins(&imported.file_id, 50, None)
            .unwrap();
        assert_eq!(origin.items[0].output.kind, OutputKind::Png);
        assert_eq!(origin.items[0].output.sha256, hash(&png_bytes));
        assert_eq!(origin.items[0].execution.approval_id, source.approval_id);
        assert_eq!(ws.pending_count().unwrap(), 1);
    }
}

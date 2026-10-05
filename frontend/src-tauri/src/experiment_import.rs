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
    CREATE INDEX IF NOT EXISTS experiment_import_run ON experiment_imports(run_id,created_ms,operation_id);";
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
    fn canonical(&self) -> Result<Self> {
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
fn check_target(ws: &Workspace, expected: &SelectedFile) -> Result<()> {
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

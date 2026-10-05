//! Durable run/input records and one-time state claims. No IPC or process launch.
use crate::{
    experiment_input::{InputSummary, PreparedInputs, MAX_FILES, MAX_FILE_BYTES},
    experiment_log::CaptureSnapshot,
    experiment_outputs::{CollectedOutputs, OutputReport, ValidatedOutput},
    workspace::{HostError, Result, Workspace},
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    time::{SystemTime, UNIX_EPOCH},
};

const MAX_SUMMARY: usize = 128 * 1024;
const MAX_RESULT: usize = 8 * 1024 * 1024;
const MAX_RECORDS: i64 = 2048;
const MAX_STORE: i64 = 64 * 1024 * 1024;
const APPROVAL_MS: i64 = 5 * 60 * 1000;
pub(crate) const SCHEMA: &str = "
    CREATE TABLE IF NOT EXISTS experiment_runs (
      operation_id TEXT PRIMARY KEY, summary TEXT NOT NULL, state TEXT NOT NULL,
      created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
      approval_id TEXT, approved_ms INTEGER, error TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS experiment_inputs (
      operation_id TEXT NOT NULL, path TEXT NOT NULL, content BLOB NOT NULL,
      PRIMARY KEY(operation_id,path));
    CREATE TABLE IF NOT EXISTS experiment_outputs (
      operation_id TEXT NOT NULL, path TEXT NOT NULL, manifest TEXT NOT NULL, content BLOB NOT NULL,
      PRIMARY KEY(operation_id,path));";

fn corrupt() -> HostError {
    HostError::new("EXPERIMENT_RECORD_CORRUPT")
}
fn conflict() -> HostError {
    HostError::new("EXPERIMENT_STATE_CHANGED")
}
fn now() -> Result<i64> {
    i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| corrupt())?
            .as_millis(),
    )
    .map_err(|_| corrupt())
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunState {
    AwaitingConfirmation,
    Approved,
    Starting,
    Running,
    CancelRequested,
    Rejected,
    Cancelled,
    Completed,
    Failed,
    Limited,
    Interrupted,
}
impl RunState {
    fn name(self) -> &'static str {
        match self {
            Self::AwaitingConfirmation => "awaiting_confirmation",
            Self::Approved => "approved",
            Self::Starting => "starting",
            Self::Running => "running",
            Self::CancelRequested => "cancel_requested",
            Self::Rejected => "rejected",
            Self::Cancelled => "cancelled",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Limited => "limited",
            Self::Interrupted => "interrupted",
        }
    }
    fn parse(name: &str) -> Result<Self> {
        [
            Self::AwaitingConfirmation,
            Self::Approved,
            Self::Starting,
            Self::Running,
            Self::CancelRequested,
            Self::Rejected,
            Self::Cancelled,
            Self::Completed,
            Self::Failed,
            Self::Limited,
            Self::Interrupted,
        ]
        .into_iter()
        .find(|state| state.name() == name)
        .ok_or_else(corrupt)
    }
    fn terminal(self) -> bool {
        matches!(
            self,
            Self::Rejected
                | Self::Cancelled
                | Self::Completed
                | Self::Failed
                | Self::Limited
                | Self::Interrupted
        )
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Completed,
    Failed,
    Cancelled,
    Limited,
}
impl Outcome {
    fn state(self) -> RunState {
        match self {
            Self::Completed => RunState::Completed,
            Self::Failed => RunState::Failed,
            Self::Cancelled => RunState::Cancelled,
            Self::Limited => RunState::Limited,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunResult {
    pub outcome: Outcome,
    pub exit_code: Option<u32>,
    pub elapsed_ms: u64,
    pub error: Option<String>,
    pub user_cpu_ticks: Option<u64>,
    pub peak_memory_bytes: Option<u64>,
    pub final_disk_bytes: Option<u64>,
    pub logs: CaptureSnapshot,
    #[serde(default)]
    pub outputs: Option<OutputReport>,
}
impl RunResult {
    fn validate(&self, summary: &InputSummary) -> Result<()> {
        let budget = summary.validate()?.limits().log_bytes() / 2;
        if let Some(outputs) = &self.outputs {
            outputs
                .validate(summary.validate()?.limits().output_bytes())
                .map_err(|_| corrupt())?;
        }
        if self.error.as_ref().is_some_and(|code| {
            code.is_empty()
                || code.len() > 96
                || !code
                    .bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
        }) {
            return Err(corrupt());
        }
        for stream in [&self.logs.stdout, &self.logs.stderr] {
            if stream.retained_bytes > budget
                || stream.bytes_seen < stream.retained_bytes as u64
                || stream.truncated != (stream.bytes_seen > stream.retained_bytes as u64)
                || stream.text.len() < stream.retained_bytes
                || stream.text.len() > stream.retained_bytes.saturating_mul(3)
                || (!stream.invalid_utf8 && stream.text.len() != stream.retained_bytes)
            {
                return Err(corrupt());
            }
            if self.outcome == Outcome::Completed && (!stream.complete || stream.read_error) {
                return Err(corrupt());
            }
        }
        if self.outcome == Outcome::Completed && (self.exit_code != Some(0) || self.error.is_some())
        {
            return Err(corrupt());
        }
        if self.outcome == Outcome::Completed
            && matches!(self.outputs, Some(OutputReport::Rejected { .. }))
        {
            return Err(corrupt());
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize)]
pub struct RunRecord {
    pub summary: InputSummary,
    pub state: RunState,
    pub created_ms: i64,
    pub updated_ms: i64,
    pub approval_id: Option<String>,
    pub approved_ms: Option<i64>,
    pub error: Option<String>,
    pub result: Option<RunResult>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryCursor {
    pub vault_id: String,
    pub created_ms: i64,
    pub operation_id: String,
}
#[derive(Debug, Serialize)]
pub struct HistoryItem {
    pub operation_id: String,
    pub fingerprint: String,
    pub entry: crate::experiment_input::SelectedFile,
    pub runtime_id: String,
    pub state: RunState,
    pub created_ms: i64,
    pub updated_ms: i64,
    pub error: Option<String>,
    pub elapsed_ms: Option<u64>,
    pub exit_code: Option<u32>,
    pub output_files: Option<usize>,
    pub output_bytes: Option<u64>,
    pub skipped_files: Option<usize>,
}
#[derive(Debug, Serialize)]
pub struct HistoryPage {
    pub items: Vec<HistoryItem>,
    pub next_cursor: Option<HistoryCursor>,
}
#[derive(Debug, Serialize)]
pub struct RetentionUsage {
    pub records: u64,
    pub forgotten_operations: u64,
    pub payload_bytes: u64,
    pub reserved_bytes: u64,
    pub maximum_bytes: u64,
    pub maximum_records: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ForgetReceipt {
    pub vault_id: String,
    pub operation_id: String,
    pub fingerprint: String,
    pub forgotten_ms: i64,
}
fn forgotten(conn: &Connection, operation: &str) -> Result<Option<ForgetReceipt>> {
    let value: Option<(Option<String>,bool)> = conn.query_row(
        "SELECT CASE WHEN length(CAST(summary AS BLOB))<=1024 THEN summary END,result IS NULL AND approval_id IS NULL AND approved_ms IS NULL AND error='EXPERIMENT_RECORD_FORGOTTEN' FROM experiment_runs WHERE operation_id=?1 AND state='forgotten'",
        [operation],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    value.map(|(encoded, cleared)| {
        let receipt:ForgetReceipt=serde_json::from_str(&encoded.ok_or_else(corrupt)?).map_err(|_| corrupt())?;
        if !cleared || receipt.operation_id!=operation || receipt.forgotten_ms<0
            || uuid::Uuid::parse_str(&receipt.vault_id).is_err()
            || uuid::Uuid::parse_str(&receipt.operation_id).is_err()
            || receipt.fingerprint.len()!=64 || !receipt.fingerprint.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {return Err(corrupt());}
        let children:i64=conn.query_row("SELECT (SELECT COUNT(*) FROM experiment_inputs WHERE operation_id=?1)+(SELECT COUNT(*) FROM experiment_outputs WHERE operation_id=?1)",[operation],|r|r.get(0))?;
        if children!=0 {return Err(corrupt());}
        Ok(receipt)
    }).transpose()
}
/// Only a successful durable Approved -> Starting transition can mint this
/// owner. It cannot be decoded from RPC and is not itself a launch permission.
pub struct ClaimedRun {
    record: RunRecord,
    inputs: PreparedInputs,
}
impl ClaimedRun {
    pub fn record(&self) -> &RunRecord {
        &self.record
    }
    pub fn inputs(&self) -> &PreparedInputs {
        &self.inputs
    }
}
pub(crate) fn recover(conn: &Connection) -> Result<()> {
    let time = now()?;
    conn.execute("UPDATE experiment_runs SET state='interrupted',error='EXPERIMENT_HOST_RESTARTED',updated_ms=?1 WHERE state IN ('starting','running','cancel_requested')",[time])?;
    // A persisted review does not become fresh execution authority on restart.
    conn.execute("UPDATE experiment_runs SET state='awaiting_confirmation',approval_id=NULL,approved_ms=NULL,updated_ms=?1 WHERE state='approved'",[time])?;
    Ok(())
}
fn usage(conn: &Connection) -> Result<i64> {
    Ok(conn.query_row("SELECT COALESCE((SELECT SUM(length(content)) FROM experiment_inputs),0) + COALESCE((SELECT SUM(length(content)+length(CAST(manifest AS BLOB))) FROM experiment_outputs),0) + COALESCE((SELECT SUM(length(CAST(summary AS BLOB))+COALESCE(length(CAST(result AS BLOB)),0)) FROM experiment_runs),0)",[],|row|row.get(0))?)
}
fn reservations(conn: &Connection, except: Option<&str>) -> Result<i64> {
    let count:i64=conn.query_row("SELECT COUNT(*) FROM experiment_runs WHERE state IN ('awaiting_confirmation','approved','starting','running','cancel_requested') AND (?1 IS NULL OR operation_id<>?1)",[except],|row|row.get(0))?;
    count.checked_mul(MAX_RESULT as i64).ok_or_else(corrupt)
}
fn get(conn: &Connection, operation: &str) -> Result<Option<RunRecord>> {
    let record=conn.query_row("SELECT CASE WHEN length(CAST(summary AS BLOB))<=?2 THEN summary END,state,created_ms,updated_ms,approval_id,approved_ms,error,CASE WHEN length(CAST(result AS BLOB))<=?3 THEN result END, result IS NOT NULL FROM experiment_runs WHERE operation_id=?1",
        params![operation,MAX_SUMMARY,MAX_RESULT], |row| Ok((
            row.get::<_,Option<String>>(0)?,row.get::<_,String>(1)?,row.get::<_,i64>(2)?,row.get::<_,i64>(3)?,
            row.get::<_,Option<String>>(4)?,row.get::<_,Option<i64>>(5)?,row.get::<_,Option<String>>(6)?,
            row.get::<_,Option<String>>(7)?,row.get::<_,bool>(8)?,
        ))).optional()?;
    let Some((
        summary,
        state,
        created_ms,
        updated_ms,
        approval_id,
        approved_ms,
        error,
        result,
        has_result,
    )) = record
    else {
        return Ok(None);
    };
    let summary: InputSummary =
        serde_json::from_str(&summary.ok_or_else(corrupt)?).map_err(|_| corrupt())?;
    summary.validate()?;
    if summary.request.operation_id != operation
        || created_ms < 0
        || updated_ms < 0
        || (has_result && result.is_none())
    {
        return Err(corrupt());
    }
    let result: Option<RunResult> = result
        .map(|data| serde_json::from_str(&data).map_err(|_| corrupt()))
        .transpose()?;
    let state = RunState::parse(&state)?;
    if state == RunState::Completed && result.is_none() {
        return Err(corrupt());
    }
    if let Some(result) = &result {
        result.validate(&summary)?;
        if result.outcome.state() != state {
            return Err(corrupt());
        }
    }
    let (count,bytes):(usize,u64)=conn.query_row("SELECT COUNT(*),COALESCE(SUM(length(content)),0) FROM experiment_outputs WHERE operation_id=?1",[operation],|row|Ok((row.get(0)?,row.get(1)?)))?;
    match result.as_ref().and_then(|r| r.outputs.as_ref()) {
        Some(OutputReport::Collected { summary: outputs }) => {
            if count != outputs.files.len()
                || bytes != outputs.files.iter().map(|f| f.bytes).sum::<u64>()
            {
                return Err(corrupt());
            }
        }
        _ if count != 0 => return Err(corrupt()),
        _ => {}
    }
    if matches!(
        state,
        RunState::Approved | RunState::Starting | RunState::Running | RunState::CancelRequested
    ) && (approval_id
        .as_ref()
        .is_none_or(|id| uuid::Uuid::parse_str(id).is_err())
        || approved_ms.is_none_or(|time| time < 0))
    {
        return Err(corrupt());
    }
    Ok(Some(RunRecord {
        summary,
        state,
        created_ms,
        updated_ms,
        approval_id,
        approved_ms,
        error,
        result,
    }))
}
impl Workspace {
    pub fn experiment_record(&self, operation: &str) -> Result<Option<RunRecord>> {
        if let Some(receipt) = forgotten(&self.db, operation)? {
            return Err(HostError::new(if receipt.vault_id == self.vault_id {
                "EXPERIMENT_RECORD_FORGOTTEN"
            } else {
                "VAULT_PERMISSION_CHANGED"
            }));
        }
        let record = get(&self.db, operation)?;
        if record
            .as_ref()
            .is_some_and(|record| record.summary.request.vault_id != self.vault_id)
        {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        Ok(record)
    }
    /// Only metadata cards, never source/output BLOBs or full log text. Stable
    /// descending tuple cursors handle equal timestamps without duplicates.
    pub fn experiment_history(
        &self,
        limit: usize,
        cursor: Option<&HistoryCursor>,
    ) -> Result<HistoryPage> {
        if !(1..=50).contains(&limit) {
            return Err(HostError::new("EXPERIMENT_HISTORY_INVALID"));
        }
        if let Some(cursor) = cursor {
            if cursor.vault_id != self.vault_id {
                return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
            }
            if cursor.created_ms < 0 || uuid::Uuid::parse_str(&cursor.operation_id).is_err() {
                return Err(HostError::new("EXPERIMENT_HISTORY_INVALID"));
            }
        }
        let mut statement=self.db.prepare("SELECT operation_id FROM experiment_runs WHERE state<>'forgotten' AND (?1 IS NULL OR created_ms<?1 OR (created_ms=?1 AND operation_id<?2)) ORDER BY created_ms DESC,operation_id DESC LIMIT ?3")?;
        let ids = statement
            .query_map(
                params![
                    cursor.map(|c| c.created_ms),
                    cursor.map(|c| c.operation_id.as_str()),
                    limit + 1
                ],
                |r| r.get::<_, String>(0),
            )?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let has_more = ids.len() > limit;
        let mut items = Vec::new();
        for operation in ids.iter().take(limit) {
            let record = self.experiment_record(operation)?.ok_or_else(corrupt)?;
            let (output_files, output_bytes, skipped_files) =
                match record.result.as_ref().and_then(|r| r.outputs.as_ref()) {
                    Some(OutputReport::Collected { summary }) => (
                        Some(summary.files.len()),
                        Some(summary.files.iter().map(|f| f.bytes).sum()),
                        Some(summary.skipped.len()),
                    ),
                    _ => (None, None, None),
                };
            items.push(HistoryItem {
                operation_id: operation.clone(),
                fingerprint: record.summary.fingerprint,
                entry: record.summary.request.entry,
                runtime_id: record.summary.request.runtime_id,
                state: record.state,
                created_ms: record.created_ms,
                updated_ms: record.updated_ms,
                error: record.error,
                elapsed_ms: record.result.as_ref().map(|r| r.elapsed_ms),
                exit_code: record.result.as_ref().and_then(|r| r.exit_code),
                output_files,
                output_bytes,
                skipped_files,
            });
        }
        let next_cursor = if has_more {
            items.last().map(|i| HistoryCursor {
                vault_id: self.vault_id.clone(),
                created_ms: i.created_ms,
                operation_id: i.operation_id.clone(),
            })
        } else {
            None
        };
        Ok(HistoryPage { items, next_cursor })
    }
    pub fn experiment_retention_usage(&self) -> Result<RetentionUsage> {
        let (records,forgotten_operations):(u64,u64)=self.db.query_row("SELECT COUNT(CASE WHEN state<>'forgotten' THEN 1 END),COUNT(CASE WHEN state='forgotten' THEN 1 END) FROM experiment_runs",[],|r|Ok((r.get(0)?,r.get(1)?)))?;
        Ok(RetentionUsage {
            records,
            forgotten_operations,
            payload_bytes: u64::try_from(usage(&self.db)?).map_err(|_| corrupt())?,
            reserved_bytes: u64::try_from(reservations(&self.db, None)?).map_err(|_| corrupt())?,
            maximum_bytes: MAX_STORE as u64,
            maximum_records: MAX_RECORDS as u64,
        })
    }
    /// Explicit user cleanup of a terminal run. No filesystem operation: source
    /// files, imports and the separate native cleanup journal are unaffected.
    /// Retain a small identity tombstone so an old request can never run again.
    pub fn experiment_forget(
        &mut self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<ForgetReceipt> {
        if let Some(receipt) = forgotten(&self.db, operation)? {
            if receipt.vault_id != self.vault_id {
                return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
            }
            return if receipt.fingerprint == fingerprint {
                Ok(receipt)
            } else {
                Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"))
            };
        }
        let tx = self.db.transaction()?;
        let record = get(&tx, operation)?.ok_or_else(conflict)?;
        if record.summary.request.vault_id != self.vault_id {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        if record.summary.fingerprint != fingerprint {
            return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
        }
        if !record.state.terminal() {
            return Err(conflict());
        }
        let receipt = ForgetReceipt {
            vault_id: self.vault_id.clone(),
            operation_id: operation.into(),
            fingerprint: fingerprint.into(),
            forgotten_ms: now()?,
        };
        let encoded = serde_json::to_string(&receipt).map_err(|_| corrupt())?;
        if encoded.len() > 1024 {
            return Err(corrupt());
        }
        tx.execute(
            "DELETE FROM experiment_outputs WHERE operation_id=?1",
            [operation],
        )?;
        tx.execute(
            "DELETE FROM experiment_inputs WHERE operation_id=?1",
            [operation],
        )?;
        if tx.execute("UPDATE experiment_runs SET state='forgotten',summary=?2,result=NULL,approval_id=NULL,approved_ms=NULL,error='EXPERIMENT_RECORD_FORGOTTEN',updated_ms=?3 WHERE operation_id=?1 AND state=?4",params![operation,encoded,receipt.forgotten_ms,record.state.name()])? !=1 {return Err(conflict());}
        if usage(&tx)? + reservations(&tx, None)? > MAX_STORE {
            return Err(HostError::new("EXPERIMENT_RECORD_QUOTA"));
        }
        tx.commit()?;
        Ok(receipt)
    }
    /// Host routes must query an existing operation before preparing new inputs.
    /// This atomically persists all snapshot bytes with the waiting record.
    pub fn experiment_prepare(&mut self, inputs: PreparedInputs) -> Result<RunRecord> {
        let summary = inputs.summary();
        summary.validate()?;
        if summary.request.vault_id != self.vault_id {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        let operation = &summary.request.operation_id;
        if let Some(old) = self.experiment_record(operation)? {
            return if old.summary.fingerprint == summary.fingerprint {
                Ok(old)
            } else {
                Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"))
            };
        }
        let encoded = serde_json::to_string(summary).map_err(|_| corrupt())?;
        if encoded.len() > MAX_SUMMARY {
            return Err(corrupt());
        }
        let time = now()?;
        let tx = self.db.transaction()?;
        let count: i64 = tx.query_row(
            "SELECT COUNT(*) FROM experiment_runs WHERE state<>'forgotten'",
            [],
            |row| row.get(0),
        )?;
        if count >= MAX_RECORDS
            || usage(&tx)?
                + reservations(&tx, None)?
                + summary.total_bytes as i64
                + encoded.len() as i64
                + MAX_RESULT as i64
                > MAX_STORE
        {
            return Err(HostError::new("EXPERIMENT_RECORD_QUOTA"));
        }
        tx.execute("INSERT INTO experiment_runs(operation_id,summary,state,created_ms,updated_ms) VALUES(?1,?2,'awaiting_confirmation',?3,?3)",params![operation,encoded,time])?;
        for (path, content) in inputs.files() {
            tx.execute(
                "INSERT INTO experiment_inputs VALUES(?1,?2,?3)",
                params![operation, path, content],
            )?;
        }
        tx.commit()?;
        self.experiment_record(operation)?.ok_or_else(corrupt)
    }
    /// Only the trusted UI confirmation path may call this after an actual user
    /// decision. There is deliberately no Core RPC / model approval endpoint.
    #[allow(dead_code)] // The product confirmation route is the next integration batch.
    pub(crate) fn experiment_approve(
        &mut self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<RunRecord> {
        let old = self.experiment_record(operation)?.ok_or_else(conflict)?;
        if old.summary.fingerprint != fingerprint {
            return Err(conflict());
        }
        if old.state == RunState::Approved {
            return Ok(old);
        }
        if old.state != RunState::AwaitingConfirmation {
            return Err(conflict());
        }
        self.experiment_validate_current(&old)?;
        let time = now()?;
        let id = uuid::Uuid::new_v4().to_string();
        if self.db.execute("UPDATE experiment_runs SET state='approved',approval_id=?2,approved_ms=?3,updated_ms=?3 WHERE operation_id=?1 AND state='awaiting_confirmation'",params![operation,id,time])?!=1 {return Err(conflict())}
        self.experiment_record(operation)?.ok_or_else(corrupt)
    }
    pub fn experiment_reject(&mut self, operation: &str, fingerprint: &str) -> Result<RunRecord> {
        let old = self.experiment_record(operation)?.ok_or_else(conflict)?;
        if old.summary.fingerprint != fingerprint {
            return Err(conflict());
        }
        if old.state == RunState::Rejected {
            return Ok(old);
        }
        if old.state != RunState::AwaitingConfirmation {
            return Err(conflict());
        }
        self.db.execute("UPDATE experiment_runs SET state='rejected',updated_ms=?2 WHERE operation_id=?1 AND state='awaiting_confirmation'",params![operation,now()?])?;
        self.experiment_record(operation)?.ok_or_else(corrupt)
    }
    /// The runner must acquire a global concurrency slot before this claim, and
    /// still validate runtime integrity / create its owned isolation afterwards.
    pub(crate) fn experiment_claim(&mut self, operation: &str) -> Result<ClaimedRun> {
        let old = self.experiment_record(operation)?.ok_or_else(conflict)?;
        if old.state != RunState::Approved {
            return Err(conflict());
        }
        // New prepares reserve complete terminal logs. Legacy pending records
        // must also have capacity before any native execution can start.
        if usage(&self.db)? + reservations(&self.db, None)? > MAX_STORE {
            return Err(HostError::new("EXPERIMENT_RECORD_QUOTA"));
        }
        let time = now()?;
        let approved = old.approved_ms.ok_or_else(corrupt)?;
        if time < approved || time - approved > APPROVAL_MS {
            self.db.execute("UPDATE experiment_runs SET state='awaiting_confirmation',approval_id=NULL,approved_ms=NULL,updated_ms=?2 WHERE operation_id=?1 AND state='approved'",params![operation,time])?;
            return Err(HostError::new("EXPERIMENT_APPROVAL_EXPIRED"));
        }
        // Source edits, rename/deletion and switching vaults invalidate the
        // current approval; the execution bytes still come from the stored copy.
        self.experiment_validate_current(&old)?;
        let tx = self.db.transaction()?;
        let count: usize = tx.query_row(
            "SELECT COUNT(*) FROM experiment_inputs WHERE operation_id=?1",
            [operation],
            |row| row.get(0),
        )?;
        if count != old.summary.sizes.len() || count > MAX_FILES {
            return Err(corrupt());
        }
        let mut bytes = BTreeMap::new();
        for path in old.summary.sizes.keys() {
            let content:Option<Vec<u8>>=tx.query_row("SELECT content FROM experiment_inputs WHERE operation_id=?1 AND path=?2 AND length(content)<=?3",params![operation,path,MAX_FILE_BYTES],|row|row.get(0)).optional()?;
            bytes.insert(path.clone(), content.ok_or_else(corrupt)?);
        }
        let inputs = PreparedInputs::restore(old.summary.clone(), bytes)?;
        if tx.execute("UPDATE experiment_runs SET state='starting',updated_ms=?3 WHERE operation_id=?1 AND state='approved' AND approval_id=?2",params![operation,old.approval_id,time])?!=1 {return Err(conflict())}
        let record = get(&tx, operation)?.ok_or_else(corrupt)?;
        tx.commit()?;
        Ok(ClaimedRun { record, inputs })
    }
    pub(crate) fn experiment_running(&mut self, run: &ClaimedRun) -> Result<()> {
        let old = self.check_claim(run)?;
        if old.state != RunState::Starting {
            return Err(conflict());
        }
        let time = now()?;
        let approved = old.approved_ms.ok_or_else(corrupt)?;
        if time < approved || time - approved > APPROVAL_MS {
            self.db.execute("UPDATE experiment_runs SET state='awaiting_confirmation',approval_id=NULL,approved_ms=NULL,updated_ms=?2 WHERE operation_id=?1 AND state='starting'",params![old.summary.request.operation_id,time])?;
            return Err(HostError::new("EXPERIMENT_APPROVAL_EXPIRED"));
        }
        // Binding resources can take time. Revalidate again at the last durable
        // transition before the runner may resume its still-suspended process.
        self.experiment_validate_current(&old)?;
        if self.db.execute("UPDATE experiment_runs SET state='running',updated_ms=?3 WHERE operation_id=?1 AND state='starting' AND approval_id=?2",params![run.record.summary.request.operation_id,run.record.approval_id,now()?])?!=1 {return Err(conflict())}
        Ok(())
    }
    pub fn experiment_cancel(&mut self, operation: &str) -> Result<RunRecord> {
        let old = self.experiment_record(operation)?.ok_or_else(conflict)?;
        let state = match old.state {
            RunState::AwaitingConfirmation | RunState::Approved => RunState::Cancelled,
            RunState::Starting | RunState::Running => RunState::CancelRequested,
            _ => return Ok(old),
        };
        self.db.execute(
            "UPDATE experiment_runs SET state=?2,updated_ms=?3 WHERE operation_id=?1 AND state=?4",
            params![operation, state.name(), now()?, old.state.name()],
        )?;
        // CancelRequested is not a claim that the native Job has stopped.
        self.experiment_record(operation)?.ok_or_else(corrupt)
    }
    fn check_claim(&self, run: &ClaimedRun) -> Result<RunRecord> {
        let record = self
            .experiment_record(&run.record.summary.request.operation_id)?
            .ok_or_else(conflict)?;
        if record.summary.fingerprint != run.record.summary.fingerprint
            || record.approval_id != run.record.approval_id
        {
            return Err(conflict());
        }
        Ok(record)
    }
    fn experiment_validate_current(&mut self, record: &RunRecord) -> Result<()> {
        if let Err(error) = PreparedInputs::prepare(self, &record.summary.validate()?) {
            self.db.execute("UPDATE experiment_runs SET state='failed',error=?2,approval_id=NULL,approved_ms=NULL,updated_ms=?3 WHERE operation_id=?1 AND state=?4",
                params![record.summary.request.operation_id,error.code,now()?,record.state.name()])?;
            return Err(error);
        }
        Ok(())
    }
    /// Called only after the runner has observed/terminated the complete Job.
    /// Cancellation recorded before completion wins; retries return the first
    /// durable terminal result instead of overwriting or launching again.
    pub(crate) fn experiment_finish(
        &mut self,
        run: &ClaimedRun,
        result: RunResult,
    ) -> Result<RunRecord> {
        self.experiment_finish_outputs(run, result, None)
    }
    pub(crate) fn experiment_finish_outputs(
        &mut self,
        run: &ClaimedRun,
        mut result: RunResult,
        outputs: Option<&CollectedOutputs>,
    ) -> Result<RunRecord> {
        let old = self.check_claim(run)?;
        if old.state.terminal() {
            return Ok(old);
        }
        if !matches!(
            old.state,
            RunState::Starting | RunState::Running | RunState::CancelRequested
        ) {
            return Err(conflict());
        }
        if old.state == RunState::CancelRequested {
            result.outcome = Outcome::Cancelled
        }
        if old.state == RunState::Starting && result.outcome == Outcome::Completed {
            return Err(conflict());
        }
        if let Some(outputs) = outputs {
            let report = outputs.report();
            report
                .validate(old.summary.validate()?.limits().output_bytes())
                .map_err(|_| corrupt())?;
            result.outputs = Some(report);
        } else if matches!(result.outputs, Some(OutputReport::Collected { .. })) {
            return Err(corrupt());
        }
        result.validate(&old.summary)?;
        let mut encoded = serde_json::to_string(&result).map_err(|_| corrupt())?;
        if encoded.len() > MAX_RESULT {
            return Err(corrupt());
        }
        let tx = self.db.transaction()?;
        let mut stored = Vec::new();
        let mut output_bytes = 0i64;
        if let Some(outputs) = outputs {
            for output in &outputs.files {
                let manifest = serde_json::to_string(output.manifest()).map_err(|_| corrupt())?;
                output_bytes = output_bytes
                    .checked_add(manifest.len() as i64 + output.content().len() as i64)
                    .ok_or_else(corrupt)?;
                stored.push((output, manifest));
            }
        }
        let available =
            MAX_STORE - usage(&tx)? - reservations(&tx, Some(&old.summary.request.operation_id))?;
        if encoded.len() as i64 + output_bytes > available {
            // Keep complete real logs/measurements using the reserved terminal
            // space. Reject all output bytes atomically, never a partial set.
            if old.state != RunState::CancelRequested {
                result.outcome = Outcome::Failed;
            }
            result.error = Some("EXPERIMENT_RECORD_QUOTA".into());
            result.outputs = Some(OutputReport::Rejected {
                error: "EXPERIMENT_RECORD_QUOTA".into(),
            });
            encoded = serde_json::to_string(&result).map_err(|_| corrupt())?;
            stored.clear();
        }
        if encoded.len() > MAX_RESULT || encoded.len() as i64 > available {
            return Err(HostError::new("EXPERIMENT_RECORD_QUOTA"));
        }
        for (output, manifest) in stored {
            tx.execute(
                "INSERT INTO experiment_outputs VALUES(?1,?2,?3,?4)",
                params![
                    old.summary.request.operation_id,
                    output.manifest().path,
                    manifest,
                    output.content()
                ],
            )?;
        }
        if tx.execute("UPDATE experiment_runs SET state=?2,result=?3,error=?4,updated_ms=?5 WHERE operation_id=?1 AND state=?6",params![old.summary.request.operation_id,result.outcome.state().name(),encoded,result.error,now()?,old.state.name()])? !=1 { return Err(conflict()) }
        tx.commit()?;
        self.experiment_record(&old.summary.request.operation_id)?
            .ok_or_else(corrupt)
    }
    /// Internal Host read only. The result's current vault and exact manifest
    /// bind these bytes; no renderer/model supplies storage content here.
    pub(crate) fn experiment_output(&self, operation: &str, path: &str) -> Result<ValidatedOutput> {
        let record = self.experiment_record(operation)?.ok_or_else(conflict)?;
        let Some(OutputReport::Collected { summary }) = record.result.and_then(|r| r.outputs)
        else {
            return Err(HostError::new("EXPERIMENT_OUTPUT_NOT_FOUND"));
        };
        let expected = summary
            .files
            .iter()
            .find(|f| f.path == path)
            .ok_or_else(|| HostError::new("EXPERIMENT_OUTPUT_NOT_FOUND"))?;
        let limit = record.summary.validate()?.limits().output_bytes();
        let (manifest,content):(Option<String>,Option<Vec<u8>>)=self.db.query_row("SELECT CASE WHEN length(CAST(manifest AS BLOB))<=4096 THEN manifest END,CASE WHEN length(content)<=?3 AND length(content)<=16777216 THEN content END FROM experiment_outputs WHERE operation_id=?1 AND path=?2",params![operation,path,limit],|row|Ok((row.get(0)?,row.get(1)?))).optional()?.ok_or_else(corrupt)?;
        let stored: crate::experiment_outputs::OutputManifest =
            serde_json::from_str(&manifest.ok_or_else(corrupt)?).map_err(|_| corrupt())?;
        if &stored != expected {
            return Err(corrupt());
        }
        ValidatedOutput::restore(expected, content.ok_or_else(corrupt)?, limit)
            .map_err(|_| corrupt())
    }
    /// Data-only read for the future trusted preview/broker. It validates the
    /// complete stored file while returning at most 256 KiB per response.
    pub fn experiment_output_read(
        &self,
        operation: &str,
        path: &str,
        offset: usize,
        limit: usize,
    ) -> Result<crate::experiment_outputs::OutputChunk> {
        use base64::{engine::general_purpose::STANDARD, Engine};
        if !(1..=256 * 1024).contains(&limit) {
            return Err(HostError::new("EXPERIMENT_OUTPUT_RANGE_INVALID"));
        }
        let output = self.experiment_output(operation, path)?;
        if offset > output.content().len() {
            return Err(HostError::new("EXPERIMENT_OUTPUT_RANGE_INVALID"));
        }
        let end = offset.saturating_add(limit).min(output.content().len());
        Ok(crate::experiment_outputs::OutputChunk {
            manifest: output.manifest().clone(),
            offset,
            next_offset: (end < output.content().len()).then_some(end),
            content_base64: STANDARD.encode(&output.content()[offset..end]),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_input::{RunRequest, SelectedFile},
        experiment_policy::ExecutionLimits,
        experiment_runtime::RUNTIME_ID,
    };
    use std::fs;
    fn setup() -> (tempfile::TempDir, Workspace, RunRequest) {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("experiments")).unwrap();
        fs::write(
            root.path().join("experiments/中文 #%.py"),
            "print('中文')\r\n",
        )
        .unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let entry = ws.read("experiments/中文 #%.py").unwrap().entry;
        let request = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry: SelectedFile {
                file_id: entry.file_id,
                path: entry.path,
                hash: entry.hash,
                revision: entry.revision,
            },
            inputs: vec![],
            limits: ExecutionLimits::default(),
        };
        (root, ws, request)
    }
    fn prepare(ws: &mut Workspace, request: &RunRequest) -> RunRecord {
        let inputs = PreparedInputs::prepare(ws, &request.validate().unwrap()).unwrap();
        ws.experiment_prepare(inputs).unwrap()
    }
    fn approve(ws: &mut Workspace, record: &RunRecord) {
        ws.experiment_approve(
            &record.summary.request.operation_id,
            &record.summary.fingerprint,
        )
        .unwrap();
    }
    fn result() -> RunResult {
        let mut stdout = crate::experiment_log::LogBuffer::new(8192).snapshot();
        stdout.complete = true;
        RunResult {
            outcome: Outcome::Completed,
            exit_code: Some(0),
            elapsed_ms: 17,
            error: None,
            user_cpu_ticks: Some(120),
            peak_memory_bytes: Some(1000),
            final_disk_bytes: Some(0),
            outputs: None,
            logs: CaptureSnapshot {
                stderr: stdout.clone(),
                stdout,
            },
        }
    }
    #[test]
    fn one_durable_claim_and_completion_survive_retries_and_restart() {
        let (root, mut ws, request) = setup();
        let waiting = prepare(&mut ws, &request);
        assert_eq!(waiting.state, RunState::AwaitingConfirmation);
        assert!(ws.experiment_claim(&request.operation_id).is_err());
        assert!(ws
            .experiment_approve(&request.operation_id, &"a".repeat(64))
            .is_err());
        approve(&mut ws, &waiting);
        let first = ws
            .experiment_record(&request.operation_id)
            .unwrap()
            .unwrap();
        approve(&mut ws, &waiting);
        assert_eq!(
            ws.experiment_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .approval_id,
            first.approval_id
        );
        let run = ws.experiment_claim(&request.operation_id).unwrap();
        assert_eq!(
            run.inputs().files().next().unwrap().1,
            "print('中文')\r\n".as_bytes()
        );
        assert!(ws.experiment_claim(&request.operation_id).is_err());
        assert!(
            ws.experiment_finish(&run, result()).is_err(),
            "must mark actual running first"
        );
        ws.experiment_running(&run).unwrap();
        let completed = ws.experiment_finish(&run, result()).unwrap();
        assert_eq!(completed.state, RunState::Completed);
        assert_eq!(
            usage(&ws.db).unwrap() as usize,
            completed.summary.total_bytes
                + serde_json::to_string(&completed.summary).unwrap().len()
                + serde_json::to_string(completed.result.as_ref().unwrap())
                    .unwrap()
                    .len(),
            "quota must count UTF-8 bytes, including Chinese paths"
        );
        let mut different = result();
        different.outcome = Outcome::Failed;
        different.exit_code = Some(1);
        assert_eq!(
            ws.experiment_finish(&run, different)
                .unwrap()
                .result
                .unwrap()
                .exit_code,
            Some(0)
        );
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let same = prepare(&mut ws, &request);
        assert_eq!(same.state, RunState::Completed);
        assert_eq!(same.result.unwrap().elapsed_ms, 17);
        assert!(ws.experiment_claim(&request.operation_id).is_err());
        assert_eq!(
            ws.db
                .query_row("SELECT COUNT(*) FROM experiment_inputs", [], |row| row
                    .get::<_, i64>(0))
                .unwrap(),
            1
        );
    }
    #[test]
    fn changed_proposal_cannot_replace_same_operation_and_source_changes_revoke_review() {
        let (root, mut ws, request) = setup();
        let waiting = prepare(&mut ws, &request);
        let mut changed = request.clone();
        changed.limits.wall_seconds += 1;
        let inputs = PreparedInputs::prepare(&mut ws, &changed.validate().unwrap()).unwrap();
        assert_eq!(
            ws.experiment_prepare(inputs).err().unwrap().code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
        approve(&mut ws, &waiting);
        fs::write(root.path().join(&request.entry.path), "print('changed')").unwrap();
        assert_eq!(
            ws.experiment_claim(&request.operation_id)
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_INPUT_CHANGED"
        );
        let failed = ws
            .experiment_record(&request.operation_id)
            .unwrap()
            .unwrap();
        assert_eq!(failed.state, RunState::Failed);
        assert_eq!(failed.error.as_deref(), Some("EXPERIMENT_INPUT_CHANGED"));
        assert!(failed.approval_id.is_none());
        assert!(ws
            .experiment_approve(&request.operation_id, &waiting.summary.fingerprint)
            .is_err());
    }
    #[test]
    fn rejection_and_cancellation_never_claim_or_fake_a_stopped_job() {
        let (_root, mut ws, mut request) = setup();
        let waiting = prepare(&mut ws, &request);
        ws.experiment_reject(&request.operation_id, &waiting.summary.fingerprint)
            .unwrap();
        assert!(ws.experiment_claim(&request.operation_id).is_err());
        request.operation_id = uuid::Uuid::new_v4().to_string();
        prepare(&mut ws, &request);
        assert_eq!(
            ws.experiment_cancel(&request.operation_id).unwrap().state,
            RunState::Cancelled
        );
        assert!(ws.experiment_claim(&request.operation_id).is_err());
        request.operation_id = uuid::Uuid::new_v4().to_string();
        let waiting = prepare(&mut ws, &request);
        approve(&mut ws, &waiting);
        let run = ws.experiment_claim(&request.operation_id).unwrap();
        assert_eq!(
            ws.experiment_cancel(&request.operation_id).unwrap().state,
            RunState::CancelRequested
        );
        assert!(ws.experiment_running(&run).is_err());
        assert_eq!(
            ws.experiment_finish(&run, result()).unwrap().state,
            RunState::Cancelled
        );
        assert_eq!(
            ws.experiment_cancel(&request.operation_id).unwrap().state,
            RunState::Cancelled
        );
    }
    #[test]
    fn restart_requires_new_confirmation_and_interrupts_only_started_records() {
        let (root, mut ws, mut request) = setup();
        let approved = prepare(&mut ws, &request);
        approve(&mut ws, &approved);
        let approved_id = request.operation_id.clone();
        let mut started = vec![];
        for state in [
            RunState::Starting,
            RunState::Running,
            RunState::CancelRequested,
        ] {
            request.operation_id = uuid::Uuid::new_v4().to_string();
            let waiting = prepare(&mut ws, &request);
            approve(&mut ws, &waiting);
            let run = ws.experiment_claim(&request.operation_id).unwrap();
            if state != RunState::Starting {
                ws.experiment_running(&run).unwrap()
            }
            if state == RunState::CancelRequested {
                ws.experiment_cancel(&request.operation_id).unwrap();
            }
            started.push(request.operation_id.clone());
        }
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let reviewed = ws.experiment_record(&approved_id).unwrap().unwrap();
        assert_eq!(reviewed.state, RunState::AwaitingConfirmation);
        assert!(reviewed.approval_id.is_none() && reviewed.approved_ms.is_none());
        assert!(ws.experiment_claim(&approved_id).is_err());
        for id in started {
            let record = ws.experiment_record(&id).unwrap().unwrap();
            assert_eq!(record.state, RunState::Interrupted);
            assert_eq!(record.error.as_deref(), Some("EXPERIMENT_HOST_RESTARTED"));
            assert!(ws.experiment_claim(&id).is_err());
        }
    }
    #[test]
    fn expiry_and_snapshot_corruption_cannot_mint_a_claim() {
        let (_root, mut ws, request) = setup();
        let waiting = prepare(&mut ws, &request);
        approve(&mut ws, &waiting);
        ws.db
            .execute(
                "UPDATE experiment_runs SET approved_ms=?2 WHERE operation_id=?1",
                params![request.operation_id, now().unwrap() - APPROVAL_MS - 1000],
            )
            .unwrap();
        assert_eq!(
            ws.experiment_claim(&request.operation_id)
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_APPROVAL_EXPIRED"
        );
        assert_eq!(
            ws.experiment_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::AwaitingConfirmation
        );
        approve(&mut ws, &waiting);
        ws.db
            .execute(
                "UPDATE experiment_inputs SET content=?2 WHERE operation_id=?1",
                params![request.operation_id, b"changed".as_slice()],
            )
            .unwrap();
        assert_eq!(
            ws.experiment_claim(&request.operation_id)
                .err()
                .unwrap()
                .code,
            "EXPERIMENT_RECORD_CORRUPT"
        );
        assert_eq!(
            ws.experiment_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::Approved
        );
        ws.db
            .execute(
                "UPDATE experiment_runs SET summary='{}' WHERE operation_id=?1",
                [&request.operation_id],
            )
            .unwrap();
        assert!(ws.experiment_record(&request.operation_id).is_err());
    }
    #[test]
    fn pre_resume_transition_rechecks_source_expiry_and_recorded_cancellation() {
        for scenario in ["source", "expiry", "cancel"] {
            let (root, mut ws, request) = setup();
            let waiting = prepare(&mut ws, &request);
            approve(&mut ws, &waiting);
            let run = ws.experiment_claim(&request.operation_id).unwrap();
            match scenario {
                "source" => {
                    fs::write(root.path().join(&request.entry.path), b"print('changed')").unwrap()
                }
                "expiry" => {
                    ws.db
                        .execute(
                            "UPDATE experiment_runs SET approved_ms=?2 WHERE operation_id=?1",
                            params![request.operation_id, now().unwrap() - APPROVAL_MS - 1000],
                        )
                        .unwrap();
                }
                "cancel" => {
                    ws.experiment_cancel(&request.operation_id).unwrap();
                }
                _ => unreachable!(),
            }
            assert!(ws.experiment_running(&run).is_err());
            let state = ws
                .experiment_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .state;
            assert_eq!(
                state,
                match scenario {
                    "source" => RunState::Failed,
                    "expiry" => RunState::AwaitingConfirmation,
                    "cancel" => RunState::CancelRequested,
                    _ => unreachable!(),
                }
            );
        }
    }
    #[test]
    fn broken_logs_and_nonzero_exit_cannot_be_completed_and_cannot_cross_vault() {
        let (_root, mut ws, request) = setup();
        let waiting = prepare(&mut ws, &request);
        approve(&mut ws, &waiting);
        let run = ws.experiment_claim(&request.operation_id).unwrap();
        ws.experiment_running(&run).unwrap();
        for broken in [
            {
                let mut r = result();
                r.exit_code = Some(1);
                r
            },
            {
                let mut r = result();
                r.logs.stdout.complete = false;
                r
            },
            {
                let mut r = result();
                r.logs.stderr.read_error = true;
                r
            },
            {
                let mut r = result();
                r.logs.stdout.retained_bytes = 1;
                r
            },
        ] {
            assert_eq!(
                ws.experiment_finish(&run, broken).err().unwrap().code,
                "EXPERIMENT_RECORD_CORRUPT"
            )
        }
        assert_eq!(
            ws.experiment_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::Running
        );
        ws.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            ws.experiment_finish(&run, result()).err().unwrap().code,
            "VAULT_PERMISSION_CHANGED"
        );
    }
    #[test]
    fn quota_rejects_atomically_without_snapshot_or_sync_side_effects() {
        let (_root, mut ws, request) = setup();
        ws.db
            .execute(
                "INSERT INTO experiment_inputs VALUES('quota-fixture','fixture',zeroblob(?1))",
                [MAX_STORE],
            )
            .unwrap();
        let inputs = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        assert_eq!(
            ws.experiment_prepare(inputs).err().unwrap().code,
            "EXPERIMENT_RECORD_QUOTA"
        );
        assert!(ws
            .experiment_record(&request.operation_id)
            .unwrap()
            .is_none());
        assert_eq!(
            ws.db
                .query_row(
                    "SELECT COUNT(*) FROM experiment_inputs WHERE operation_id=?1",
                    [&request.operation_id],
                    |row| row.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(ws.pending_count().unwrap(), 0);
    }
    #[test]
    fn schema_fifteen_migration_backs_up_notes_before_adding_run_tables() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let note = ws.write("note.md", "", b"old note", "local").unwrap();
        ws.db
            .execute_batch(
                "DROP TABLE experiment_outputs; DROP TABLE experiment_inputs; DROP TABLE experiment_runs; PRAGMA user_version=15;",
            )
            .unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.read("note.md").unwrap().entry.file_id, note.file_id);
        assert_eq!(ws.pending_count().unwrap(), 1);
        assert_eq!(
            ws.db
                .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            17
        );
        let backup = fs::read_dir(root.path().join(".ainote"))
            .unwrap()
            .filter_map(|entry| entry.ok())
            .find(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with("host-schema15-")
            })
            .unwrap();
        let previous = Connection::open(backup.path()).unwrap();
        assert_eq!(
            previous
                .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            15
        );
        assert_eq!(
            previous
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='experiment_runs'",
                    [],
                    |row| row.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(
            previous
                .query_row("SELECT COUNT(*) FROM outbox", [], |row| row
                    .get::<_, i64>(0))
                .unwrap(),
            1
        );
    }
    fn started(ws: &mut Workspace, request: &RunRequest) -> ClaimedRun {
        let waiting = prepare(ws, request);
        approve(ws, &waiting);
        let run = ws.experiment_claim(&request.operation_id).unwrap();
        ws.experiment_running(&run).unwrap();
        run
    }
    #[test]
    fn outputs_are_atomic_immutable_vault_bound_and_verified_after_restart() {
        use base64::{engine::general_purpose::STANDARD, Engine};
        let (root, mut ws, request) = setup();
        let run = started(&mut ws, &request);
        let bytes = "# 中文结果\r\nvalue:18\r\n".as_bytes();
        let outputs = CollectedOutputs::fixture("结果/report.md", bytes);
        let done = ws
            .experiment_finish_outputs(&run, result(), Some(&outputs))
            .unwrap();
        assert_eq!(done.state, RunState::Completed);
        let first = ws
            .experiment_output_read(&request.operation_id, "结果/report.md", 0, 4)
            .unwrap();
        assert_eq!(STANDARD.decode(first.content_base64).unwrap(), &bytes[..4]);
        assert_eq!(first.next_offset, Some(4));
        assert_eq!(
            STANDARD
                .decode(
                    ws.experiment_output_read(&request.operation_id, "结果/report.md", 4, 256)
                        .unwrap()
                        .content_base64
                )
                .unwrap(),
            &bytes[4..]
        );
        assert!(ws
            .experiment_output_read(&request.operation_id, "结果/report.md", bytes.len() + 1, 16)
            .is_err());
        assert!(ws
            .experiment_output_read(&request.operation_id, "结果/report.md", 0, 256 * 1024 + 1)
            .is_err());
        let changed = CollectedOutputs::fixture("结果/report.md", b"changed");
        ws.experiment_finish_outputs(&run, result(), Some(&changed))
            .unwrap();
        assert_eq!(
            ws.experiment_output(&request.operation_id, "结果/report.md")
                .unwrap()
                .content(),
            bytes
        );
        let vault = ws.vault_id.clone();
        ws.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            ws.experiment_output(&request.operation_id, "结果/report.md")
                .unwrap_err()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        ws.vault_id = vault;
        assert!(ws
            .experiment_output(&uuid::Uuid::new_v4().to_string(), "结果/report.md")
            .is_err());
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        assert_eq!(
            ws.experiment_output(&request.operation_id, "结果/report.md")
                .unwrap()
                .content(),
            bytes
        );
        ws.db
            .execute(
                "UPDATE experiment_outputs SET content=?2 WHERE operation_id=?1",
                params![request.operation_id, vec![b'X'; bytes.len()]],
            )
            .unwrap();
        assert_eq!(
            ws.experiment_output(&request.operation_id, "结果/report.md")
                .unwrap_err()
                .code,
            "EXPERIMENT_RECORD_CORRUPT"
        );
        assert_eq!(
            fs::read(root.path().join("experiments/中文 #%.py")).unwrap(),
            "print('中文')\r\n".as_bytes()
        );
    }
    #[test]
    fn failing_terminal_commit_rolls_back_all_output_bytes() {
        let (_root, mut ws, request) = setup();
        let run = started(&mut ws, &request);
        let outputs = CollectedOutputs::fixture("report.txt", b"immutable");
        ws.db.execute_batch("CREATE TRIGGER fail_terminal BEFORE UPDATE ON experiment_runs WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'synthetic terminal failure'); END;").unwrap();
        assert!(ws
            .experiment_finish_outputs(&run, result(), Some(&outputs))
            .is_err());
        assert_eq!(
            ws.experiment_record(&request.operation_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::Running
        );
        assert_eq!(
            ws.db
                .query_row("SELECT COUNT(*) FROM experiment_outputs", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
        ws.db.execute_batch("DROP TRIGGER fail_terminal;").unwrap();
        ws.experiment_finish_outputs(&run, result(), Some(&outputs))
            .unwrap();
        assert_eq!(
            ws.experiment_output(&request.operation_id, "report.txt")
                .unwrap()
                .content(),
            b"immutable"
        );
    }
    #[test]
    fn output_quota_rejects_all_files_but_keeps_reserved_real_terminal_logs() {
        let (_root, mut ws, mut request) = setup();
        let outputs = CollectedOutputs::fixture("large.txt", &vec![b'A'; 16 * 1024 * 1024]);
        for _ in 0..3 {
            request.operation_id = uuid::Uuid::new_v4().to_string();
            let run = started(&mut ws, &request);
            assert_eq!(
                ws.experiment_finish_outputs(&run, result(), Some(&outputs))
                    .unwrap()
                    .state,
                RunState::Completed
            );
        }
        request.operation_id = uuid::Uuid::new_v4().to_string();
        let run = started(&mut ws, &request);
        let mut terminal = result();
        let mut log = crate::experiment_log::LogBuffer::new(8192);
        log.append(b"real completed calculation");
        terminal.logs.stdout = log.snapshot();
        terminal.logs.stdout.complete = true;
        let done = ws
            .experiment_finish_outputs(&run, terminal, Some(&outputs))
            .unwrap();
        assert_eq!(done.state, RunState::Failed);
        let actual = done.result.unwrap();
        assert_eq!(actual.error.as_deref(), Some("EXPERIMENT_RECORD_QUOTA"));
        assert_eq!(actual.logs.stdout.text, "real completed calculation");
        assert_eq!(actual.exit_code, Some(0));
        assert!(matches!(
            actual.outputs,
            Some(OutputReport::Rejected { .. })
        ));
        assert_eq!(
            ws.db
                .query_row(
                    "SELECT COUNT(*) FROM experiment_outputs WHERE operation_id=?1",
                    [&request.operation_id],
                    |r| r.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        assert!(usage(&ws.db).unwrap() <= MAX_STORE);
        assert_eq!(reservations(&ws.db, None).unwrap(), 0);
    }
    #[test]
    fn schema_sixteen_backup_preserves_legacy_results_and_future_schema_is_refused() {
        let (root, mut ws, request) = setup();
        let run = started(&mut ws, &request);
        let done = ws.experiment_finish(&run, result()).unwrap();
        let mut legacy = serde_json::to_value(done.result.unwrap()).unwrap();
        legacy.as_object_mut().unwrap().remove("outputs");
        ws.db
            .execute(
                "UPDATE experiment_runs SET result=?2 WHERE operation_id=?1",
                params![
                    request.operation_id,
                    serde_json::to_string(&legacy).unwrap()
                ],
            )
            .unwrap();
        ws.db
            .execute_batch("DROP TABLE experiment_outputs; PRAGMA user_version=16;")
            .unwrap();
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        assert_eq!(
            ws.db
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            17
        );
        assert!(ws
            .experiment_record(&request.operation_id)
            .unwrap()
            .unwrap()
            .result
            .unwrap()
            .outputs
            .is_none());
        let backup = fs::read_dir(root.path().join(".ainote"))
            .unwrap()
            .map(|e| e.unwrap().path())
            .find(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("host-schema16-")
            })
            .unwrap();
        let previous = Connection::open(backup).unwrap();
        assert_eq!(
            previous
                .query_row("SELECT COUNT(*) FROM experiment_runs", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert_eq!(
            previous
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='experiment_outputs'",
                    [],
                    |r| r.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        drop(previous);
        ws.db.execute_batch("PRAGMA user_version=18;").unwrap();
        drop(ws);
        assert_eq!(
            Workspace::open(root.path()).err().unwrap().code,
            "SCHEMA_INCOMPATIBLE"
        );
    }
}

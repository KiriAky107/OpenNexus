//! Durable Agent review identities. Core prepares/reads/cancels proposals;
//! only the main window's native user decision can approve them. The desktop
//! adapter can consume that recorded consent through its owned Runner.
use crate::{
    experiment_import::{ImportRecord, ImportRequest, ImportState, Selection},
    experiment_input::{fingerprint, PreparedInputs, RunRequest, SelectedFile, MAX_FILES},
    experiment_policy::ExecutionLimits,
    experiment_runtime::RUNTIME_ID,
    experiment_store::{RunRecord, RunState},
    workspace::{hash, HostError, Result, Workspace},
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub(crate) const SCHEMA: &str = "
 CREATE TABLE IF NOT EXISTS experiment_agent_reviews (
   operation_id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
   request_id TEXT NOT NULL, kind TEXT NOT NULL, arguments_hash TEXT NOT NULL,
   superseded_by TEXT);
 CREATE UNIQUE INDEX IF NOT EXISTS experiment_agent_current ON experiment_agent_reviews
   (agent_run_id,tool_call_id,kind) WHERE superseded_by IS NULL;";
const MAX_REVIEWS: i64 = 8192;
fn bad() -> HostError {
    HostError::new("AGENT_EXPERIMENT_REVIEW_INVALID")
}
fn changed() -> HostError {
    HostError::new("AGENT_EXPERIMENT_REVIEW_CHANGED")
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub agent_run_id: String,
    pub tool_call_id: String,
    pub request_id: String,
}
impl Context {
    fn validate(&self) -> Result<()> {
        if [&self.agent_run_id, &self.tool_call_id, &self.request_id]
            .into_iter()
            .any(|id| {
                id.is_empty()
                    || id.len() > 160
                    || !id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-.".contains(&b))
            })
        {
            return Err(bad());
        }
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RunDraft {
    vault_id: String,
    context: Context,
    entry_file_id: String,
    input_file_ids: Vec<String>,
    limits: ExecutionLimits,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ImportDraft {
    vault_id: String,
    context: Context,
    source_run_id: String,
    selections: Vec<Selection>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Read {
    vault_id: String,
    context: Context,
    operation_id: String,
}
#[cfg(any(windows, test))]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Execution {
    vault_id: String,
    context: Context,
    operation_id: String,
    fingerprint: String,
}
#[cfg(any(windows, test))]
fn execution_review<T>(
    ws: &Workspace,
    p: &Execution,
    review: impl FnOnce(&Workspace, &str, &str) -> Result<Review<T>>,
) -> Result<Review<T>> {
    bound(ws, &p.vault_id)?;
    p.context.validate()?;
    let r = review(ws, &p.operation_id, &p.fingerprint)?;
    if r.context != p.context {
        return Err(changed());
    }
    Ok(r)
}
#[derive(Debug, Serialize)]
pub struct Review<T> {
    pub kind: &'static str,
    pub vault_id: String,
    pub operation_id: String,
    pub fingerprint: String,
    pub context: Context,
    pub record: T,
}
struct Binding {
    operation: String,
    context: Context,
    kind: String,
    arguments_hash: String,
    superseded_by: Option<String>,
}
fn row(r: &rusqlite::Row<'_>) -> rusqlite::Result<Binding> {
    Ok(Binding {
        operation: r.get(0)?,
        context: Context {
            agent_run_id: r.get(1)?,
            tool_call_id: r.get(2)?,
            request_id: r.get(3)?,
        },
        kind: r.get(4)?,
        arguments_hash: r.get(5)?,
        superseded_by: r.get(6)?,
    })
}
impl Binding {
    fn validate(&self) -> Result<()> {
        self.context.validate()?;
        if uuid::Uuid::parse_str(&self.operation).is_err()
            || !matches!(self.kind.as_str(), "run" | "import")
            || self.arguments_hash.len() != 64
            || !self
                .arguments_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || self
                .superseded_by
                .as_ref()
                .is_some_and(|id| uuid::Uuid::parse_str(id).is_err())
        {
            return Err(bad());
        }
        Ok(())
    }
}
fn args_hash(value: impl Serialize) -> Result<String> {
    Ok(hash(&serde_json::to_vec(&value).map_err(|_| bad())?))
}
fn bound(ws: &Workspace, vault: &str) -> Result<()> {
    if ws.vault_id != vault {
        return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
    }
    Ok(())
}
fn current(
    ws: &Workspace,
    context: &Context,
    kind: &str,
    arguments_hash: &str,
) -> Result<Option<Binding>> {
    context.validate()?;
    let result = ws.db.query_row("SELECT * FROM experiment_agent_reviews WHERE agent_run_id=?1 AND tool_call_id=?2 AND kind=?3 AND superseded_by IS NULL",
        params![context.agent_run_id,context.tool_call_id,kind],row).optional()?;
    if let Some(b) = &result {
        b.validate()?;
        if b.context != *context || b.arguments_hash != arguments_hash {
            return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
        }
    }
    Ok(result)
}
fn lookup(ws: &Workspace, operation: &str, kind: &str) -> Result<Binding> {
    let b = ws
        .db
        .query_row(
            "SELECT * FROM experiment_agent_reviews WHERE operation_id=?1",
            [operation],
            row,
        )
        .optional()?
        .ok_or_else(changed)?;
    b.validate()?;
    if b.kind != kind || b.superseded_by.is_some() {
        return Err(changed());
    }
    Ok(b)
}
fn publish(
    ws: &mut Workspace,
    old: Option<&Binding>,
    operation: &str,
    context: &Context,
    kind: &str,
    arguments_hash: &str,
) -> Result<()> {
    let tx = ws.db.transaction()?;
    if let Some(old) = old {
        if tx.execute("UPDATE experiment_agent_reviews SET superseded_by=?2 WHERE operation_id=?1 AND superseded_by IS NULL",params![old.operation,operation])? != 1 {
            return Err(changed());
        }
        // An old dialog cannot approve the retired proposal. Its audit and
        // snapshots are retained; no files are executed or imported here.
        if kind == "run" {
            tx.execute("UPDATE experiment_runs SET state='rejected' WHERE operation_id=?1 AND state='awaiting_confirmation'",[&old.operation])?;
        } else {
            tx.execute("UPDATE experiment_imports SET state='rejected' WHERE operation_id=?1 AND state='awaiting_confirmation'",[&old.operation])?;
            tx.execute("UPDATE experiment_import_items SET state='rejected' WHERE import_id=?1 AND state='pending'",[&old.operation])?;
        }
    }
    if tx.execute("UPDATE experiment_agent_reviews SET superseded_by=NULL WHERE operation_id=?1 AND superseded_by=operation_id AND agent_run_id=?2 AND tool_call_id=?3 AND request_id=?4 AND kind=?5 AND arguments_hash=?6",
        params![operation,context.agent_run_id,context.tool_call_id,context.request_id,kind,arguments_hash])? != 1 {
        return Err(changed());
    }
    tx.commit()?;
    Ok(())
}
fn reserve(
    ws: &Workspace,
    operation: &str,
    context: &Context,
    kind: &str,
    arguments_hash: &str,
) -> Result<()> {
    capacity(ws)?;
    if ws.experiment_record(operation)?.is_some()
        || ws.experiment_import_record(operation)?.is_some()
        || ws.operation(operation)?.is_some()
    {
        return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
    }
    // Self-supersession is a quarantined reservation, never an active review.
    // Associate origin before creating a record: any later SQL failure leaves
    // the proposal blocked from both the Agent and generic manual start paths.
    ws.db.execute(
        "INSERT INTO experiment_agent_reviews VALUES(?1,?2,?3,?4,?5,?6,?1)",
        params![
            operation,
            context.agent_run_id,
            context.tool_call_id,
            context.request_id,
            kind,
            arguments_hash
        ],
    )?;
    Ok(())
}
fn capacity(ws: &Workspace) -> Result<()> {
    let count: i64 = ws
        .db
        .query_row("SELECT COUNT(*) FROM experiment_agent_reviews", [], |r| {
            r.get(0)
        })?;
    if count >= MAX_REVIEWS {
        return Err(HostError::new("AGENT_EXPERIMENT_REVIEW_QUOTA"));
    }
    Ok(())
}
fn run_review(ws: &Workspace, b: Binding) -> Result<Review<RunRecord>> {
    let record = ws.experiment_record(&b.operation)?.ok_or_else(changed)?;
    Ok(Review {
        kind: "experiment_run",
        vault_id: ws.vault_id.clone(),
        operation_id: b.operation,
        fingerprint: record.summary.fingerprint.clone(),
        context: b.context,
        record,
    })
}
fn import_review(ws: &Workspace, b: Binding) -> Result<Review<ImportRecord>> {
    let record = ws
        .experiment_import_record(&b.operation)?
        .ok_or_else(changed)?;
    Ok(Review {
        kind: "experiment_import",
        vault_id: ws.vault_id.clone(),
        operation_id: b.operation,
        fingerprint: record.fingerprint.clone(),
        context: b.context,
        record,
    })
}
fn prepare_run(ws: &mut Workspace, mut p: RunDraft) -> Result<Review<RunRecord>> {
    bound(ws, &p.vault_id)?;
    p.context.validate()?;
    p.limits.validate()?;
    if p.input_file_ids.len() >= MAX_FILES
        || uuid::Uuid::parse_str(&p.entry_file_id).is_err()
        || p.input_file_ids
            .iter()
            .any(|id| uuid::Uuid::parse_str(id).is_err())
    {
        return Err(bad());
    }
    p.input_file_ids.sort();
    let hash = args_hash((&p.entry_file_id, &p.input_file_ids, &p.limits))?;
    let old = current(ws, &p.context, "run", &hash)?;
    if let Some(b) = &old {
        let record = ws.experiment_record(&b.operation)?.ok_or_else(changed)?;
        if record.state != RunState::AwaitingConfirmation {
            return run_review(ws, lookup(ws, &b.operation, "run")?);
        }
    }
    let entries = ws.scan()?;
    let select = |id: &str| -> Result<SelectedFile> {
        let e = entries
            .iter()
            .find(|e| !e.deleted && !e.is_folder && e.file_id == id)
            .ok_or_else(|| HostError::new("EXPERIMENT_INPUT_CHANGED"))?;
        Ok(SelectedFile {
            file_id: e.file_id.clone(),
            path: e.path.clone(),
            hash: e.hash.clone(),
            revision: e.revision,
        })
    };
    let mut request = RunRequest {
        vault_id: p.vault_id,
        operation_id: old
            .as_ref()
            .map_or_else(|| uuid::Uuid::new_v4().to_string(), |b| b.operation.clone()),
        runtime_id: RUNTIME_ID.into(),
        entry: select(&p.entry_file_id)?,
        inputs: p
            .input_file_ids
            .iter()
            .map(|id| select(id))
            .collect::<Result<Vec<_>>>()?,
        limits: p.limits,
    };
    let validated = request.validate()?;
    if let Some(b) = &old {
        let record = ws.experiment_record(&b.operation)?.ok_or_else(changed)?;
        if record.summary.fingerprint == fingerprint(validated.request())? {
            return run_review(ws, lookup(ws, &b.operation, "run")?);
        }
        request.operation_id = uuid::Uuid::new_v4().to_string();
    }
    capacity(ws)?;
    let inputs = PreparedInputs::prepare(ws, &request.validate()?)?;
    reserve(ws, &request.operation_id, &p.context, "run", &hash)?;
    ws.experiment_prepare(inputs)?;
    publish(
        ws,
        old.as_ref(),
        &request.operation_id,
        &p.context,
        "run",
        &hash,
    )?;
    run_review(ws, lookup(ws, &request.operation_id, "run")?)
}
fn prepare_import(ws: &mut Workspace, mut p: ImportDraft) -> Result<Review<ImportRecord>> {
    bound(ws, &p.vault_id)?;
    p.context.validate()?;
    if p.selections.is_empty() || p.selections.len() > MAX_FILES {
        return Err(bad());
    }
    p.selections
        .sort_by(|a, b| a.destination.cmp(&b.destination));
    let hash = args_hash((&p.source_run_id, &p.selections))?;
    let old = current(ws, &p.context, "import", &hash)?;
    if let Some(b) = &old {
        let r = ws
            .experiment_import_record(&b.operation)?
            .ok_or_else(changed)?;
        if r.state != ImportState::AwaitingConfirmation {
            return import_review(ws, lookup(ws, &b.operation, "import")?);
        }
        ws.scan()?;
        let unchanged = r
            .plan
            .items
            .iter()
            .all(|item| crate::experiment_import::check_target(ws, &item.target).is_ok());
        if unchanged {
            return import_review(ws, lookup(ws, &b.operation, "import")?);
        }
    }
    let request = ImportRequest {
        vault_id: p.vault_id,
        operation_id: uuid::Uuid::new_v4().to_string(),
        run_id: p.source_run_id,
        selections: p.selections,
    }
    .canonical()?;
    capacity(ws)?;
    reserve(ws, &request.operation_id, &p.context, "import", &hash)?;
    ws.experiment_import_prepare(&request)?;
    publish(
        ws,
        old.as_ref(),
        &request.operation_id,
        &p.context,
        "import",
        &hash,
    )?;
    import_review(ws, lookup(ws, &request.operation_id, "import")?)
}
impl Workspace {
    /// Includes retired Agent proposals: generic manual start/import must not
    /// bypass the Agent's still-pending policy decision or revive stale consent.
    pub fn experiment_is_agent_operation(&self, operation: &str) -> Result<bool> {
        Ok(self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM experiment_agent_reviews WHERE operation_id=?1)",
            [operation],
            |r| r.get(0),
        )?)
    }
    pub fn experiment_agent_run_review(
        &self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<Review<RunRecord>> {
        let r = run_review(self, lookup(self, operation, "run")?)?;
        if r.fingerprint != fingerprint {
            return Err(changed());
        }
        Ok(r)
    }
    pub fn experiment_agent_import_review(
        &self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<Review<ImportRecord>> {
        let r = import_review(self, lookup(self, operation, "import")?)?;
        if r.fingerprint != fingerprint {
            return Err(changed());
        }
        Ok(r)
    }
    /// Trusted main-window/native decision only. Deliberately no Core route.
    /// Approval does not start a process or commit any import item.
    pub fn experiment_agent_approve_run_from_user(
        &mut self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<Review<RunRecord>> {
        let r = self.experiment_agent_run_review(operation, fingerprint)?;
        if r.record.state == RunState::AwaitingConfirmation {
            self.experiment_approve(operation, fingerprint)?;
        }
        self.experiment_agent_run_review(operation, fingerprint)
    }
    pub fn experiment_agent_approve_import_from_user(
        &mut self,
        operation: &str,
        fingerprint: &str,
    ) -> Result<Review<ImportRecord>> {
        let r = self.experiment_agent_import_review(operation, fingerprint)?;
        if r.record.state == ImportState::AwaitingConfirmation {
            self.experiment_import_approve(operation, fingerprint)?;
        }
        self.experiment_agent_import_review(operation, fingerprint)
    }
}
fn core_value(review: impl Serialize) -> Result<Value> {
    let mut value = serde_json::to_value(review).map_err(|_| bad())?;
    // Keep original capture counters and audit bytes in the Host. Agent reads
    // receive bounded display prefixes, including explicit display truncation.
    for name in ["stdout", "stderr"] {
        let Some(stream) = value
            .get_mut("record")
            .and_then(|record| record.get_mut("result"))
            .and_then(|result| result.get_mut("logs"))
            .and_then(|logs| logs.get_mut(name))
            .and_then(Value::as_object_mut)
        else {
            continue;
        };
        if let Some(text) = stream.get("text").and_then(Value::as_str) {
            let mut end = text.len().min(16 * 1024);
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            let truncated = end < text.len();
            let prefix = text[..end].to_owned();
            stream.insert("text".into(), Value::String(prefix));
            stream.insert("displayed_bytes".into(), Value::from(end));
            stream.insert("display_truncated".into(), Value::Bool(truncated));
        }
    }
    Ok(value)
}

/// This adapter is called by the desktop transport with its own Runner,
/// resource root and original vault slot. None are supplied by Core/model JSON.
#[cfg(windows)]
pub fn is_execution_request(request: &Value) -> bool {
    matches!(
        request["rpc"].as_str(),
        Some("workspace.experiment_agent.start_run" | "workspace.experiment_agent.import_next")
    )
}
#[cfg(windows)]
pub fn dispatch_execution(
    runner: &crate::experiment_runner::Runner,
    workspace: &crate::experiment_execution::WorkspaceSlot,
    resource_root: &std::path::Path,
    request: &Value,
) -> std::result::Result<Value, String> {
    let execute = || -> Result<Value> {
        let p: Execution = serde_json::from_value(request["params"].clone()).map_err(|_| bad())?;
        let mut slot = workspace.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
        let ws = slot
            .as_mut()
            .ok_or_else(|| HostError::new("VAULT_NOT_OPEN"))?;
        match request["rpc"].as_str().unwrap_or_default() {
            "workspace.experiment_agent.start_run" => {
                let r = execution_review(ws, &p, Workspace::experiment_agent_run_review)?;
                if r.record.state == RunState::AwaitingConfirmation {
                    return Err(HostError::new("EXPERIMENT_CONFIRMATION_REQUIRED"));
                }
                if r.record.state != RunState::Approved {
                    return core_value(r);
                }
                drop(slot);
                if let Err(error) = runner.start_approved_for_vault(
                    workspace.clone(),
                    &p.vault_id,
                    resource_root.to_owned(),
                    &p.operation_id,
                ) {
                    // A duplicate concurrent start can race the first claim.
                    // Reconcile the exact same bound record instead of replaying.
                    let mut slot = workspace.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
                    let ws = slot
                        .as_mut()
                        .ok_or_else(|| HostError::new("VAULT_NOT_OPEN"))?;
                    let current = execution_review(ws, &p, Workspace::experiment_agent_run_review)?;
                    if current.record.state == RunState::Approved
                        || current.record.state == RunState::AwaitingConfirmation
                    {
                        return Err(error);
                    }
                    return core_value(current);
                }
                let mut slot = workspace.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
                let ws = slot
                    .as_mut()
                    .ok_or_else(|| HostError::new("VAULT_NOT_OPEN"))?;
                core_value(execution_review(
                    ws,
                    &p,
                    Workspace::experiment_agent_run_review,
                )?)
            }
            "workspace.experiment_agent.import_next" => {
                let r = execution_review(ws, &p, Workspace::experiment_agent_import_review)?;
                if r.record.state == ImportState::AwaitingConfirmation {
                    return Err(HostError::new("EXPERIMENT_CONFIRMATION_REQUIRED"));
                }
                if r.record.state != ImportState::Approved {
                    return core_value(r);
                }
                drop(slot);
                runner.import_next_for_vault(workspace, &p.vault_id, &p.operation_id)?;
                let mut slot = workspace.lock().map_err(|_| HostError::new("HOST_BUSY"))?;
                let ws = slot
                    .as_mut()
                    .ok_or_else(|| HostError::new("VAULT_NOT_OPEN"))?;
                core_value(execution_review(
                    ws,
                    &p,
                    Workspace::experiment_agent_import_review,
                )?)
            }
            _ => Err(HostError::new("HOST_METHOD_DENIED")),
        }
    };
    execute().map_err(|error| error.code)
}
pub(crate) fn dispatch(ws: &mut Workspace, request: &Value) -> std::result::Result<Value, String> {
    let mut run = || -> Result<Value> {
        let params = &request["params"];
        match request["rpc"].as_str().unwrap_or_default() {
            "workspace.experiment_agent.prepare_run" => {
                let p = serde_json::from_value::<RunDraft>(params.clone()).map_err(|_| bad())?;
                core_value(prepare_run(ws, p)?)
            }
            "workspace.experiment_agent.prepare_import" => {
                let p = serde_json::from_value::<ImportDraft>(params.clone()).map_err(|_| bad())?;
                core_value(prepare_import(ws, p)?)
            }
            method @ ("workspace.experiment_agent.run_record"
            | "workspace.experiment_agent.import_record"
            | "workspace.experiment_agent.cancel_run"
            | "workspace.experiment_agent.cancel_import") => {
                let p = serde_json::from_value::<Read>(params.clone()).map_err(|_| bad())?;
                bound(ws, &p.vault_id)?;
                p.context.validate()?;
                let kind = if method.ends_with("_run") || method.ends_with("run_record") {
                    "run"
                } else {
                    "import"
                };
                let b = lookup(ws, &p.operation_id, kind)?;
                if b.context != p.context {
                    return Err(changed());
                }
                if method.ends_with("cancel_run") {
                    crate::experiment_owner::RunOwner::request_cancel(ws, &p.operation_id)?;
                }
                if method.ends_with("cancel_import") {
                    let r = ws
                        .experiment_import_record(&p.operation_id)?
                        .ok_or_else(changed)?;
                    ws.experiment_import_cancel(&p.operation_id, &r.fingerprint)?;
                }
                if kind == "run" {
                    core_value(run_review(ws, b)?)
                } else {
                    core_value(import_review(ws, b)?)
                }
            }
            _ => Err(HostError::new("HOST_METHOD_DENIED")),
        }
    };
    run().map_err(|e| e.code)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_log::{CaptureSnapshot, LogBuffer},
        experiment_outputs::CollectedOutputs,
        experiment_store::{Outcome, RunResult},
    };
    use serde_json::json;
    fn context() -> Context {
        Context {
            agent_run_id: "run_parent".into(),
            tool_call_id: "call_run".into(),
            request_id: "permission_one".into(),
        }
    }
    fn setup() -> (tempfile::TempDir, Workspace, String) {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(
            root.path().join("experiments/课程 #%.py"),
            "print('中文')\r\n",
        )
        .unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let id = ws.read("experiments/课程 #%.py").unwrap().entry.file_id;
        (root, ws, id)
    }
    fn draft(ws: &Workspace, id: &str) -> Value {
        json!({"vault_id":ws.vault_id,"context":context(),
        "entry_file_id":id,"input_file_ids":[],"limits":ExecutionLimits::default()})
    }
    fn rpc(ws: &mut Workspace, method: &str, params: Value) -> std::result::Result<Value, String> {
        crate::workspace_broker::dispatch(
            ws,
            &json!({"rpc":format!("workspace.experiment_agent.{method}"),"params":params}),
        )
    }
    fn read(ws: &Workspace, review: &Value) -> Value {
        json!({"vault_id":ws.vault_id,"context":review["context"],"operation_id":review["operation_id"]})
    }
    fn approve(ws: &mut Workspace, review: &Value) -> Review<RunRecord> {
        ws.experiment_agent_approve_run_from_user(
            review["operation_id"].as_str().unwrap(),
            review["fingerprint"].as_str().unwrap(),
        )
        .unwrap()
    }
    fn completed(ws: &mut Workspace, review: &Value) -> String {
        completed_with_log(ws, review, b"")
    }
    fn completed_with_log(ws: &mut Workspace, review: &Value, stdout: &[u8]) -> String {
        let r = approve(ws, review);
        let op = r.operation_id;
        let claimed = ws.experiment_claim(&op).unwrap();
        ws.experiment_running(&claimed).unwrap();
        let mut buffer = LogBuffer::new(32 * 1024);
        buffer.append(stdout);
        let mut stream = buffer.snapshot();
        stream.complete = true;
        let result = RunResult {
            outcome: Outcome::Completed,
            exit_code: Some(0),
            elapsed_ms: 1,
            error: None,
            user_cpu_ticks: Some(10),
            peak_memory_bytes: Some(1000),
            final_disk_bytes: Some(30),
            logs: CaptureSnapshot {
                stdout: stream.clone(),
                stderr: {
                    let mut empty = LogBuffer::new(8192).snapshot();
                    empty.complete = true;
                    empty
                },
            },
            outputs: None,
        };
        let output = CollectedOutputs::fixture("report.md", "# 中文成果\r\n".as_bytes());
        ws.experiment_finish_outputs(&claimed, result, Some(&output))
            .unwrap();
        op
    }
    fn import_draft(ws: &Workspace, source: &str) -> Value {
        let mut c = context();
        c.tool_call_id = "call_import".into();
        c.request_id = "permission_import".into();
        json!({"vault_id":ws.vault_id,"context":c,"source_run_id":source,
            "selections":[{"output_path":"report.md","destination":"成果/report.md"}]})
    }
    #[test]
    fn preparation_replays_and_native_consent_does_not_start_a_process() {
        let (root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        assert_eq!(r["record"]["state"], "awaiting_confirmation");
        assert_eq!(rpc(&mut ws, "prepare_run", p.clone()).unwrap(), r);
        let a = approve(&mut ws, &r);
        assert_eq!(a.record.state, RunState::Approved);
        assert!(a.record.result.is_none());
        let b = approve(&mut ws, &r);
        assert_eq!(a.record.approval_id, b.record.approval_id);
        std::fs::write(
            root.path().join("experiments/课程 #%.py"),
            "print('human edit')\r\n",
        )
        .unwrap();
        let original = rpc(&mut ws, "prepare_run", p).unwrap();
        assert_eq!(original["operation_id"], r["operation_id"]);
        assert_eq!(original["record"]["summary"], r["record"]["summary"]);
        assert_eq!(original["record"]["state"], "approved");
        assert!(ws.experiment_claim(&a.operation_id).is_err());
    }
    #[test]
    fn refreshing_changed_source_retires_the_old_dialog_and_keeps_its_audit() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let old = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        let doc = ws.read("experiments/课程 #%.py").unwrap();
        ws.write(
            &doc.entry.path,
            &doc.entry.hash,
            "# new 中文\r\n".as_bytes(),
            "local",
        )
        .unwrap();
        let new = rpc(&mut ws, "prepare_run", p).unwrap();
        assert_ne!(old["operation_id"], new["operation_id"]);
        assert_eq!(
            ws.experiment_record(old["operation_id"].as_str().unwrap())
                .unwrap()
                .unwrap()
                .state,
            RunState::Rejected
        );
        assert_eq!(
            ws.experiment_agent_approve_run_from_user(
                old["operation_id"].as_str().unwrap(),
                old["fingerprint"].as_str().unwrap()
            )
            .unwrap_err()
            .code,
            "AGENT_EXPERIMENT_REVIEW_CHANGED"
        );
        let parent: String = ws
            .db
            .query_row(
                "SELECT superseded_by FROM experiment_agent_reviews WHERE operation_id=?1",
                [old["operation_id"].as_str().unwrap()],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(parent, new["operation_id"]);
        assert!(ws
            .experiment_is_agent_operation(old["operation_id"].as_str().unwrap())
            .unwrap());
        assert_eq!(approve(&mut ws, &new).record.state, RunState::Approved);
    }
    #[test]
    fn another_task_tool_ticket_kind_or_arguments_cannot_use_consent() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        for key in ["agent_run_id", "tool_call_id", "request_id"] {
            let mut q = read(&ws, &r);
            q["context"][key] = json!("foreign");
            assert_eq!(
                rpc(&mut ws, "run_record", q).unwrap_err(),
                "AGENT_EXPERIMENT_REVIEW_CHANGED"
            );
        }
        let q = read(&ws, &r);
        assert_eq!(
            rpc(&mut ws, "import_record", q).unwrap_err(),
            "AGENT_EXPERIMENT_REVIEW_CHANGED"
        );
        let mut q = p.clone();
        q["limits"]["wall_seconds"] = json!(90);
        assert_eq!(
            rpc(&mut ws, "prepare_run", q).unwrap_err(),
            "OPERATION_PAYLOAD_CONFLICT"
        );
        let mut q = p;
        q["context"]["request_id"] = json!("new_ticket");
        assert_eq!(
            rpc(&mut ws, "prepare_run", q).unwrap_err(),
            "OPERATION_PAYLOAD_CONFLICT"
        );
        assert!(ws
            .experiment_record(r["operation_id"].as_str().unwrap())
            .unwrap()
            .unwrap()
            .approval_id
            .is_none());
    }
    #[test]
    fn unprivileged_dispatch_cannot_approve_launch_or_import_and_cannot_add_shell_parameters() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        for name in [
            "approve_run",
            "confirm_run",
            "start_run",
            "approve_import",
            "confirm_import",
            "import_next",
            "shell",
        ] {
            assert_eq!(
                rpc(&mut ws, name, p.clone()).unwrap_err(),
                "HOST_METHOD_DENIED"
            );
        }
        for (key, value) in [
            ("approved", json!(true)),
            ("command", json!("python")),
            ("env", json!({"TOKEN":"x"})),
            ("entry_path", json!("C:/secret.py")),
        ] {
            let mut q = p.clone();
            q[key] = value;
            assert_eq!(
                rpc(&mut ws, "prepare_run", q).unwrap_err(),
                "AGENT_EXPERIMENT_REVIEW_INVALID"
            );
        }
        let mut q = p;
        q["context"]["agent_run_id"] = json!("run_\nInjected title");
        assert_eq!(
            rpc(&mut ws, "prepare_run", q).unwrap_err(),
            "AGENT_EXPERIMENT_REVIEW_INVALID"
        );
    }
    #[test]
    fn cancelling_a_native_approved_request_never_revives_or_runs_it() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        approve(&mut ws, &r);
        let q = read(&ws, &r);
        let cancelled = rpc(&mut ws, "cancel_run", q).unwrap();
        assert_eq!(cancelled["record"]["state"], "cancelled");
        let retry = rpc(&mut ws, "prepare_run", p).unwrap();
        assert_eq!(retry["operation_id"], r["operation_id"]);
        assert_eq!(retry["record"]["state"], "cancelled");
    }
    #[test]
    fn context_survives_reopening_but_consent_requires_a_fresh_native_decision() {
        let (root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        let a = approve(&mut ws, &r);
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let q = read(&ws, &r);
        let actual = rpc(&mut ws, "run_record", q.clone()).unwrap();
        assert_eq!(actual["context"], r["context"]);
        assert_eq!(actual["record"]["state"], "awaiting_confirmation");
        assert!(actual["record"]["approval_id"].is_null());
        let reapproved = approve(&mut ws, &actual);
        assert_ne!(reapproved.record.approval_id, a.record.approval_id);
        let mut q = q;
        q["vault_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            rpc(&mut ws, "cancel_run", q).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
        let mut p = p;
        p["vault_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            rpc(&mut ws, "prepare_run", p).unwrap_err(),
            "VAULT_PERMISSION_CHANGED"
        );
    }
    #[test]
    fn binding_sql_failure_cannot_publish_a_new_review_or_retire_old_consent() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        let doc = ws.read("experiments/课程 #%.py").unwrap();
        ws.write(&doc.entry.path, &doc.entry.hash, b"# changed", "local")
            .unwrap();
        ws.db.execute_batch("CREATE TRIGGER refuse_agent_binding BEFORE UPDATE ON experiment_agent_reviews WHEN NEW.superseded_by IS NULL BEGIN SELECT RAISE(ABORT,'fault'); END;").unwrap();
        assert!(rpc(&mut ws, "prepare_run", p.clone()).is_err());
        let q = read(&ws, &r);
        let original = rpc(&mut ws, "run_record", q).unwrap();
        assert_eq!(original["record"]["state"], "awaiting_confirmation");
        assert!(ws
            .experiment_agent_approve_run_from_user(
                r["operation_id"].as_str().unwrap(),
                r["fingerprint"].as_str().unwrap()
            )
            .is_err());
        let orphan:String=ws.db.query_row("SELECT operation_id FROM experiment_agent_reviews WHERE superseded_by=operation_id",[],|r|r.get(0)).unwrap();
        assert!(ws.experiment_is_agent_operation(&orphan).unwrap());
        let record = ws.experiment_record(&orphan).unwrap().unwrap();
        assert!(ws
            .experiment_agent_approve_run_from_user(&orphan, &record.summary.fingerprint)
            .is_err());
        ws.db
            .execute_batch("DROP TRIGGER refuse_agent_binding;")
            .unwrap();
        // The native decision against changed source failed the original
        // request. A retry must report that failure, not silently mint a run.
        let failed = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        assert_eq!(failed["operation_id"], r["operation_id"]);
        assert_eq!(failed["record"]["state"], "failed");
        assert_eq!(failed["record"]["error"], "EXPERIMENT_INPUT_CHANGED");
        let mut new_call = p;
        new_call["context"]["tool_call_id"] = json!("call_after_failure");
        let newest = rpc(&mut ws, "prepare_run", new_call).unwrap();
        assert_ne!(newest["operation_id"], r["operation_id"]);
        assert_eq!(newest["record"]["state"], "awaiting_confirmation");
    }
    #[test]
    fn import_consent_is_independent_and_does_not_commit_any_file() {
        let (root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p).unwrap();
        let source = completed(&mut ws, &r);
        let p = import_draft(&ws, &source);
        let import = rpc(&mut ws, "prepare_import", p.clone()).unwrap();
        assert_eq!(import["record"]["state"], "awaiting_confirmation");
        assert!(!root.path().join("成果/report.md").exists());
        let op = import["operation_id"].as_str().unwrap();
        let fp = import["fingerprint"].as_str().unwrap();
        let approved = ws
            .experiment_agent_approve_import_from_user(op, fp)
            .unwrap();
        assert_eq!(approved.record.state, ImportState::Approved);
        assert!(!root.path().join("成果/report.md").exists());
        assert_eq!(
            rpc(&mut ws, "prepare_import", p).unwrap()["operation_id"],
            import["operation_id"]
        );
        let result = ws.experiment_import_next(op).unwrap();
        assert_eq!(result.state, ImportState::Completed);
        assert_eq!(
            std::fs::read(root.path().join("成果/report.md")).unwrap(),
            "# 中文成果\r\n".as_bytes()
        );
    }
    #[test]
    fn refreshed_import_target_revokes_the_old_native_dialog() {
        let (root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p).unwrap();
        let source = completed(&mut ws, &r);
        let p = import_draft(&ws, &source);
        let old = rpc(&mut ws, "prepare_import", p.clone()).unwrap();
        std::fs::create_dir(root.path().join("成果")).unwrap();
        std::fs::write(root.path().join("成果/report.md"), b"human").unwrap();
        let new = rpc(&mut ws, "prepare_import", p).unwrap();
        assert_ne!(old["operation_id"], new["operation_id"]);
        assert_eq!(
            ws.experiment_agent_approve_import_from_user(
                old["operation_id"].as_str().unwrap(),
                old["fingerprint"].as_str().unwrap()
            )
            .unwrap_err()
            .code,
            "AGENT_EXPERIMENT_REVIEW_CHANGED"
        );
        let op = new["operation_id"].as_str().unwrap();
        let fp = new["fingerprint"].as_str().unwrap();
        ws.experiment_agent_approve_import_from_user(op, fp)
            .unwrap();
        assert_eq!(
            std::fs::read(root.path().join("成果/report.md")).unwrap(),
            b"human"
        );
        std::fs::write(root.path().join("成果/report.md"), b"human after approval").unwrap();
        let conflict = ws.experiment_import_next(op).unwrap();
        assert_eq!(conflict.state, ImportState::Failed);
        assert_eq!(conflict.items[0].state, "failed");
        assert_eq!(
            conflict.items[0].error.as_deref(),
            Some("EXPERIMENT_IMPORT_TARGET_CHANGED")
        );
        assert_eq!(
            std::fs::read(root.path().join("成果/report.md")).unwrap(),
            b"human after approval"
        );
    }
    #[test]
    fn rejection_cannot_be_replaced_by_a_model_retry() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p.clone()).unwrap();
        ws.experiment_reject(
            r["operation_id"].as_str().unwrap(),
            r["fingerprint"].as_str().unwrap(),
        )
        .unwrap();
        let retry = rpc(&mut ws, "prepare_run", p).unwrap();
        assert_eq!(retry["operation_id"], r["operation_id"]);
        assert_eq!(retry["record"]["state"], "rejected");
        assert!(approve(&mut ws, &retry).record.approval_id.is_none());
    }
    #[test]
    fn malformed_import_arguments_do_not_consume_review_reservations() {
        let (_root, mut ws, _id) = setup();
        let base = import_draft(&ws, &uuid::Uuid::new_v4().to_string());
        for (field, value) in [
            ("source_run_id", json!("bad")),
            (
                "selections",
                json!([{"output_path":"../secret","destination":"results.md"}]),
            ),
            (
                "selections",
                json!([{"output_path":"report.md","destination":".ainote/record.md"}]),
            ),
        ] {
            let mut p = base.clone();
            p[field] = value;
            assert_eq!(
                rpc(&mut ws, "prepare_import", p).unwrap_err(),
                "EXPERIMENT_IMPORT_INVALID"
            );
        }
        let count: i64 = ws
            .db
            .query_row("SELECT COUNT(*) FROM experiment_agent_reviews", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0);
    }
    #[test]
    fn agent_log_prefixes_preserve_utf8_capture_counters_and_host_audit() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p).unwrap();
        assert!(r["record"]["result"].is_null());
        let raw = "中文🙂\r\n".repeat(4000);
        let op = completed_with_log(&mut ws, &r, raw.as_bytes());
        let stored = ws.experiment_record(&op).unwrap().unwrap();
        let query = read(&ws, &r);
        let actual = rpc(&mut ws, "run_record", query).unwrap();
        let log = &actual["record"]["result"]["logs"]["stdout"];
        let prefix = log["text"].as_str().unwrap();
        assert!(prefix.len() <= 16 * 1024);
        assert!(stored
            .result
            .as_ref()
            .unwrap()
            .logs
            .stdout
            .text
            .starts_with(prefix));
        assert_eq!(log["bytes_seen"], raw.len());
        assert_eq!(log["retained_bytes"], 32 * 1024);
        assert_eq!(log["displayed_bytes"], prefix.len());
        assert_eq!(log["display_truncated"], true);
        assert!(stored.result.as_ref().unwrap().logs.stdout.text.len() > prefix.len());
        assert_eq!(
            serde_json::to_value(ws.experiment_record(&op).unwrap().unwrap()).unwrap(),
            serde_json::to_value(stored).unwrap()
        );
    }
    fn execution_params(ws: &Workspace, review: &Value) -> Value {
        let mut params = read(ws, review);
        params["fingerprint"] = review["fingerprint"].clone();
        params
    }
    #[test]
    fn consumption_checks_exact_task_ticket_fingerprint_vault_and_fields() {
        let (_root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p).unwrap();
        approve(&mut ws, &r);
        let base = execution_params(&ws, &r);
        for field in ["agent_run_id", "tool_call_id", "request_id"] {
            let mut q = base.clone();
            q["context"][field] = json!("foreign");
            let p = serde_json::from_value(q).unwrap();
            assert_eq!(
                execution_review(&ws, &p, Workspace::experiment_agent_run_review)
                    .unwrap_err()
                    .code,
                "AGENT_EXPERIMENT_REVIEW_CHANGED"
            );
        }
        let mut q = base.clone();
        q["fingerprint"] = json!("0".repeat(64));
        assert!(execution_review(
            &ws,
            &serde_json::from_value(q).unwrap(),
            Workspace::experiment_agent_run_review
        )
        .is_err());
        let mut q = base.clone();
        q["vault_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            execution_review(
                &ws,
                &serde_json::from_value(q).unwrap(),
                Workspace::experiment_agent_run_review
            )
            .unwrap_err()
            .code,
            "VAULT_PERMISSION_CHANGED"
        );
        for (field, value) in [
            ("approved", json!(true)),
            ("command", json!("python")),
            ("runtime_root", json!("C:/private")),
        ] {
            let mut q = base.clone();
            q[field] = value;
            assert!(serde_json::from_value::<Execution>(q).is_err());
        }
        assert_eq!(
            execution_review(
                &ws,
                &serde_json::from_value(base).unwrap(),
                Workspace::experiment_agent_run_review
            )
            .unwrap()
            .record
            .state,
            RunState::Approved
        );
    }
    #[cfg(windows)]
    #[test]
    fn desktop_transport_requires_native_consent_and_import_retries_do_not_write_twice() {
        use crate::experiment_runner::Runner;
        use std::sync::{Arc, Mutex};
        let (root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p).unwrap();
        let unapproved =
            json!({"rpc":"workspace.experiment_agent.start_run","params":execution_params(&ws,&r)});
        let source = completed(&mut ws, &r);
        let p = import_draft(&ws, &source);
        let import = rpc(&mut ws, "prepare_import", p).unwrap();
        let request = json!({"rpc":"workspace.experiment_agent.import_next","params":execution_params(&ws,&import)});
        let op = import["operation_id"].as_str().unwrap();
        let fp = import["fingerprint"].as_str().unwrap();
        let runner = Runner::default();
        runner
            .initialize_cleanup(&root.path().join("host-data"))
            .unwrap();
        let workspace = Arc::new(Mutex::new(Some(ws)));
        // A completed run can be queried through start without being replayed.
        let existing = dispatch_execution(&runner, &workspace, root.path(), &unapproved).unwrap();
        assert_eq!(existing["record"]["state"], "completed");
        assert_eq!(
            dispatch_execution(&runner, &workspace, root.path(), &request).unwrap_err(),
            "EXPERIMENT_CONFIRMATION_REQUIRED"
        );
        assert!(!root.path().join("成果/report.md").exists());
        workspace
            .lock()
            .unwrap()
            .as_mut()
            .unwrap()
            .experiment_agent_approve_import_from_user(op, fp)
            .unwrap();
        let committed = dispatch_execution(&runner, &workspace, root.path(), &request).unwrap();
        assert_eq!(committed["record"]["state"], "completed");
        let first = std::fs::read(root.path().join("成果/report.md")).unwrap();
        assert_eq!(first, "# 中文成果\r\n".as_bytes());
        std::fs::write(root.path().join("成果/report.md"), b"later human edit").unwrap();
        assert_eq!(
            dispatch_execution(&runner, &workspace, root.path(), &request).unwrap(),
            committed
        );
        assert_eq!(
            std::fs::read(root.path().join("成果/report.md")).unwrap(),
            b"later human edit"
        );
        let mut bad = request;
        bad["params"]["context"]["request_id"] = json!("foreign_ticket");
        assert_eq!(
            dispatch_execution(&runner, &workspace, root.path(), &bad).unwrap_err(),
            "AGENT_EXPERIMENT_REVIEW_CHANGED"
        );
        runner.shutdown().unwrap();
    }
    #[cfg(windows)]
    #[test]
    fn desktop_start_claims_once_and_never_runs_an_unbundled_interpreter() {
        use crate::experiment_runner::Runner;
        use std::sync::{Arc, Mutex};
        let _lock = crate::experiment_owner::TEST_EXECUTION_LOCK.lock().unwrap();
        let (root, mut ws, id) = setup();
        let p = draft(&ws, &id);
        let r = rpc(&mut ws, "prepare_run", p).unwrap();
        let request =
            json!({"rpc":"workspace.experiment_agent.start_run","params":execution_params(&ws,&r)});
        let runner = Runner::default();
        runner
            .initialize_cleanup(&root.path().join("host-data"))
            .unwrap();
        let resources = root.path().join("missing-resources");
        let op = r["operation_id"].as_str().unwrap();
        let workspace = Arc::new(Mutex::new(Some(ws)));
        assert_eq!(
            dispatch_execution(&runner, &workspace, &resources, &request).unwrap_err(),
            "EXPERIMENT_CONFIRMATION_REQUIRED"
        );
        workspace
            .lock()
            .unwrap()
            .as_mut()
            .unwrap()
            .experiment_agent_approve_run_from_user(op, r["fingerprint"].as_str().unwrap())
            .unwrap();
        let first = dispatch_execution(&runner, &workspace, &resources, &request).unwrap();
        assert_ne!(first["record"]["state"], "approved");
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(6);
        let final_record = loop {
            let r = runner.record(&workspace, op).unwrap().unwrap();
            if matches!(
                r.state,
                RunState::Completed
                    | RunState::Failed
                    | RunState::Cancelled
                    | RunState::Limited
                    | RunState::Interrupted
                    | RunState::Rejected
            ) {
                break r;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "missing runtime must fail promptly"
            );
            std::thread::sleep(std::time::Duration::from_millis(10));
        };
        assert_eq!(final_record.state, RunState::Failed);
        assert!(final_record.result.as_ref().unwrap().error.is_some());
        let replay = dispatch_execution(&runner, &workspace, &resources, &request).unwrap();
        assert_eq!(replay["record"]["state"], "failed");
        assert_eq!(
            replay["record"]["approval_id"],
            json!(final_record.approval_id)
        );
        runner.shutdown().unwrap();
        assert!(runner.cleanup_status().unwrap().is_none());
        assert_eq!(
            std::fs::read(root.path().join("experiments/课程 #%.py")).unwrap(),
            "print('中文')\r\n".as_bytes()
        );
    }
}

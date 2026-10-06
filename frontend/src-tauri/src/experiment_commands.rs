//! Main-window experiment IPC. Core RPC has no route to these user decisions.
use super::Host;
use notesagent_host::{
    experiment_import::ImportRequest, experiment_input::RunRequest,
    experiment_store::HistoryCursor, workspace::Workspace,
};
use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{Manager, WebviewWindow};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    vault_id: String,
    action: Action,
}
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum Action {
    Status {},
    CleanupReview {},
    RecoverCleanup {
        fingerprint: String,
    },
    Prepare {
        request: RunRequest,
    },
    History {
        limit: usize,
        cursor: Option<HistoryCursor>,
    },
    Record {
        operation_id: String,
    },
    Live {
        operation_id: String,
    },
    ConfirmRun {
        operation_id: String,
        fingerprint: String,
    },
    ConfirmAgentRun {
        operation_id: String,
        fingerprint: String,
    },
    ConfirmAgentImport {
        operation_id: String,
        fingerprint: String,
    },
    StartApproved {
        operation_id: String,
    },
    RejectRun {
        operation_id: String,
        fingerprint: String,
    },
    CancelRun {
        operation_id: String,
    },
    SourcePreview {
        operation_id: String,
        path: String,
    },
    SourceRead {
        operation_id: String,
        path: String,
        offset: usize,
        limit: usize,
    },
    OutputPreview {
        operation_id: String,
        path: String,
    },
    OutputRead {
        operation_id: String,
        path: String,
        offset: usize,
        limit: usize,
    },
    ImportPrepare {
        request: ImportRequest,
    },
    ImportRecord {
        operation_id: String,
    },
    ImportHistory {
        run_id: String,
        limit: usize,
        cursor: Option<HistoryCursor>,
    },
    AllImportHistory {
        limit: usize,
        cursor: Option<HistoryCursor>,
    },
    ConfirmImport {
        operation_id: String,
        fingerprint: String,
    },
    ImportNext {
        operation_id: String,
    },
    CancelImport {
        operation_id: String,
        fingerprint: String,
    },
    Origins {
        file_id: String,
        limit: usize,
        cursor: Option<HistoryCursor>,
    },
    Usage {},
    FilePath {
        file_id: String,
    },
    ImportTargetPreview {
        operation_id: String,
        output_path: String,
    },
    ForgetRun {
        operation_id: String,
        fingerprint: String,
    },
    ForgetImport {
        operation_id: String,
        fingerprint: String,
    },
}
fn at_vault<T>(
    host: &Host,
    vault: &str,
    f: impl FnOnce(&mut Workspace) -> notesagent_host::workspace::Result<T>,
) -> Result<T, String> {
    let mut slot = host.workspace.lock().map_err(|_| "HOST_BUSY")?;
    let ws = slot.as_mut().ok_or("VAULT_NOT_OPEN")?;
    if ws.vault_id != vault {
        return Err("VAULT_PERMISSION_CHANGED".into());
    }
    f(ws).map_err(|e| e.code)
}
fn encoded(value: impl serde::Serialize) -> Result<Value, String> {
    serde_json::to_value(value).map_err(|_| "EXPERIMENT_RESPONSE_INVALID".into())
}
fn main_window(label: &str) -> Result<(), String> {
    if label == "main" {
        Ok(())
    } else {
        Err("EXPERIMENT_WINDOW_DENIED".into())
    }
}
#[cfg(windows)]
fn confirm(window: &WebviewWindow, title: &str, description: &str) -> bool {
    rfd::MessageDialog::new()
        .set_parent(window)
        .set_title(title)
        .set_description(description)
        .set_buttons(rfd::MessageButtons::YesNo)
        .show()
        == rfd::MessageDialogResult::Yes
}
#[cfg(windows)]
fn run_description(record: &notesagent_host::experiment_store::RunRecord) -> String {
    let r = &record.summary.request;
    let l = &r.limits;
    let mut text = format!("运行隔离实验 / Run isolated experiment\n{}\n\n环境 / Runtime: {}\n网络 / Network: 关闭 / Disabled\n墙钟 / Wall: {}s · CPU: {}s · RAM: {} MiB · 进程 / Processes: {}\n磁盘占用 / Disk usage: {} MiB (超限监测 / monitored)\n成果 / Outputs: {} MiB · 日志 / Logs: {} KiB\n\n源文件 / Source: {}\n修订 / Revision: {}\nSHA-256: {}\n\n只读输入 / Read-only inputs:\n", r.entry.path, r.runtime_id, l.wall_seconds, l.cpu_seconds, l.memory_mib, l.processes, l.disk_mib, l.output_mib, l.log_kib, r.entry.path, r.entry.revision, r.entry.hash);
    for input in &r.inputs {
        text.push_str(&format!(
            "{} · r{}\n{}\n",
            input.path, input.revision, input.hash
        ));
    }
    text.push_str("\n仅运行本次已保存源版本。成果导入需要另行确认。\nRuns this saved source version once. Importing outputs requires separate confirmation.");
    text
}
#[cfg(windows)]
fn import_description(record: &notesagent_host::experiment_import::ImportRecord) -> String {
    let mut text = "将选择的成果写入当前知识库。每个文件单独提交，多文件可能部分成功。\nImport selected outputs into this vault. Each file commits separately; a batch may partially succeed.\n\n".to_string();
    for item in &record.plan.items {
        text.push_str(&format!(
            "{} → {}\n{} B · {} · r{}\nSHA-256: {}\n\n",
            item.output.path,
            item.target.path,
            item.output.bytes,
            if item.target.hash.is_empty() {
                "新文件 / New file"
            } else {
                "覆盖 / Replace"
            },
            item.target.revision,
            item.output.sha256
        ));
    }
    text
}
#[cfg(any(windows, test))]
fn reject_agent_manual(ws: &Workspace, operation: &str) -> notesagent_host::workspace::Result<()> {
    if ws.experiment_is_agent_operation(operation)? {
        return Err(notesagent_host::workspace::HostError::new(
            "AGENT_EXPERIMENT_REQUIRES_REVIEW",
        ));
    }
    Ok(())
}
#[cfg(windows)]
fn dispatch(
    window: &WebviewWindow,
    host: &Host,
    resource_root: std::path::PathBuf,
    request: Request,
) -> Result<Value, String> {
    use notesagent_host::experiment_store::RunState;
    let vault = &request.vault_id;
    // The individual workspace operation also checks under the same lock. This
    // first check avoids showing a native dialog for an already replaced vault.
    at_vault(host, vault, |_| Ok(()))?;
    match request.action {
        Action::Status {} => {
            let runtime = host.experiment_runtime.get().and_then(|v| v.as_ref().ok());
            let cleanup = host.experiments.cleanup_status();
            let available = runtime.is_some() && matches!(cleanup, Ok(None));
            let error = match &cleanup {
                Err(e) => Some(e.code.as_str()),
                Ok(Some(_)) => Some("EXPERIMENT_CLEANUP_REQUIRED"),
                _ if runtime.is_none() => Some("EXPERIMENT_RUNTIME_UNAVAILABLE"),
                _ => None,
            };
            Ok(
                json!({"available":available,"runtime":runtime,"limits":notesagent_host::experiment_policy::ExecutionLimits::default(),"error":error,"cleanup":cleanup.as_ref().ok().and_then(|s|s.as_ref()).map(|s|json!({"operation_id":s.operation_id,"phase":s.phase,"error":s.error}))}),
            )
        }
        Action::Prepare { request } => {
            if request.vault_id != *vault {
                return Err("VAULT_PERMISSION_CHANGED".into());
            }
            encoded(
                host.experiments
                    .prepare(&host.workspace, &request)
                    .map_err(|e| e.code)?,
            )
        }
        Action::CleanupReview {} => encoded(host.experiments.cleanup_review().map_err(|e| e.code)?),
        Action::RecoverCleanup { fingerprint } => {
            let Some(review) = host.experiments.cleanup_review().map_err(|e| e.code)? else {
                return Ok(json!({"cleaned":true}));
            };
            if review.fingerprint != fingerprint {
                return Err("EXPERIMENT_CLEANUP_REVIEW_CHANGED".into());
            }
            let description = format!("上次实验 / Previous experiment: {}\n\n临时源文件副本与目录 / Temporary source-copy objects: {}\n借用的运行时对象权限 / Borrowed runtime permissions: {}\n\n将清理已核对的实验临时资源，包括临时容器内尚未导入的文件；撤销该实验对运行时的读取权限。\nThis removes verified temporary experiment resources, including files in the temporary container that were not imported, and revokes this experiment's runtime access.\n\n已保留的运行成果、已导入的文件、知识库源文件及运行时文件将保留。\nRetained outputs, imported files, vault sources and runtime files are preserved.", review.operation_id, review.temporary_objects, review.borrowed_objects);
            if !confirm(
                window,
                "清理实验临时资源 / Clean up experiment resources",
                &description,
            ) {
                return Err("EXPERIMENT_USER_CANCELLED".into());
            }
            // Hold the vault lock across the native recovery. A switch during
            // the dialog must not consume approval for the previous vault.
            at_vault(host, vault, |_| {
                host.experiments.recover_cleanup(&fingerprint)
            })?;
            Ok(json!({"cleaned":true}))
        }
        Action::History { limit, cursor } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_history(limit, cursor.as_ref())
        })?),
        Action::Record { operation_id } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_record(&operation_id)
        })?),
        Action::Live { operation_id } => encoded(
            host.experiments
                .live(vault, &operation_id)
                .map_err(|e| e.code)?,
        ),
        Action::ConfirmRun {
            operation_id,
            fingerprint,
        } => {
            at_vault(host, vault, |ws| reject_agent_manual(ws, &operation_id))?;
            let record = at_vault(host, vault, |ws| ws.experiment_record(&operation_id))?
                .ok_or("EXPERIMENT_OPERATION_NOT_FOUND")?;
            if record.summary.fingerprint != fingerprint {
                return Err("EXPERIMENT_OPERATION_MISMATCH".into());
            }
            if record.state != RunState::AwaitingConfirmation {
                return encoded(record);
            }
            if !confirm(window, "确认运行 / Confirm run", &run_description(&record)) {
                return encoded(at_vault(host, vault, |ws| {
                    ws.experiment_reject(&operation_id, &fingerprint)
                })?);
            }
            let approved = host
                .experiments
                .confirm_from_user_for_vault(&host.workspace, vault, &operation_id, &fingerprint)
                .map_err(|e| e.code)?;
            if approved.state != RunState::Approved {
                return encoded(approved);
            }
            encoded(
                host.experiments
                    .start_approved_for_vault(
                        host.workspace.clone(),
                        vault,
                        resource_root,
                        &operation_id,
                    )
                    .map_err(|e| e.code)?,
            )
        }
        Action::ConfirmAgentRun {
            operation_id,
            fingerprint,
        } => {
            let review = at_vault(host, vault, |ws| {
                ws.experiment_agent_run_review(&operation_id, &fingerprint)
            })?;
            if review.record.state != RunState::AwaitingConfirmation {
                return encoded(review);
            }
            let text=format!("Agent: {}\nTool call: {}\n\n{}\n本次仅记录批准；Agent 工具须重新核对当前权限后才可启动。\nThis records consent only; the Agent tool must recheck its current permissions before starting.",
                review.context.agent_run_id,review.context.tool_call_id,run_description(&review.record));
            if !confirm(window, "确认 Agent 运行 / Confirm Agent run", &text) {
                at_vault(host, vault, |ws| {
                    ws.experiment_reject(&operation_id, &fingerprint)
                })?;
                return encoded(at_vault(host, vault, |ws| {
                    ws.experiment_agent_run_review(&operation_id, &fingerprint)
                })?);
            }
            encoded(at_vault(host, vault, |ws| {
                ws.experiment_agent_approve_run_from_user(&operation_id, &fingerprint)
            })?)
        }
        Action::ConfirmAgentImport {
            operation_id,
            fingerprint,
        } => {
            let review = at_vault(host, vault, |ws| {
                ws.experiment_agent_import_review(&operation_id, &fingerprint)
            })?;
            if review.record.state
                != notesagent_host::experiment_import::ImportState::AwaitingConfirmation
            {
                return encoded(review);
            }
            let text=format!("Agent: {}\nTool call: {}\n\n{}\n本次仅记录批准；Agent 工具须重新核对导入权限后才可提交文件。\nThis records consent only; the Agent tool must recheck import permission before committing files.",
                review.context.agent_run_id,review.context.tool_call_id,import_description(&review.record));
            if !confirm(window, "确认 Agent 导入 / Confirm Agent import", &text) {
                at_vault(host, vault, |ws| {
                    ws.experiment_import_cancel(&operation_id, &fingerprint)
                })?;
                return encoded(at_vault(host, vault, |ws| {
                    ws.experiment_agent_import_review(&operation_id, &fingerprint)
                })?);
            }
            encoded(at_vault(host, vault, |ws| {
                ws.experiment_agent_approve_import_from_user(&operation_id, &fingerprint)
            })?)
        }
        Action::StartApproved { operation_id } => {
            at_vault(host, vault, |ws| reject_agent_manual(ws, &operation_id))?;
            encoded(
                host.experiments
                    .start_approved_for_vault(
                        host.workspace.clone(),
                        vault,
                        resource_root,
                        &operation_id,
                    )
                    .map_err(|e| e.code)?,
            )
        }
        Action::RejectRun {
            operation_id,
            fingerprint,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_reject(&operation_id, &fingerprint)
        })?),
        Action::CancelRun { operation_id } => encoded(at_vault(host, vault, |ws| {
            notesagent_host::experiment_owner::RunOwner::request_cancel(ws, &operation_id)
        })?),
        Action::SourcePreview { operation_id, path } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_source_preview(&operation_id, &path)
        })?),
        Action::SourceRead {
            operation_id,
            path,
            offset,
            limit,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_source_read(&operation_id, &path, offset, limit)
        })?),
        Action::OutputPreview { operation_id, path } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_output_preview(&operation_id, &path)
        })?),
        Action::OutputRead {
            operation_id,
            path,
            offset,
            limit,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_output_read(&operation_id, &path, offset, limit)
        })?),
        Action::ImportPrepare { request } => {
            if request.vault_id != *vault {
                return Err("VAULT_PERMISSION_CHANGED".into());
            }
            encoded(at_vault(host, vault, |ws| {
                ws.experiment_import_prepare(&request)
            })?)
        }
        Action::ImportRecord { operation_id } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_import_record(&operation_id)
        })?),
        Action::ImportHistory {
            run_id,
            limit,
            cursor,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_import_history(&run_id, limit, cursor.as_ref())
        })?),
        Action::AllImportHistory { limit, cursor } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_all_import_history(limit, cursor.as_ref())
        })?),
        Action::ConfirmImport {
            operation_id,
            fingerprint,
        } => {
            at_vault(host, vault, |ws| reject_agent_manual(ws, &operation_id))?;
            let record = at_vault(host, vault, |ws| ws.experiment_import_record(&operation_id))?
                .ok_or("EXPERIMENT_IMPORT_INVALID")?;
            if record.fingerprint != fingerprint {
                return Err("OPERATION_PAYLOAD_CONFLICT".into());
            }
            if record.state != notesagent_host::experiment_import::ImportState::AwaitingConfirmation
            {
                return encoded(record);
            }
            let text = import_description(&record);
            if !confirm(window, "确认导入 / Confirm import", &text) {
                return encoded(at_vault(host, vault, |ws| {
                    ws.experiment_import_cancel(&operation_id, &fingerprint)
                })?);
            }
            encoded(
                host.experiments
                    .confirm_import_from_user_for_vault(
                        &host.workspace,
                        vault,
                        &operation_id,
                        &fingerprint,
                    )
                    .map_err(|e| e.code)?,
            )
        }
        Action::ImportNext { operation_id } => {
            at_vault(host, vault, |ws| reject_agent_manual(ws, &operation_id))?;
            encoded(
                host.experiments
                    .import_next_for_vault(&host.workspace, vault, &operation_id)
                    .map_err(|e| e.code)?,
            )
        }
        Action::CancelImport {
            operation_id,
            fingerprint,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_import_cancel(&operation_id, &fingerprint)
        })?),
        Action::Origins {
            file_id,
            limit,
            cursor,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_artifact_origins(&file_id, limit, cursor.as_ref())
        })?),
        Action::Usage {} => encoded(at_vault(host, vault, |ws| ws.experiment_retention_usage())?),
        Action::FilePath { file_id } => encoded(at_vault(host, vault, |ws| {
            ws.scan()?;
            ws.path_for_id(&file_id)
        })?),
        Action::ImportTargetPreview {
            operation_id,
            output_path,
        } => encoded(at_vault(host, vault, |ws| {
            ws.experiment_import_target_preview(&operation_id, &output_path)
        })?),
        Action::ForgetRun {
            operation_id,
            fingerprint,
        } => {
            let record = at_vault(host, vault, |ws| ws.experiment_record(&operation_id))?
                .ok_or("EXPERIMENT_OPERATION_NOT_FOUND")?;
            if record.summary.fingerprint != fingerprint {
                return Err("EXPERIMENT_OPERATION_MISMATCH".into());
            }
            if !confirm(window, "清理运行记录 / Clear run record", "删除本次保留的源版本、日志和临时成果。已导入的知识库文件会保留。\nRemove this run's retained source snapshots, logs and temporary outputs. Imported vault files are kept.") { return Err("EXPERIMENT_USER_CANCELLED".into()); }
            encoded(
                host.experiments
                    .forget_from_user_for_vault(&host.workspace, vault, &operation_id, &fingerprint)
                    .map_err(|e| e.code)?,
            )
        }
        Action::ForgetImport {
            operation_id,
            fingerprint,
        } => {
            if !confirm(window, "清理来源记录 / Clear import provenance", "删除本次导入的来源关联。知识库文件会保留。\nRemove this import's provenance. Vault files are kept.") { return Err("EXPERIMENT_USER_CANCELLED".into()); }
            at_vault(host, vault, |ws| {
                ws.experiment_import_forget(&operation_id, &fingerprint)
            })?;
            Ok(Value::Null)
        }
    }
}

#[tauri::command]
pub async fn experiment_request(
    window: WebviewWindow,
    app: tauri::AppHandle,
    request: Request,
) -> Result<Value, String> {
    main_window(window.label())?;
    let resource_root = app
        .path()
        .resource_dir()
        .map_err(|_| "EXPERIMENT_RUNTIME_UNAVAILABLE")?;
    tauri::async_runtime::spawn_blocking(move || {
        #[cfg(windows)]
        {
            dispatch(&window, &app.state::<Host>(), resource_root, request)
        }
        #[cfg(not(windows))]
        {
            let _ = (window, app, request, resource_root);
            Err("EXPERIMENT_PLATFORM_UNAVAILABLE".into())
        }
    })
    .await
    .map_err(|_| "HOST_BUSY".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn manual_actions_reject_agent_proposals_before_and_after_consent() {
        use notesagent_host::{
            experiment_input::PreparedInputs, experiment_policy::ExecutionLimits, workspace_broker,
        };
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(root.path().join("experiments/main.py"), b"print(1)").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let file = ws.read("experiments/main.py").unwrap().entry;
        let context = json!({"agent_run_id":"run_test","tool_call_id":"call_test","request_id":"request_test"});
        let prepare = json!({
            "rpc":"workspace.experiment_agent.prepare_run", "params":{
                "vault_id":ws.vault_id, "context":context,
                "entry_file_id":file.file_id,"input_file_ids":[],"limits":ExecutionLimits::default()
            }
        });
        let review = workspace_broker::dispatch(&mut ws, &prepare).unwrap();
        let operation = review["operation_id"].as_str().unwrap();
        let fingerprint = review["fingerprint"].as_str().unwrap();
        assert_eq!(
            reject_agent_manual(&ws, operation).unwrap_err().code,
            "AGENT_EXPERIMENT_REQUIRES_REVIEW"
        );
        ws.experiment_agent_approve_run_from_user(operation, fingerprint)
            .unwrap();
        assert!(reject_agent_manual(&ws, operation).is_err());
        let cancel = json!({"rpc":"workspace.experiment_agent.cancel_run", "params":{
            "vault_id":ws.vault_id,"context":context,"operation_id":operation
        }});
        workspace_broker::dispatch(&mut ws, &cancel).unwrap();
        assert!(reject_agent_manual(&ws, operation).is_err());

        // A genuine separately prepared manual run still uses its manual UI.
        let mut manual: RunRequest =
            serde_json::from_value(review["record"]["summary"]["request"].clone()).unwrap();
        manual.operation_id = uuid::Uuid::new_v4().to_string();
        let inputs = PreparedInputs::prepare(&mut ws, &manual.validate().unwrap()).unwrap();
        ws.experiment_prepare(inputs).unwrap();
        assert!(reject_agent_manual(&ws, &manual.operation_id).is_ok());
    }
    #[test]
    fn model_payload_cannot_supply_commands_paths_or_user_approval() {
        for action in [
            json!({"kind":"status","command":"python"}),
            json!({"kind":"confirm_run","operation_id":"x","fingerprint":"y","approved":true}),
            json!({"kind":"shell","command":"calc"}),
            json!({"kind":"output_preview","operation_id":"x","path":"a.md","html":true}),
            json!({"kind":"recover_cleanup","fingerprint":"f","profile_name":"foreign"}),
            json!({"kind":"cleanup_review","root":"C:/user-data"}),
            json!({"kind":"confirm_agent_run","operation_id":"x","fingerprint":"y","approved":true}),
            json!({"kind":"confirm_agent_import","operation_id":"x","fingerprint":"y","context":{"agent_run_id":"foreign"}}),
        ] {
            assert!(
                serde_json::from_value::<Request>(json!({"vault_id":"v","action":action})).is_err()
            );
        }
        assert!(serde_json::from_value::<Request>(
            json!({"vault_id":"v","action":{"kind":"status"}})
        )
        .is_ok());
        assert!(main_window("plugin").is_err());
        assert!(main_window("main").is_ok());
    }
}

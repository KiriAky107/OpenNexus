//! Narrow foreground cycle/review actions. Credentials and HTTP stay native.
use crate::{
    mobile_sync_auth::{Auth, SecretStore},
    mobile_sync_foreground::{Lease, ScopedWorkspace},
    mobile_sync_session::{Scope, Selection},
    mobile_workspace::MobileWorkspace,
    sync_client::{SyncError, WorkspaceAccess},
    sync_inbox::RemoteRevision,
    sync_progress::Event,
    sync_review::ConflictReview,
    sync_state::Binding,
    workspace::Workspace,
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};

type Result<T> = std::result::Result<T, SyncError>;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub expected: Selection,
    pub vault_id: String,
    pub binding_id: String,
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Status {
        target: Target,
        after: i64,
        limit: usize,
    },
    Now {
        target: Target,
    },
    Review {
        target: Target,
        sequence: i64,
    },
    Decision {
        target: Target,
        sequence: i64,
    },
    Resolve {
        target: Target,
        sequence: i64,
        choice: String,
        destination: String,
        expected_hash: String,
        fingerprint: String,
    },
}
impl Request {
    fn target(&self) -> &Target {
        match self {
            Self::Status { target, .. }
            | Self::Now { target }
            | Self::Review { target, .. }
            | Self::Decision { target, .. }
            | Self::Resolve { target, .. } => target,
        }
    }
    fn validate(&self) -> Result<()> {
        let target = self.target();
        for id in [&target.vault_id, &target.binding_id] {
            if !uuid::Uuid::parse_str(id).is_ok_and(|value| value.to_string() == *id) {
                return Err(SyncError::new("SYNC_TARGET_INVALID"));
            }
        }
        match self {
            Self::Status { after, limit, .. } if *after < 0 || !(1..=20).contains(limit) => {
                Err(SyncError::new("SYNC_PAGE_INVALID"))
            }
            Self::Review { sequence, .. }
            | Self::Decision { sequence, .. }
            | Self::Resolve { sequence, .. }
                if *sequence <= 0 =>
            {
                Err(SyncError::new("SYNC_RESPONSE_INVALID"))
            }
            Self::Resolve {
                choice,
                destination,
                expected_hash,
                fingerprint,
                ..
            } if !matches!(choice.as_str(), "local" | "remote" | "copy")
                || destination.len() > 1024
                || !expected_hash.is_empty() && !digest(expected_hash)
                || !digest(fingerprint) =>
            {
                Err(SyncError::new("SYNC_RESOLUTION_INVALID"))
            }
            _ => Ok(()),
        }
    }
}
fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

#[derive(Serialize)]
pub struct Conflict {
    pub sequence: i64,
    pub file_id: String,
    pub local_path: String,
    /// Recorded at conflict creation. The current bytes require a new review.
    pub recorded_local_hash: String,
    pub remote: RemoteRevision,
}
#[derive(Serialize)]
pub struct Status {
    pub vault_id: String,
    pub binding: Binding,
    pub paused: bool,
    pub incoming_boundary: Option<i64>,
    pub pending_outbox: i64,
    pub queued_jobs: i64,
    pub open_conflicts: i64,
    pub pending_decisions: i64,
    pub attempts: Vec<serde_json::Value>,
    pub conflicts: Vec<Conflict>,
    pub next_after: Option<i64>,
    pub last_cycle_success: Option<i64>,
}
#[derive(Serialize)]
pub struct Decision {
    pub sequence: i64,
    pub choice: String,
    pub destination: String,
    pub expected_hash: String,
    pub fingerprint: Option<String>,
    pub operation_id: String,
    pub rename_id: String,
    pub copy_id: String,
    pub retire_id: String,
    pub restore_id: String,
    pub state: String,
}
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Response {
    Status { status: Status },
    Review { review: ConflictReview },
    Decision { decision: Option<Decision> },
}

fn bound(ws: &Workspace, target: &Target) -> crate::workspace::Result<Binding> {
    if ws.vault_id != target.vault_id {
        return Err(crate::workspace::HostError::new("VAULT_CHANGED"));
    }
    let binding = ws
        .sync_binding()?
        .ok_or_else(|| crate::workspace::HostError::new("SYNC_NOT_BOUND"))?;
    if binding.id != target.binding_id
        || binding.endpoint != target.expected.endpoint
        || binding.account != target.expected.account
    {
        return Err(crate::workspace::HostError::new("SYNC_BINDING_CHANGED"));
    }
    Ok(binding)
}
fn decision(
    ws: &Workspace,
    target: &Target,
    sequence: i64,
) -> crate::workspace::Result<Option<Decision>> {
    bound(ws, target)?;
    Ok(ws.db.query_row(
        "SELECT r.choice,r.destination,r.expected,v.fingerprint,r.operation_id,r.rename_id,r.copy_id,r.retire_id,r.restore_id,r.state FROM sync_resolutions r LEFT JOIN sync_review_receipts v ON v.binding=r.binding AND v.sequence=r.sequence WHERE r.binding=?1 AND r.sequence=?2",
        params![target.binding_id, sequence], |row| Ok(Decision {
            sequence, choice: row.get(0)?, destination: row.get(1)?, expected_hash: row.get(2)?,
            fingerprint: row.get(3)?, operation_id: row.get(4)?, rename_id: row.get(5)?,
            copy_id: row.get(6)?, retire_id: row.get(7)?, restore_id: row.get(8)?, state: row.get(9)?,
        }),
    ).optional()?)
}
fn status(
    ws: &Workspace,
    target: &Target,
    after: i64,
    limit: usize,
) -> crate::workspace::Result<Status> {
    let binding = bound(ws, target)?;
    let mut statement = ws.db.prepare("SELECT sequence,file_id,local_path,local_hash,remote FROM sync_conflicts WHERE binding=?1 AND state='open' AND sequence>?2 ORDER BY sequence LIMIT ?3")?;
    let rows = statement
        .query_map(params![target.binding_id, after, limit as i64 + 1], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let mut conflicts = Vec::new();
    for (sequence, file_id, local_path, recorded_local_hash, encoded) in &rows {
        if conflicts.len() == limit {
            break;
        }
        let remote: RemoteRevision = serde_json::from_str(encoded)
            .map_err(|_| crate::workspace::HostError::new("SYNC_RESPONSE_INVALID"))?;
        remote.validate(&binding)?;
        if remote.sequence != *sequence || remote.file_id != *file_id {
            return Err(crate::workspace::HostError::new("SYNC_RESPONSE_INVALID"));
        }
        conflicts.push(Conflict {
            sequence: *sequence,
            file_id: file_id.clone(),
            local_path: local_path.clone(),
            recorded_local_hash: recorded_local_hash.clone(),
            remote,
        });
    }
    let next_after = if rows.len() > limit {
        conflicts.last().map(|row| row.sequence)
    } else {
        None
    };
    Ok(Status {
        vault_id: ws.vault_id.clone(),
        paused: ws.sync_paused(&target.binding_id)?,
        incoming_boundary: ws.sync_boundary(&target.binding_id)?,
        pending_outbox: ws.db.query_row(
            "SELECT count(*) FROM outbox WHERE state='pending'",
            [],
            |row| row.get(0),
        )?,
        queued_jobs: ws.db.query_row(
            "SELECT count(*) FROM sync_jobs WHERE binding=?1 AND state NOT IN ('acked','archived')",
            [&target.binding_id],
            |row| row.get(0),
        )?,
        open_conflicts: ws.db.query_row(
            "SELECT count(*) FROM sync_conflicts WHERE binding=?1 AND state='open'",
            [&target.binding_id],
            |row| row.get(0),
        )?,
        pending_decisions: ws.db.query_row(
            "SELECT count(*) FROM sync_resolutions WHERE binding=?1 AND state='pending'",
            [&target.binding_id],
            |row| row.get(0),
        )?,
        attempts: ws.sync_attempts(&target.binding_id)?,
        conflicts,
        next_after,
        last_cycle_success: ws.sync_last_cycle_success(&target.binding_id)?,
        binding,
    })
}
async fn current_status(
    port: &ScopedWorkspace,
    target: Target,
    after: i64,
    limit: usize,
) -> Result<Status> {
    let binding = target.binding_id.clone();
    port.work(&binding, move |ws, lease, _| {
        let value = status(ws, &target, after, limit)?;
        lease.check()?;
        Ok(value)
    })
    .await
}

/// The Android wrapper owns the existing main-window Auth gate and Activity
/// lease. Now is a bounded cycle over the durable original outbox, not a new
/// file operation or a claim that the queue is empty. An unknown outcome is
/// inspected with Status/Decision before an explicit continuation.
pub async fn execute<S: SecretStore>(
    auth: &mut Auth<S>,
    lease: &Lease,
    engine: Arc<Mutex<Option<MobileWorkspace>>>,
    request: Request,
    now: u64,
    report: Arc<dyn Fn(Event) + Send + Sync>,
) -> Result<Response> {
    request.validate()?;
    lease.check()?;
    let target = request.target().clone();
    if auth.selection(lease)?.as_ref() != Some(&target.expected) {
        return Err(SyncError::new("SYNC_AUTH_SELECTION_CHANGED"));
    }
    let scope = Scope::new(&target.expected.endpoint, &target.expected.account)?;
    let operation = target.expected.operation_id.clone();
    auth.authorized_device(
        lease,
        &scope,
        &operation,
        now,
        move |client, device| async move {
            let port = device.workspace(engine);
            match request {
                Request::Status { after, limit, .. } => Ok(Response::Status {
                    status: current_status(&port, target, after, limit).await?,
                }),
                Request::Now { .. } => {
                    let original = target.clone();
                    let binding = port
                        .work(&target.binding_id, move |ws, _, _| {
                            let binding = bound(ws, &original)?;
                            if ws.sync_paused(&binding.id)? {
                                return Err(SyncError::new("SYNC_PAUSED"));
                            }
                            Ok(binding)
                        })
                        .await?;
                    crate::sync_cycle::run(client, &port, &binding, lease.progress(report)).await?;
                    let completed_at = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map_err(|_| SyncError::new("SYNC_TIME_INVALID"))?
                        .as_secs();
                    let completed_at = i64::try_from(completed_at)
                        .map_err(|_| SyncError::new("SYNC_TIME_INVALID"))?;
                    port.access(|ws| {
                        bound(ws, &target)?;
                        ws.sync_cycle_succeeded(&target.binding_id, completed_at)
                    })?;
                    Ok(Response::Status {
                        status: current_status(&port, target, 0, 20).await?,
                    })
                }
                Request::Review { sequence, .. } => {
                    let binding = target.binding_id.clone();
                    let review = port
                        .work(&binding, move |ws, lease, binding| {
                            bound(ws, &target)?;
                            ws.sync_conflict_review_checked(binding, sequence, lease)
                                .map_err(Into::into)
                        })
                        .await?;
                    Ok(Response::Review { review })
                }
                Request::Decision { sequence, .. } => {
                    let binding = target.binding_id.clone();
                    let original = port
                        .work(&binding, move |ws, lease, _| {
                            let value = decision(ws, &target, sequence)?;
                            lease.check()?;
                            Ok(value)
                        })
                        .await?;
                    Ok(Response::Decision { decision: original })
                }
                Request::Resolve {
                    sequence,
                    choice,
                    destination,
                    expected_hash,
                    fingerprint,
                    ..
                } => {
                    let binding = target.binding_id.clone();
                    let original = port
                        .work(&binding, move |ws, lease, binding| {
                            bound(ws, &target)?;
                            ws.sync_resolve_reviewed_checked(
                                binding,
                                sequence,
                                (&choice, &destination, &expected_hash, &fingerprint),
                                lease,
                            )?;
                            decision(ws, &target, sequence).map_err(Into::into)
                        })
                        .await?;
                    Ok(Response::Decision { decision: original })
                }
            }
        },
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::hash;

    fn target(ws: &Workspace, binding: &Binding) -> Target {
        Target {
            expected: Selection {
                revision: 1,
                endpoint: binding.endpoint.clone(),
                account: binding.account.clone(),
                operation_id: uuid::Uuid::new_v4().to_string(),
                generation: 1,
            },
            vault_id: ws.vault_id.clone(),
            binding_id: binding.id.clone(),
        }
    }
    fn receive(ws: &mut Workspace, binding: &Binding, revision: &RemoteRevision) {
        ws.sync_set_boundary(&binding.id, revision.sequence)
            .unwrap();
        ws.sync_stage(&binding.id, revision).unwrap();
        ws.sync_apply_pending(&binding.id).unwrap();
    }
    #[test]
    fn native_sync_cycle_contract_rejects_transport_hooks_ambiguous_targets_and_unbounded_pages() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example/", "owned", "reader")
            .unwrap();
        let frozen = serde_json::to_value(target(&ws, &binding)).unwrap();
        for field in ["url", "method", "token", "headers"] {
            let mut request = serde_json::json!({"action":"now","target":frozen});
            request[field] = serde_json::json!("owned-test-only");
            assert!(serde_json::from_value::<Request>(request).is_err());
        }
        for (after, limit) in [(-1, 20), (0, 0), (0, 21)] {
            assert!(Request::Status {
                target: target(&ws, &binding),
                after,
                limit
            }
            .validate()
            .is_err());
        }
        let mut invalid = target(&ws, &binding);
        invalid.vault_id = "another-path/or-account".into();
        assert!(Request::Now { target: invalid }.validate().is_err());
        let mut invalid = target(&ws, &binding);
        invalid.binding_id = "old-binding".into();
        assert!(Request::Now { target: invalid }.validate().is_err());
        assert!(Request::Review {
            target: target(&ws, &binding),
            sequence: 0
        }
        .validate()
        .is_err());
        assert!(Request::Resolve {
            target: target(&ws, &binding),
            sequence: 1,
            choice: "remote".into(),
            destination: String::new(),
            expected_hash: "not-original-digest".into(),
            fingerprint: hash(b"review")
        }
        .validate()
        .is_err());
    }
    #[test]
    fn native_sync_cycle_status_is_readonly_bounded_metadata_and_never_reads_current_paths() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example/", "owned", "reader")
            .unwrap();
        for index in 0..21 {
            let mut remote = RemoteRevision {
                vault_id: "owned".into(),
                sequence: index * 2 + 1,
                base_revision: 0,
                file_id: uuid::Uuid::new_v4().to_string(),
                path: format!("note-{index}.md"),
                operation: "put".into(),
                hash: Some(ws.sync_store_bytes(b"base").unwrap()),
                size: 4,
                operation_id: uuid::Uuid::new_v4().to_string(),
            };
            receive(&mut ws, &binding, &remote);
            ws.write(
                &remote.path,
                remote.hash.as_ref().unwrap(),
                b"local",
                "local",
            )
            .unwrap();
            remote.base_revision = remote.sequence;
            remote.sequence += 1;
            remote.operation_id = uuid::Uuid::new_v4().to_string();
            remote.hash = Some(ws.sync_store_bytes(b"incoming").unwrap());
            remote.size = 8;
            receive(&mut ws, &binding, &remote);
        }
        // A path resolver or old full-conflict hashing API would reject this
        // owned outside link. The status page only reads recorded SQL metadata.
        #[cfg(unix)]
        let outside = {
            let owned = tempfile::tempdir().unwrap();
            std::fs::write(owned.path().join("sentinel"), b"untouched outside original").unwrap();
            std::fs::remove_file(root.path().join("note-0.md")).unwrap();
            std::os::unix::fs::symlink(
                owned.path().join("sentinel"),
                root.path().join("note-0.md"),
            )
            .unwrap();
            owned
        };
        let original = target(&ws, &binding);
        let before = ws.db.total_changes();
        let first = status(&ws, &original, 0, 20).unwrap();
        assert_eq!(first.open_conflicts, 21);
        assert_eq!(first.conflicts.len(), 20);
        assert_eq!(first.next_after, Some(40));
        assert_eq!(first.conflicts[0].recorded_local_hash, hash(b"local"));
        let last = status(&ws, &original, 40, 20).unwrap();
        assert_eq!(last.conflicts.len(), 1);
        assert_eq!(last.conflicts[0].sequence, 42);
        assert!(last.next_after.is_none());
        assert_eq!(ws.db.total_changes(), before);
        #[cfg(unix)]
        assert_eq!(
            std::fs::read(outside.path().join("sentinel")).unwrap(),
            b"untouched outside original"
        );
        let mut stale = original.clone();
        stale.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            status(&ws, &stale, 0, 20).err().unwrap().code,
            "VAULT_CHANGED"
        );
        let mut stale = original.clone();
        stale.expected.account = "another reader".into();
        assert_eq!(
            status(&ws, &stale, 0, 20).err().unwrap().code,
            "SYNC_BINDING_CHANGED"
        );
        assert_eq!(ws.db.total_changes(), before);
    }
    #[test]
    fn native_sync_cycle_decision_query_retains_five_original_ids_without_applying_or_replacing() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example/", "owned", "reader")
            .unwrap();
        let original = target(&ws, &binding);
        let ids = (0..5)
            .map(|_| uuid::Uuid::new_v4().to_string())
            .collect::<Vec<_>>();
        ws.db.execute("INSERT INTO sync_resolutions VALUES (?1,2,'copy','copy.md',?2,?3,?4,?5,'pending',?6,?7)",
            params![binding.id,hash(b"local"),ids[0],ids[1],ids[2],ids[3],ids[4]]).unwrap();
        ws.db
            .execute(
                "INSERT INTO sync_review_receipts VALUES (?1,2,?2,?3,'copy','copy.md','[]')",
                params![binding.id, hash(b"review"), hash(b"local")],
            )
            .unwrap();
        let before = ws.db.total_changes();
        let value = decision(&ws, &original, 2).unwrap().unwrap();
        assert_eq!(value.choice, "copy");
        assert_eq!(value.state, "pending");
        assert_eq!(value.fingerprint, Some(hash(b"review")));
        assert_eq!(
            vec![
                value.operation_id,
                value.rename_id,
                value.copy_id,
                value.retire_id,
                value.restore_id
            ],
            ids
        );
        assert!(decision(&ws, &original, 3).unwrap().is_none());
        assert_eq!(ws.db.total_changes(), before);
        assert!(!root.path().join("copy.md").exists());
        assert_eq!(
            ws.db
                .query_row("SELECT count(*) FROM operations", [], |row| row
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
    }
}

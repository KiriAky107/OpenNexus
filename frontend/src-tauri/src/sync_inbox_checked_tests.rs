use super::*;
use crate::workspace_commit::{CommitGuard, UnscopedCommit};
use std::{cell::Cell, fs};

struct StopAfter {
    commits: Cell<usize>,
    after: usize,
}
impl CommitGuard for StopAfter {
    fn check(&self) -> Result<()> {
        if self.commits.get() >= self.after {
            Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
        } else {
            Ok(())
        }
    }
    fn commit(&self, transaction: rusqlite::Transaction<'_>) -> Result<()> {
        self.check()?;
        transaction.commit()?;
        self.commits.set(self.commits.get() + 1);
        Ok(())
    }
}
struct Reject;
impl CommitGuard for Reject {
    fn check(&self) -> Result<()> {
        Ok(())
    }
    fn commit(&self, _transaction: rusqlite::Transaction<'_>) -> Result<()> {
        Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
    }
}
fn setup() -> (tempfile::TempDir, Workspace, Binding) {
    let root = tempfile::tempdir().unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let binding = ws
        .sync_bind_download("https://sync.example/", "remote-vault", "account")
        .unwrap();
    (root, ws, binding)
}
fn revision(file_id: &str, path: &str, bytes: &[u8], sequence: i64) -> RemoteRevision {
    RemoteRevision {
        vault_id: "remote-vault".into(),
        sequence,
        file_id: file_id.into(),
        base_revision: sequence - 1,
        path: path.into(),
        operation: "put".into(),
        hash: Some(hash(bytes)),
        size: bytes.len() as i64,
        operation_id: Uuid::new_v4().to_string(),
    }
}
fn stage(ws: &mut Workspace, binding: &Binding, revision: &RemoteRevision, bytes: &[u8]) {
    if revision.operation == "put" {
        assert_eq!(
            ws.sync_store_bytes(bytes).unwrap(),
            revision.hash.as_ref().unwrap().as_str()
        );
    }
    ws.sync_set_boundary(&binding.id, revision.sequence)
        .unwrap();
    ws.sync_stage(&binding.id, revision).unwrap();
}
fn ids(ws: &Workspace, binding: &Binding, sequence: i64) -> (String, String) {
    ws.db
        .query_row(
            "SELECT operation_id,rename_id FROM sync_inbox WHERE binding=?1 AND sequence=?2",
            params![binding.id, sequence],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap()
}
fn rows(ws: &Workspace, table: &str) -> i64 {
    ws.db
        .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
            row.get(0)
        })
        .unwrap()
}

#[test]
fn sync_inbox_refused_stage_publishes_no_ids_and_preserves_original_window() {
    let (_root, ws, binding) = setup();
    let bytes = b"remote original\r\n";
    let rev = revision(&Uuid::new_v4().to_string(), "original.md", bytes, 1);
    ws.sync_store_bytes(bytes).unwrap();
    ws.sync_set_boundary(&binding.id, 1).unwrap();
    assert_eq!(
        ws.sync_stage_checked(&binding.id, &rev, &Reject)
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    assert_eq!(rows(&ws, "sync_inbox"), 0);
    assert_eq!(rows(&ws, "operations"), 0);
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
    assert_eq!(ws.sync_boundary(&binding.id).unwrap(), Some(1));
    assert_eq!(
        fs::read(ws.sync_spool(rev.hash.as_ref().unwrap()).unwrap()).unwrap(),
        bytes
    );
}

#[test]
fn sync_inbox_canceled_stage_verification_stops_between_original_payload_chunks() {
    struct Cancel(Cell<usize>);
    impl CommitGuard for Cancel {
        fn check(&self) -> Result<()> {
            self.0.set(self.0.get() + 1);
            if self.0.get() == 4 {
                Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
            } else {
                Ok(())
            }
        }
        fn commit(&self, _transaction: rusqlite::Transaction<'_>) -> Result<()> {
            panic!("Canceled verification reached SQL acceptance")
        }
    }
    let (_root, ws, binding) = setup();
    let bytes = vec![7; 256 * 1024];
    let rev = revision(&Uuid::new_v4().to_string(), "original.md", &bytes, 1);
    ws.sync_store_bytes(&bytes).unwrap();
    ws.sync_set_boundary(&binding.id, 1).unwrap();
    let before = ws.db.total_changes();
    let guard = Cancel(Cell::new(0));
    assert_eq!(
        ws.sync_stage_checked(&binding.id, &rev, &guard)
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    assert_eq!(guard.0.get(), 4);
    assert_eq!(ws.db.total_changes(), before);
    assert_eq!(rows(&ws, "sync_inbox"), 0);
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
    assert_eq!(ws.sync_boundary(&binding.id).unwrap(), Some(1));
}

#[test]
fn sync_inbox_accepted_write_finishes_original_bytes_but_cursor_waits_for_new_foreground() {
    let (root, mut ws, binding) = setup();
    let bytes = b"remote original\r\n";
    let rev = revision(&Uuid::new_v4().to_string(), "original.md", bytes, 1);
    stage(&mut ws, &binding, &rev, bytes);
    let original = ids(&ws, &binding, 1);
    let guard = StopAfter {
        commits: Cell::new(0),
        after: 1,
    };
    assert_eq!(
        ws.sync_apply_pending_checked(&binding.id, &guard)
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    assert_eq!(
        ws.operation(&original.0).unwrap().unwrap()["state"],
        "committed"
    );
    let entry = ws.entry("original.md").unwrap().unwrap();
    assert_eq!(entry.file_id, rev.file_id);
    assert_eq!(entry.revision, 1);
    assert_eq!(fs::read(root.path().join("original.md")).unwrap(), bytes);
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
    assert_eq!(rows(&ws, "sync_heads"), 0);
    assert_eq!(rows(&ws, "outbox"), 0);
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
    assert!(ws
        .sync_apply_pending_checked(&binding.id, &UnscopedCommit)
        .unwrap());
    assert_eq!(ids(&ws, &binding, 1), original);
    assert_eq!(ws.entry("original.md").unwrap(), Some(entry));
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 1);
    assert_eq!(rows(&ws, "operations"), 1);
    assert!(!ws.sync_apply_pending(&binding.id).unwrap());
}

#[test]
fn sync_inbox_accepted_rename_resumes_original_write_without_replacing_either_id() {
    let (root, mut ws, binding) = setup();
    let file_id = Uuid::new_v4().to_string();
    let first = revision(&file_id, "original.md", b"before", 1);
    stage(&mut ws, &binding, &first, b"before");
    ws.sync_apply_pending(&binding.id).unwrap();
    let next = revision(&file_id, "renamed.md", b"remote after", 2);
    stage(&mut ws, &binding, &next, b"remote after");
    let original = ids(&ws, &binding, 2);
    let guard = StopAfter {
        commits: Cell::new(0),
        after: 1,
    };
    assert_eq!(
        ws.sync_apply_pending_checked(&binding.id, &guard)
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    assert_eq!(
        ws.operation(&original.1).unwrap().unwrap()["state"],
        "committed"
    );
    assert!(ws.operation(&original.0).unwrap().is_none());
    assert!(!root.path().join("original.md").exists());
    assert_eq!(fs::read(root.path().join("renamed.md")).unwrap(), b"before");
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 1);
    assert_eq!(ws.entry("renamed.md").unwrap().unwrap().file_id, file_id);
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.sync_apply_pending_checked(&binding.id, &UnscopedCommit)
        .unwrap();
    assert_eq!(ids(&ws, &binding, 2), original);
    assert_eq!(
        fs::read(root.path().join("renamed.md")).unwrap(),
        b"remote after"
    );
    let entry = ws.entry("renamed.md").unwrap().unwrap();
    assert_eq!(entry.file_id, file_id);
    assert_eq!(entry.revision, 3);
    assert_eq!(
        ws.operation(&original.0).unwrap().unwrap()["state"],
        "committed"
    );
    assert_eq!(rows(&ws, "operations"), 3);
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 2);
    assert_eq!(rows(&ws, "outbox"), 0);
}

#[test]
fn sync_inbox_accepted_delete_keeps_original_trash_and_does_not_advance_while_stopped() {
    let (root, mut ws, binding) = setup();
    let file_id = Uuid::new_v4().to_string();
    let first = revision(&file_id, "original.md", b"before", 1);
    stage(&mut ws, &binding, &first, b"before");
    ws.sync_apply_pending(&binding.id).unwrap();
    let next = RemoteRevision {
        vault_id: binding.remote_vault.clone(),
        sequence: 2,
        file_id: file_id.clone(),
        base_revision: 1,
        path: "original.md".into(),
        operation: "delete".into(),
        hash: None,
        size: 0,
        operation_id: Uuid::new_v4().to_string(),
    };
    stage(&mut ws, &binding, &next, b"");
    let original = ids(&ws, &binding, 2);
    assert_eq!(
        ws.sync_apply_pending_checked(
            &binding.id,
            &StopAfter {
                commits: Cell::new(0),
                after: 1
            }
        )
        .unwrap_err()
        .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    let trash = root.path().join(".ainote/trash").join(&original.0);
    assert_eq!(fs::read(&trash).unwrap(), b"before");
    assert!(!root.path().join("original.md").exists());
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 1);
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.sync_apply_pending(&binding.id).unwrap();
    assert_eq!(ids(&ws, &binding, 2), original);
    assert_eq!(fs::read(trash).unwrap(), b"before");
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 2);
    assert_eq!(rows(&ws, "operations"), 2);
    assert!(ws.entry("original.md").unwrap().unwrap().deleted);
    assert_eq!(rows(&ws, "outbox"), 0);
}

#[test]
fn sync_inbox_accepted_conflict_keeps_both_payloads_and_original_local_queue_before_cursor() {
    let (root, mut ws, binding) = setup();
    let local = ws
        .write("original.md", "", b"local exact", "local")
        .unwrap();
    let rev = revision(
        &Uuid::new_v4().to_string(),
        "original.md",
        b"remote exact",
        1,
    );
    stage(&mut ws, &binding, &rev, b"remote exact");
    let original = ids(&ws, &binding, 1);
    assert_eq!(
        ws.sync_apply_pending_checked(
            &binding.id,
            &StopAfter {
                commits: Cell::new(0),
                after: 1
            }
        )
        .unwrap_err()
        .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    let conflict: String = ws
        .db
        .query_row(
            "SELECT local_hash FROM sync_conflicts WHERE binding=?1 AND sequence=1",
            [&binding.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(conflict, local.hash);
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
    assert_eq!(ws.pending_count().unwrap(), 1);
    assert_eq!(
        fs::read(root.path().join("original.md")).unwrap(),
        b"local exact"
    );
    assert_eq!(
        fs::read(ws.sync_spool(&conflict).unwrap()).unwrap(),
        b"local exact"
    );
    assert_eq!(
        fs::read(ws.sync_spool(rev.hash.as_ref().unwrap()).unwrap()).unwrap(),
        b"remote exact"
    );
    ws.sync_apply_pending_checked(&binding.id, &UnscopedCommit)
        .unwrap();
    assert_eq!(ids(&ws, &binding, 1), original);
    assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 1);
    assert_eq!(rows(&ws, "sync_conflicts"), 1);
    assert_eq!(ws.pending_count().unwrap(), 1);
    assert_eq!(
        ws.entry("original.md").unwrap().unwrap().file_id,
        local.file_id
    );
}

#[test]
fn sync_ack_freeze_during_stop_retains_original_body_and_requires_new_foreground_before_ack() {
    let (_root, mut ws, binding) = setup();
    let operation = Uuid::new_v4().to_string();
    let entry = ws
        .write_operation("original.md", "", b"local exact", "local", &operation)
        .unwrap();
    ws.sync_capture(&binding.id).unwrap();
    let job = ws.sync_next(&binding.id).unwrap().unwrap();
    let response = serde_json::json!({"vault_id":binding.remote_vault,"sequence":1,"file_id":entry.file_id,"base_revision":0,"path":"original.md","operation":"put","hash":entry.hash,"size":11,"operation_id":operation});
    assert_eq!(
        ws.sync_ack_checked(
            &job,
            &response,
            &StopAfter {
                commits: Cell::new(0),
                after: 1
            }
        )
        .unwrap_err()
        .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    let original = ws.sync_next(&binding.id).unwrap().unwrap();
    assert_eq!(original.operation_id, job.operation_id);
    assert_eq!(original.base_revision, Some(0));
    assert_eq!(rows(&ws, "sync_heads"), 0);
    assert_eq!(ws.pending_count().unwrap(), 1);
    let payload = ws.sync_commit_payload(&original).unwrap();
    assert_eq!(payload["operation_id"], job.operation_id);
    assert_eq!(payload["base_revision"], 0);
    ws.sync_ack_checked(&original, &response, &UnscopedCommit)
        .unwrap();
    assert!(ws.sync_next(&binding.id).unwrap().is_none());
    assert_eq!(ws.sync_commit_payload(&original).unwrap(), payload);
    assert_eq!(rows(&ws, "sync_heads"), 1);
}

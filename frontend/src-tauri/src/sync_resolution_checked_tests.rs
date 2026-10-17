use crate::{
    sync_inbox::RemoteRevision,
    workspace::{hash, HostError, Result, Workspace},
    workspace_commit::{CommitGuard, UnscopedCommit},
};
use rusqlite::params;
use std::{cell::Cell, fs};
use uuid::Uuid;

struct StopAfter {
    commits: Cell<usize>,
    after: usize,
}
impl StopAfter {
    fn new(after: usize) -> Self {
        Self {
            commits: Cell::new(0),
            after,
        }
    }
}
impl CommitGuard for StopAfter {
    fn check(&self) -> Result<()> {
        if self.commits.get() >= self.after {
            Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
        } else {
            Ok(())
        }
    }
    fn commit(&self, tx: rusqlite::Transaction<'_>) -> Result<()> {
        self.check()?;
        tx.commit()?;
        self.commits.set(self.commits.get() + 1);
        Ok(())
    }
}
struct RefuseCommit;
impl CommitGuard for RefuseCommit {
    fn check(&self) -> Result<()> {
        Ok(())
    }
    fn commit(&self, _tx: rusqlite::Transaction<'_>) -> Result<()> {
        Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
    }
}
fn receive(ws: &mut Workspace, binding: &str, revision: &RemoteRevision) {
    ws.sync_set_boundary(binding, revision.sequence).unwrap();
    ws.sync_stage(binding, revision).unwrap();
    ws.sync_apply_pending(binding).unwrap();
}
fn fixture(local: &[u8]) -> (tempfile::TempDir, Workspace, String, RemoteRevision) {
    let root = tempfile::tempdir().unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let binding = ws
        .sync_bind_download("https://sync.example/", "remote-vault", "account")
        .unwrap();
    let mut remote = RemoteRevision {
        vault_id: "remote-vault".into(),
        sequence: 1,
        file_id: Uuid::new_v4().to_string(),
        base_revision: 0,
        path: "original.md".into(),
        operation: "put".into(),
        hash: Some(ws.sync_store_bytes(b"base").unwrap()),
        size: 4,
        operation_id: Uuid::new_v4().to_string(),
    };
    receive(&mut ws, &binding.id, &remote);
    ws.write(&remote.path, remote.hash.as_ref().unwrap(), local, "local")
        .unwrap();
    remote.sequence = 2;
    remote.base_revision = 1;
    remote.operation_id = Uuid::new_v4().to_string();
    remote.hash = Some(ws.sync_store_bytes(b"incoming").unwrap());
    remote.size = 8;
    receive(&mut ws, &binding.id, &remote);
    (root, ws, binding.id, remote)
}
fn ids(ws: &Workspace, binding: &str, sequence: i64) -> Vec<String> {
    ws.db.query_row(
        "SELECT operation_id,rename_id,copy_id,retire_id,restore_id FROM sync_resolutions WHERE binding=?1 AND sequence=?2",
        params![binding, sequence], |r| (0..5).map(|i| r.get(i)).collect(),
    ).unwrap()
}
fn rows(ws: &Workspace, table: &str) -> i64 {
    ws.db
        .query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0))
        .unwrap()
}
fn state(ws: &Workspace, binding: &str, sequence: i64) -> String {
    ws.db
        .query_row(
            "SELECT state FROM sync_resolutions WHERE binding=?1 AND sequence=?2",
            params![binding, sequence],
            |r| r.get(0),
        )
        .unwrap()
}

#[test]
fn sync_resolution_refused_decision_or_review_receipt_leaves_no_file_intent() {
    for reviewed in [false, true] {
        let (root, mut ws, binding, _) = fixture(b"local");
        let review = ws.sync_conflict_review(&binding, 2).unwrap();
        let before = rows(&ws, "operations");
        let error = if reviewed {
            ws.sync_resolve_reviewed_checked(
                &binding,
                2,
                ("copy", "copy.md", &review.local.hash, &review.fingerprint),
                &RefuseCommit,
            )
        } else {
            ws.sync_resolve_checked(
                &binding,
                2,
                "copy",
                "copy.md",
                &review.local.hash,
                &RefuseCommit,
            )
        }
        .unwrap_err();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(rows(&ws, "sync_resolutions"), 0);
        assert_eq!(rows(&ws, "sync_review_receipts"), 0);
        assert_eq!(rows(&ws, "operations"), before);
        assert_eq!(fs::read(root.path().join("original.md")).unwrap(), b"local");
        assert!(!root.path().join("copy.md").exists());
    }
}

#[test]
fn sync_resolution_review_cancels_between_chunks_without_changing_original_state() {
    struct Cancel(Cell<usize>);
    impl CommitGuard for Cancel {
        fn check(&self) -> Result<()> {
            self.0.set(self.0.get() + 1);
            if self.0.get() == 6 {
                Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
            } else {
                Ok(())
            }
        }
        fn commit(&self, _tx: rusqlite::Transaction<'_>) -> Result<()> {
            panic!("Review wrote SQL")
        }
    }
    let bytes = vec![b'x'; 256 * 1024];
    let (root, ws, binding, _) = fixture(&bytes);
    let before = ws.db.total_changes();
    let cancel = Cancel(Cell::new(0));
    assert_eq!(
        ws.sync_conflict_review_checked(&binding, 2, &cancel)
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    assert_eq!(cancel.0.get(), 6);
    assert_eq!(ws.db.total_changes(), before);
    assert_eq!(fs::read(root.path().join("original.md")).unwrap(), bytes);
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    assert_eq!(review.local.hash, hash(&bytes));
    assert_eq!(review.local.preview_bytes, 64 * 1024);
    assert!(review.local.truncated);
}

#[test]
fn sync_resolution_copy_finishes_accepted_original_then_waits_before_overwrite() {
    let (root, mut ws, binding, _) = fixture(b"local");
    let before = rows(&ws, "operations");
    assert_eq!(
        ws.sync_resolve_checked(
            &binding,
            2,
            "copy",
            "copy.md",
            &hash(b"local"),
            &StopAfter::new(2)
        )
        .unwrap_err()
        .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    let original = ids(&ws, &binding, 2);
    assert_eq!(state(&ws, &binding, 2), "pending");
    assert_eq!(fs::read(root.path().join("copy.md")).unwrap(), b"local");
    assert_eq!(fs::read(root.path().join("original.md")).unwrap(), b"local");
    assert_eq!(
        ws.operation(&original[2]).unwrap().unwrap()["state"],
        "committed"
    );
    assert!(ws.operation(&original[0]).unwrap().is_none());
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.sync_resume_resolutions_checked(&binding, &UnscopedCommit)
        .unwrap();
    ws.sync_resume_resolutions_checked(&binding, &UnscopedCommit)
        .unwrap();
    assert_eq!(ids(&ws, &binding, 2), original);
    assert_eq!(rows(&ws, "operations"), before + 2);
    assert_eq!(state(&ws, &binding, 2), "completed");
    assert_eq!(
        fs::read(root.path().join("original.md")).unwrap(),
        b"incoming"
    );
    assert_eq!(fs::read(root.path().join("copy.md")).unwrap(), b"local");
}

#[test]
fn sync_resolution_accepted_replace_waits_before_final_archival_and_resumes_same_ids() {
    for choice in ["local", "remote"] {
        let (root, mut ws, binding, _) = fixture(b"local");
        ws.sync_capture(&binding).unwrap();
        let before = rows(&ws, "operations");
        assert_eq!(
            ws.sync_resolve_checked(&binding, 2, choice, "", &hash(b"local"), &StopAfter::new(2))
                .unwrap_err()
                .code,
            "SYNC_FOREGROUND_CHANGED"
        );
        let original = ids(&ws, &binding, 2);
        assert_eq!(state(&ws, &binding, 2), "pending");
        assert_eq!(
            ws.operation(&original[0]).unwrap().unwrap()["state"],
            "committed"
        );
        assert_eq!(ws.sync_conflicts(&binding).unwrap().len(), 1);
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.sync_resume_resolutions_checked(&binding, &UnscopedCommit)
            .unwrap();
        assert_eq!(ids(&ws, &binding, 2), original);
        assert_eq!(rows(&ws, "operations"), before + 1);
        assert!(ws.sync_conflicts(&binding).unwrap().is_empty());
        assert_eq!(
            fs::read(root.path().join("original.md")).unwrap(),
            if choice == "local" {
                b"local".as_slice()
            } else {
                b"incoming".as_slice()
            }
        );
    }
}

#[test]
fn sync_resolution_reviewed_collision_resumes_each_accepted_phase_without_new_decision() {
    for limit in 2..=6 {
        let (root, mut ws, binding, mut remote) = fixture(b"local");
        ws.write("target.md", "", b"occupant", "local").unwrap();
        remote.sequence = 3;
        remote.base_revision = 2;
        remote.path = "target.md".into();
        remote.operation_id = Uuid::new_v4().to_string();
        receive(&mut ws, &binding, &remote);
        let review = ws.sync_conflict_review(&binding, 3).unwrap();
        let before = rows(&ws, "operations");
        let outcome = ws.sync_resolve_reviewed_checked(
            &binding,
            3,
            ("copy", "copy.md", &review.local.hash, &review.fingerprint),
            &StopAfter::new(limit),
        );
        assert_eq!(outcome.unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
        let original = ids(&ws, &binding, 3);
        let receipt: String = ws
            .db
            .query_row("SELECT guards FROM sync_review_receipts", [], |r| r.get(0))
            .unwrap();
        assert_eq!(state(&ws, &binding, 3), "pending");
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.sync_resolve_reviewed_checked(
            &binding,
            3,
            ("copy", "copy.md", &review.local.hash, &review.fingerprint),
            &UnscopedCommit,
        )
        .unwrap();
        assert_eq!(ids(&ws, &binding, 3), original);
        assert_eq!(
            ws.db
                .query_row("SELECT guards FROM sync_review_receipts", [], |r| r
                    .get::<_, String>(0))
                .unwrap(),
            receipt
        );
        assert_eq!(rows(&ws, "operations"), before + 4);
        assert_eq!(
            fs::read(root.path().join("target.md")).unwrap(),
            b"incoming"
        );
        assert_eq!(fs::read(root.path().join("copy.md")).unwrap(), b"occupant");
        assert!(!root.path().join("original.md").exists());
        assert!(ws
            .sync_conflicts(&binding)
            .unwrap()
            .iter()
            .all(|v| v["sequence"] != 3));
    }
}

#[test]
fn sync_resolution_partial_review_still_refuses_later_human_source_changes() {
    let (root, mut ws, binding, _) = fixture(b"local");
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    assert_eq!(
        ws.sync_resolve_reviewed_checked(
            &binding,
            2,
            ("copy", "copy.md", &review.local.hash, &review.fingerprint),
            &StopAfter::new(3)
        )
        .unwrap_err()
        .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    let original = ids(&ws, &binding, 2);
    fs::write(root.path().join("original.md"), b"human newer original").unwrap();
    assert_eq!(
        ws.sync_resume_resolutions_checked(&binding, &UnscopedCommit)
            .unwrap_err()
            .code,
        "SYNC_CONFLICT_CHANGED"
    );
    assert_eq!(ids(&ws, &binding, 2), original);
    assert_eq!(
        fs::read(root.path().join("original.md")).unwrap(),
        b"human newer original"
    );
    assert_eq!(fs::read(root.path().join("copy.md")).unwrap(), b"local");
    assert!(ws.operation(&original[0]).unwrap().is_none());
    assert_eq!(state(&ws, &binding, 2), "pending");
}

#[test]
fn sync_resolution_local_delete_intent_is_guarded_and_keeps_original_receipt_on_reopen() {
    let (root, mut ws, binding, mut remote) = fixture(b"local");
    // Finish the first conflict, then a local delete conflicts with a new remote put.
    ws.sync_resolve(&binding, 2, "remote", "", &hash(b"local"))
        .unwrap();
    ws.delete("original.md", &hash(b"incoming")).unwrap();
    remote.sequence = 3;
    remote.base_revision = 2;
    remote.operation_id = Uuid::new_v4().to_string();
    remote.hash = Some(ws.sync_store_bytes(b"new incoming").unwrap());
    remote.size = 12;
    receive(&mut ws, &binding, &remote);
    let before = rows(&ws, "operations");
    assert_eq!(
        ws.sync_resolve_checked(&binding, 3, "local", "", "", &StopAfter::new(2))
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    let original = ids(&ws, &binding, 3);
    assert_eq!(
        ws.operation(&original[0]).unwrap().unwrap()["state"],
        "committed"
    );
    assert_eq!(state(&ws, &binding, 3), "pending");
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.sync_resume_resolutions_checked(&binding, &UnscopedCommit)
        .unwrap();
    assert_eq!(ids(&ws, &binding, 3), original);
    assert_eq!(rows(&ws, "operations"), before + 1);
    assert_eq!(state(&ws, &binding, 3), "completed");
    assert!(!root.path().join("original.md").exists());
    assert_eq!(rows(&ws, "sync_resolutions"), 2);
}

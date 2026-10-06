use crate::{
    sync_inbox::RemoteRevision,
    workspace::{hash, Workspace},
};
use rusqlite::params;
use std::fs;
use uuid::Uuid;

fn receive(ws: &mut Workspace, binding: &str, revision: &RemoteRevision) {
    ws.sync_set_boundary(binding, revision.sequence).unwrap();
    ws.sync_stage(binding, revision).unwrap();
    ws.sync_apply_pending(binding).unwrap();
}
fn fixture(
    path: &str,
    local: &[u8],
    incoming: &[u8],
) -> (tempfile::TempDir, Workspace, String, RemoteRevision) {
    let root = tempfile::tempdir().unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let binding = ws
        .sync_bind_download("https://sync.example", "remote-vault", "account")
        .unwrap();
    let baseline = b"base\r\n";
    let mut revision = RemoteRevision {
        vault_id: binding.remote_vault.clone(),
        sequence: 1,
        file_id: Uuid::new_v4().to_string(),
        base_revision: 0,
        path: path.to_owned(),
        operation: "put".into(),
        hash: Some(ws.sync_store_bytes(baseline).unwrap()),
        size: baseline.len() as i64,
        operation_id: Uuid::new_v4().to_string(),
    };
    receive(&mut ws, &binding.id, &revision);
    ws.write(path, revision.hash.as_deref().unwrap(), local, "local")
        .unwrap();
    revision.base_revision = 1;
    revision.sequence = 2;
    revision.operation_id = Uuid::new_v4().to_string();
    revision.hash = Some(ws.sync_store_bytes(incoming).unwrap());
    revision.size = incoming.len() as i64;
    receive(&mut ws, &binding.id, &revision);
    (root, ws, binding.id, revision)
}
fn rename_fixture() -> (tempfile::TempDir, Workspace, String, RemoteRevision) {
    let (root, mut ws, binding, mut revision) =
        fixture("experiments/source.py", b"source local", b"remote");
    ws.write("experiments/target.py", "", b"occupied target", "local")
        .unwrap();
    revision.sequence = 3;
    revision.base_revision = 2;
    revision.path = "experiments/target.py".into();
    revision.operation_id = Uuid::new_v4().to_string();
    receive(&mut ws, &binding, &revision);
    (root, ws, binding, revision)
}
#[test]
fn rename_reviews_include_the_original_source_and_reject_later_source_edits() {
    let (root, mut ws, binding, revision) = rename_fixture();
    let review = ws.sync_conflict_review(&binding, 3).unwrap();
    assert_eq!(review.local_path, "experiments/target.py");
    assert!(review.base.is_none()); // Different local identity, not a common base.
    assert_eq!(review.related.len(), 1);
    assert_eq!(review.related[0].path, "experiments/source.py");
    assert_eq!(
        review.related[0].file_id.as_deref(),
        Some(revision.file_id.as_str())
    );
    assert_eq!(
        review.related[0].content.text.as_deref(),
        Some("source local")
    );
    ws.write(
        "experiments/source.py",
        &hash(b"source local"),
        b"human after review",
        "local",
    )
    .unwrap();
    assert_eq!(
        ws.sync_resolve_reviewed(
            &binding,
            3,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .unwrap_err()
        .code,
        "SYNC_CONFLICT_CHANGED"
    );
    assert_eq!(
        fs::read(root.path().join("experiments/source.py")).unwrap(),
        b"human after review"
    );
    assert_eq!(
        fs::read(root.path().join("experiments/target.py")).unwrap(),
        b"occupied target"
    );
}
#[test]
fn replacing_an_identity_with_equal_bytes_still_invalidates_the_review() {
    let (root, mut ws, binding, _) = rename_fixture();
    let review = ws.sync_conflict_review(&binding, 3).unwrap();
    ws.db
        .execute(
            "UPDATE files SET id=?1 WHERE path='experiments/target.py'",
            [Uuid::new_v4().to_string()],
        )
        .unwrap();
    assert_eq!(
        ws.sync_resolve_reviewed(
            &binding,
            3,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .unwrap_err()
        .code,
        "SYNC_CONFLICT_CHANGED"
    );
    assert_eq!(
        fs::read(root.path().join("experiments/target.py")).unwrap(),
        b"occupied target"
    );
    assert_eq!(
        fs::read(root.path().join("experiments/source.py")).unwrap(),
        b"source local"
    );
}
#[test]
fn missing_review_caches_are_not_created_or_repaired() {
    let (root, ws, binding, _) = fixture("a.md", b"local", b"remote");
    let cache = root.path().join(".ainote/sync-spool");
    assert!(cache
        .canonicalize()
        .unwrap()
        .starts_with(root.path().canonicalize().unwrap()));
    fs::remove_dir_all(&cache).unwrap();
    assert!(ws.sync_conflict_review(&binding, 2).is_err());
    assert!(!cache.exists());
    assert_eq!(fs::read(root.path().join("a.md")).unwrap(), b"local");
}
#[cfg(windows)]
#[test]
fn reviewed_case_only_renames_keep_identity_and_replay_their_committed_phase() {
    let (root, mut ws, binding, mut revision) =
        fixture("experiments/source.py", b"source local", b"remote");
    revision.sequence = 3;
    revision.base_revision = 2;
    revision.path = "Experiments/Source.py".into();
    revision.operation_id = Uuid::new_v4().to_string();
    receive(&mut ws, &binding, &revision);
    let review = ws.sync_conflict_review(&binding, 3).unwrap();
    ws.db.execute_batch("CREATE TRIGGER fail_case_write BEFORE INSERT ON journal WHEN NEW.path='Experiments/Source.py' BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    assert!(ws
        .sync_resolve_reviewed(
            &binding,
            3,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .is_err());
    assert_eq!(
        fs::read(root.path().join("Experiments/Source.py")).unwrap(),
        b"source local"
    );
    ws.db.execute_batch("DROP TRIGGER fail_case_write").unwrap();
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.sync_resume_resolutions(&binding).unwrap();
    assert_eq!(
        fs::read(root.path().join("Experiments/Source.py")).unwrap(),
        b"remote"
    );
    assert_eq!(
        ws.entry("Experiments/Source.py").unwrap().unwrap().file_id,
        revision.file_id
    );
    assert_eq!(
        ws.path_for_id(&revision.file_id).unwrap(),
        "Experiments/Source.py"
    );
}
#[test]
fn a_partial_target_retirement_cannot_approve_new_source_bytes_on_retry() {
    let (root, mut ws, binding, revision) = rename_fixture();
    let review = ws.sync_conflict_review(&binding, 3).unwrap();
    ws.db.execute_batch("CREATE TRIGGER fail_source BEFORE INSERT ON file_ops WHEN NEW.path='experiments/source.py' BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    assert!(ws
        .sync_resolve_reviewed(
            &binding,
            3,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .is_err());
    assert!(!root.path().join("experiments/target.py").exists());
    ws.write(
        "experiments/source.py",
        &hash(b"source local"),
        b"human after partial apply",
        "local",
    )
    .unwrap();
    ws.db.execute_batch("DROP TRIGGER fail_source").unwrap();
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    assert_eq!(
        ws.sync_resume_resolutions(&binding).unwrap_err().code,
        "SYNC_CONFLICT_CHANGED"
    );
    assert_eq!(
        fs::read(root.path().join("experiments/source.py")).unwrap(),
        b"human after partial apply"
    );
    assert!(!root.path().join("experiments/target.py").exists());
    // Restoring the original reviewed source permits that same durable decision.
    ws.write(
        "experiments/source.py",
        &hash(b"human after partial apply"),
        b"source local",
        "local",
    )
    .unwrap();
    ws.sync_resolve_reviewed(
        &binding,
        3,
        "remote",
        "",
        &review.local.hash,
        &review.fingerprint,
    )
    .unwrap();
    assert!(!root.path().join("experiments/source.py").exists());
    assert_eq!(
        fs::read(root.path().join("experiments/target.py")).unwrap(),
        b"remote"
    );
    assert_eq!(
        ws.entry("experiments/target.py").unwrap().unwrap().file_id,
        revision.file_id
    );
}
#[test]
fn a_committed_rename_only_resumes_with_the_reviewed_destination_bytes() {
    for human_edit in [false, true] {
        let (root, mut ws, binding, mut revision) =
            fixture("source.md", b"source local", b"remote");
        revision.sequence = 3;
        revision.base_revision = 2;
        revision.path = "destination.md".into();
        revision.operation_id = Uuid::new_v4().to_string();
        receive(&mut ws, &binding, &revision);
        let review = ws.sync_conflict_review(&binding, 3).unwrap();
        assert_eq!(review.related[0].path, "destination.md");
        assert!(!review.related[0].content.exists);
        ws.db.execute_batch("CREATE TRIGGER fail_write BEFORE INSERT ON journal WHEN NEW.path='destination.md' BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
        assert!(ws
            .sync_resolve_reviewed(
                &binding,
                3,
                "remote",
                "",
                &review.local.hash,
                &review.fingerprint
            )
            .is_err());
        assert!(!root.path().join("source.md").exists());
        assert_eq!(
            fs::read(root.path().join("destination.md")).unwrap(),
            b"source local"
        );
        ws.db.execute_batch("DROP TRIGGER fail_write").unwrap();
        if human_edit {
            ws.write(
                "destination.md",
                &hash(b"source local"),
                b"later human edit",
                "local",
            )
            .unwrap();
        }
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        let outcome = ws.sync_resume_resolutions(&binding);
        if human_edit {
            assert_eq!(outcome.unwrap_err().code, "SYNC_CONFLICT_CHANGED");
            assert_eq!(
                fs::read(root.path().join("destination.md")).unwrap(),
                b"later human edit"
            );
        } else {
            outcome.unwrap();
            assert_eq!(
                fs::read(root.path().join("destination.md")).unwrap(),
                b"remote"
            );
            assert!(ws.sync_conflicts(&binding).unwrap().is_empty());
        }
    }
}
#[test]
fn three_way_review_is_read_only_and_replays_only_the_original_decision() {
    let local = "本地😀\r\n".as_bytes();
    let remote = "远端 # %\r\n".as_bytes();
    let (root, mut ws, binding, _) = fixture("experiments/课程 #%.py", local, remote);
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    assert_eq!(review.local.text.as_deref(), Some("本地😀\r\n"));
    assert_eq!(review.incoming.text.as_deref(), Some("远端 # %\r\n"));
    assert_eq!(review.base.unwrap().text.as_deref(), Some("base\r\n"));
    assert_eq!(
        fs::read(root.path().join(&review.local_path)).unwrap(),
        local
    );
    ws.sync_resolve_reviewed(
        &binding,
        2,
        "copy",
        "conflicts/保留 #%.md",
        &review.local.hash,
        &review.fingerprint,
    )
    .unwrap();
    assert_eq!(
        fs::read(root.path().join("conflicts/保留 #%.md")).unwrap(),
        local
    );
    assert_eq!(
        fs::read(root.path().join(&review.local_path)).unwrap(),
        remote
    );
    ws.write(
        &review.local_path,
        &hash(remote),
        b"later human edit",
        "local",
    )
    .unwrap();
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.sync_resolve_reviewed(
        &binding,
        2,
        "copy",
        "conflicts/保留 #%.md",
        &review.local.hash,
        &review.fingerprint,
    )
    .unwrap();
    assert_eq!(
        fs::read(root.path().join(&review.local_path)).unwrap(),
        b"later human edit"
    );
    assert_eq!(
        ws.sync_resolve_reviewed(
            &binding,
            2,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .unwrap_err()
        .code,
        "SYNC_RESOLUTION_CHANGED"
    );
}
#[test]
fn edits_new_remote_heads_and_other_bindings_cannot_consume_the_old_review() {
    let (root, mut ws, binding, mut revision) = fixture("a.md", b"local", b"remote");
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    ws.write("a.md", &review.local.hash, b"human after review", "local")
        .unwrap();
    assert_eq!(
        ws.sync_resolve_reviewed(
            &binding,
            2,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .unwrap_err()
        .code,
        "SYNC_CONFLICT_CHANGED"
    );
    assert_eq!(
        fs::read(root.path().join("a.md")).unwrap(),
        b"human after review"
    );
    assert!(ws.sync_conflict_review("another-binding", 2).is_err());
    revision.base_revision = 2;
    revision.sequence = 3;
    revision.operation_id = Uuid::new_v4().to_string();
    revision.hash = Some(ws.sync_store_bytes(b"new remote").unwrap());
    revision.size = 10;
    receive(&mut ws, &binding, &revision);
    assert_eq!(
        ws.sync_conflict_review(&binding, 2).unwrap_err().code,
        "SYNC_CONFLICT_CHANGED"
    );
    assert!(
        ws.sync_conflict_review(&binding, 3).unwrap().base.is_none(),
        "An unresolved remote conflict is not a common ancestor"
    );
}
#[test]
fn missing_or_unrelated_baselines_are_two_way_and_utf8_prefixes_keep_full_hashes() {
    let mut local = vec![b'a'; 65535];
    local.extend_from_slice("中文😀\r\n".as_bytes());
    let (root, ws, binding, _) = fixture("a.md", &local, b"remote");
    fs::remove_file(ws.sync_spool(&hash(b"base\r\n")).unwrap()).unwrap();
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    assert!(review.base.is_none());
    assert!(review.local.truncated);
    assert_eq!(review.local.text.unwrap(), "a".repeat(65535));
    assert_eq!(review.local.preview_bytes, 65535);
    assert_eq!(review.local.hash, hash(&local));
    assert_eq!(review.local.byte_size, local.len() as u64);
    assert_eq!(fs::read(root.path().join("a.md")).unwrap(), local);
    // A cached same-sequence record for another file cannot fabricate a base.
    ws.db
        .execute(
            "UPDATE sync_inbox SET revision=json_set(revision,'$.file_id',?1) WHERE sequence=1",
            [Uuid::new_v4().to_string()],
        )
        .unwrap();
    assert!(ws.sync_conflict_review(&binding, 2).unwrap().base.is_none());
}
#[test]
fn receipt_publication_failure_and_corrupt_cached_content_never_apply_files() {
    let (root, mut ws, binding, revision) = fixture("a.md", b"local", b"remote");
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    ws.db.execute_batch("CREATE TRIGGER fail_review BEFORE INSERT ON sync_review_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    assert!(ws
        .sync_resolve_reviewed(
            &binding,
            2,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .is_err());
    assert_eq!(fs::read(root.path().join("a.md")).unwrap(), b"local");
    assert_eq!(
        ws.db
            .query_row("SELECT count(*) FROM sync_resolutions", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
    fs::write(
        ws.sync_spool(revision.hash.as_deref().unwrap()).unwrap(),
        b"forged",
    )
    .unwrap();
    assert_eq!(
        ws.sync_conflict_review(&binding, 2).unwrap_err().code,
        "SYNC_SPOOL_CORRUPT"
    );
}
#[test]
fn hard_links_are_rejected_and_binary_previews_do_not_decode_html() {
    let (root, ws, binding, _) = fixture("a.md", b"local", b"remote");
    fs::hard_link(root.path().join("a.md"), root.path().join("duplicate.md")).unwrap();
    assert_eq!(
        ws.sync_conflict_review(&binding, 2).unwrap_err().code,
        "SYNC_REVIEW_UNSAFE_FILE"
    );
    drop(ws);
    let (_binary_root, ws, binding, _) = fixture(
        "attachments/image.png",
        b"local binary",
        b"<img onerror=alert(1)>",
    );
    let review = ws.sync_conflict_review(&binding, 2).unwrap();
    assert!(review.local.text.is_none() && review.incoming.text.is_none());
    assert!(!review.incoming.truncated);
}
#[test]
fn acknowledged_upload_can_supply_a_verified_common_base() {
    let (_root, ws, binding, revision) = fixture("a.md", b"local", b"remote");
    ws.db
        .execute(
            "DELETE FROM sync_inbox WHERE binding=?1 AND sequence=1",
            [&binding],
        )
        .unwrap();
    ws.db.execute("INSERT INTO sync_jobs(binding,operation_id,file_id,base_revision,path,operation,hash,size,state,remote_revision) VALUES (?1,?2,?3,0,'a.md','put',?4,6,'acked',1)",
        params![binding,Uuid::new_v4().to_string(),revision.file_id,hash(b"base\r\n")]).unwrap();
    assert_eq!(
        ws.sync_conflict_review(&binding, 2)
            .unwrap()
            .base
            .unwrap()
            .text
            .as_deref(),
        Some("base\r\n")
    );
}

#[test]
fn a_remote_delete_and_a_later_local_deletion_have_distinct_missing_sides() {
    let (root, mut ws, binding, mut revision) = fixture("a.md", b"local", b"remote");
    revision.sequence = 3;
    revision.base_revision = 2;
    revision.operation = "delete".into();
    revision.operation_id = Uuid::new_v4().to_string();
    revision.hash = None;
    revision.size = 0;
    receive(&mut ws, &binding, &revision);
    let review = ws.sync_conflict_review(&binding, 3).unwrap();
    assert!(review.local.exists && !review.incoming.exists && review.base.is_none());
    fs::remove_file(root.path().join("a.md")).unwrap();
    let absent = ws.sync_conflict_review(&binding, 3).unwrap();
    assert!(!absent.local.exists && absent.local.hash.is_empty());
    assert_ne!(review.fingerprint, absent.fingerprint);
    assert_eq!(
        ws.sync_resolve_reviewed(
            &binding,
            3,
            "remote",
            "",
            &review.local.hash,
            &review.fingerprint
        )
        .unwrap_err()
        .code,
        "SYNC_CONFLICT_CHANGED"
    );
}

#[cfg(unix)]
#[test]
fn symlinked_files_and_parent_directories_never_return_foreign_bytes() {
    use std::os::unix::fs::symlink;
    let (root, ws, binding, _) = fixture("folder/a.md", b"local", b"remote");
    let outside = tempfile::tempdir().unwrap();
    fs::write(outside.path().join("a.md"), b"foreign").unwrap();
    fs::remove_file(root.path().join("folder/a.md")).unwrap();
    symlink(outside.path().join("a.md"), root.path().join("folder/a.md")).unwrap();
    assert!(ws.sync_conflict_review(&binding, 2).is_err());
    fs::remove_file(root.path().join("folder/a.md")).unwrap();
    fs::remove_dir(root.path().join("folder")).unwrap();
    symlink(outside.path(), root.path().join("folder")).unwrap();
    assert!(ws.sync_conflict_review(&binding, 2).is_err());
    assert_eq!(fs::read(outside.path().join("a.md")).unwrap(), b"foreign");
}

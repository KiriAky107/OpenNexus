use super::*;
use crate::sync_inbox::RemoteRevision;
use crate::sync_state::Binding;
use rusqlite::params;

fn files(ws: &mut Workspace) -> Vec<Entry> {
    ws.tree()
        .unwrap()
        .into_iter()
        .filter(|entry| !entry.is_folder)
        .collect()
}

fn replay(
    source: &mut Workspace,
    binding: &Binding,
    peer: &mut Workspace,
    peer_binding: &Binding,
    sequence: &mut i64,
) -> Vec<(String, String, String)> {
    replay_until(source, binding, peer, peer_binding, sequence, usize::MAX)
}

fn replay_until(
    source: &mut Workspace,
    binding: &Binding,
    peer: &mut Workspace,
    peer_binding: &Binding,
    sequence: &mut i64,
    limit: usize,
) -> Vec<(String, String, String)> {
    source.sync_capture(&binding.id).unwrap();
    let mut operations = Vec::new();
    while let Some(job) = source.sync_next(&binding.id).unwrap() {
        let payload = source.sync_commit_payload(&job).unwrap();
        *sequence += 1;
        let revision = RemoteRevision {
            vault_id: binding.remote_vault.clone(),
            sequence: *sequence,
            file_id: job.file_id.clone(),
            base_revision: payload["base_revision"].as_i64().unwrap(),
            path: job.path.clone(),
            operation: job.operation.clone(),
            hash: (job.operation == "put").then(|| job.hash.clone()),
            size: job.size,
            operation_id: job.operation_id.clone(),
        };
        if job.operation == "put" {
            let content = fs::read(source.sync_spool(&job.hash).unwrap()).unwrap();
            assert_eq!(peer.sync_store_bytes(&content).unwrap(), job.hash);
        }
        peer.sync_set_boundary(&peer_binding.id, *sequence).unwrap();
        peer.sync_stage(&peer_binding.id, &revision).unwrap();
        assert!(peer.sync_apply_pending(&peer_binding.id).unwrap());
        assert!(peer.sync_conflicts(&peer_binding.id).unwrap().is_empty());
        source
            .sync_ack(&job, &serde_json::to_value(revision).unwrap())
            .unwrap();
        operations.push((job.operation, job.file_id, job.path));
        if operations.len() == limit {
            break;
        }
    }
    operations
}

#[cfg(windows)]
#[test]
fn windows_remote_directory_case_change_does_not_echo_siblings_after_disconnect() {
    let root = tempfile::tempdir().unwrap();
    let peer_root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("Folder")).unwrap();
    fs::write(root.path().join("Folder/a.md"), b"a").unwrap();
    fs::write(root.path().join("Folder/b.md"), b"b").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let binding = ws
        .sync_bind_empty("https://sync.example", "remote", "test")
        .unwrap();
    let mut peer = Workspace::open(peer_root.path()).unwrap();
    let peer_binding = peer
        .sync_bind_download("https://sync.example", "remote", "test")
        .unwrap();
    let mut sequence = 0;
    replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence);
    let sibling = peer.entry("Folder/b.md").unwrap().unwrap().file_id;
    fs::rename(root.path().join("Folder"), root.path().join("folder")).unwrap();
    ws.changed_path("folder");
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 2);
    assert_eq!(
        replay_until(
            &mut ws,
            &binding,
            &mut peer,
            &peer_binding,
            &mut sequence,
            1
        )
        .len(),
        1
    );
    drop(peer);
    let mut peer = Workspace::open(peer_root.path()).unwrap();
    assert_eq!(peer.path_for_id(&sibling).unwrap(), "folder/b.md");
    assert_eq!(peer.sync_discover(&peer_binding.id).unwrap(), 0);
    assert_eq!(
        replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence).len(),
        1
    );
    assert_eq!(peer.pending_count().unwrap(), 0);
    assert!(peer.sync_conflicts(&peer_binding.id).unwrap().is_empty());
    assert_eq!(peer.read("folder/b.md").unwrap().content, "b");
}

#[cfg(windows)]
#[test]
fn windows_remote_directory_case_recovery_keeps_local_sibling_edits_pending() {
    for atomic_replace in [false, true] {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("Folder")).unwrap();
        fs::write(root.path().join("Folder/a.md"), b"a").unwrap();
        fs::write(root.path().join("Folder/b.md"), b"b").unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        // Remote-origin writes establish observed baselines without a local outbox.
        ws.write("Folder/a.md", &super::super::hash(b"a"), b"a", "remote")
            .unwrap();
        ws.write("Folder/b.md", &super::super::hash(b"b"), b"b", "remote")
            .unwrap();
        let sibling_id = ws.entry("Folder/b.md").unwrap().unwrap().file_id;
        let operation = uuid::Uuid::new_v4().to_string();
        ws.prepare_file_op_with_id(
            "rename",
            "Folder/a.md",
            "folder/a.md",
            &super::super::hash(b"a"),
            &operation,
            "remote",
        )
        .unwrap();
        // A crash after the directory spelling changed but before the DB commit.
        fs::rename(root.path().join("Folder"), root.path().join("folder")).unwrap();
        if atomic_replace {
            let replacement = tempfile::NamedTempFile::new_in(root.path().join("folder")).unwrap();
            fs::write(replacement.path(), b"local edit").unwrap();
            replacement
                .persist(root.path().join("folder/b.md"))
                .unwrap();
        } else {
            fs::write(root.path().join("folder/b.md"), b"local edit").unwrap();
        }
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.path_for_id(&sibling_id).unwrap(), "folder/b.md");
        assert_eq!(
            ws.entry("folder/b.md").unwrap().unwrap().hash,
            super::super::hash(b"local edit")
        );
        let observed: (String, String) = ws
            .db
            .query_row(
                "SELECT path,hash FROM sync_observed WHERE path='folder/b.md'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(observed, ("folder/b.md".into(), super::super::hash(b"b")));
        assert_eq!(ws.read("folder/b.md").unwrap().content, "local edit");
        assert_eq!(
            ws.operation(&operation).unwrap().unwrap()["state"],
            "committed"
        );
    }
}

#[cfg(windows)]
#[test]
fn windows_case_only_rename_keeps_identity_in_tree_sync_and_restart() {
    let root = tempfile::tempdir().unwrap();
    let remote_root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("Folder")).unwrap();
    fs::write(root.path().join("Folder/Note.md"), b"original").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let id = ws.entry("Folder/Note.md").unwrap().unwrap().file_id;
    let binding = ws
        .sync_bind_empty("https://sync.example", "remote", "test")
        .unwrap();
    let mut peer = Workspace::open(remote_root.path()).unwrap();
    let peer_binding = peer
        .sync_bind_download("https://sync.example", "remote", "test")
        .unwrap();
    let mut sequence = 0;
    replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence);
    fs::rename(
        root.path().join("Folder/Note.md"),
        root.path().join("Folder/note.md"),
    )
    .unwrap();
    // Only the obsolete name is delivered; exists() still returns true.
    ws.changed_path("Folder/Note.md");
    assert_eq!(
        files(&mut ws)
            .iter()
            .map(|e| (&e.file_id, e.path.as_str()))
            .collect::<Vec<_>>(),
        [(&id, "Folder/note.md")]
    );
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
    assert_eq!(
        replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence),
        [("put".into(), id.clone(), "Folder/note.md".into())]
    );
    assert_eq!(peer.read("Folder/note.md").unwrap().content, "original");
    assert_eq!(peer.path_for_id(&id).unwrap(), "Folder/note.md");
    fs::rename(root.path().join("Folder"), root.path().join("folder")).unwrap();
    ws.changed_path("folder");
    assert_eq!(files(&mut ws)[0].path, "folder/note.md");
    assert!(!ws
        .tree()
        .unwrap()
        .iter()
        .any(|e| e.path.starts_with("Folder")));
    assert_eq!(ws.path_for_id(&id).unwrap(), "folder/note.md");
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
    replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence);
    ws.scan().unwrap();
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    assert_eq!(files(&mut ws).len(), 1);
    assert_eq!(ws.path_for_id(&id).unwrap(), "folder/note.md");
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
    assert_eq!(peer.read("folder/note.md").unwrap().content, "original");
    assert_eq!(peer.path_for_id(&id).unwrap(), "folder/note.md");
    assert_eq!(files(&mut peer)[0].path, "folder/note.md");
}

#[cfg(windows)]
#[test]
fn windows_native_notification_renames_without_manual_hint() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("NOTE.md"), b"same").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let id = ws.entry("NOTE.md").unwrap().unwrap().file_id;
    assert_eq!(ws.watch_status().mode, "watch");
    ws.poll_change().unwrap();
    fs::rename(root.path().join("NOTE.md"), root.path().join("note.md")).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        ws.poll_change().unwrap();
        if ws.path_for_id(&id).unwrap() == "note.md" {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "native watcher missed case rename"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(files(&mut ws).len(), 1);
    assert_eq!(ws.read("NOTE.md").unwrap().entry.file_id, id);
    assert_eq!(files(&mut ws)[0].path, "note.md");
}

#[cfg(unix)]
#[test]
fn case_sensitive_names_remain_distinct() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("NOTE.md"), b"upper").unwrap();
    fs::write(root.path().join("note.md"), b"lower").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let entries = files(&mut ws);
    assert_eq!(entries.len(), 2);
    assert_ne!(entries[0].file_id, entries[1].file_id);
}

#[test]
fn one_hard_link_rename_keeps_two_distinct_logical_identities() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("a.md"), b"same").unwrap();
    fs::hard_link(root.path().join("a.md"), root.path().join("b.md")).unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let a = ws.entry("a.md").unwrap().unwrap().file_id;
    let b = ws.entry("b.md").unwrap().unwrap().file_id;
    fs::rename(root.path().join("a.md"), root.path().join("c.md")).unwrap();
    ws.changed_path("c.md");
    assert_eq!(files(&mut ws).len(), 2);
    assert_eq!(ws.path_for_id(&a).unwrap(), "c.md");
    assert_eq!(ws.path_for_id(&b).unwrap(), "b.md");
}

#[test]
fn rename_into_deleted_name_preserves_source_and_syncs_delete_before_put() {
    let root = tempfile::tempdir().unwrap();
    let remote_root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("a.md"), b"source").unwrap();
    std::thread::sleep(Duration::from_millis(10));
    fs::write(root.path().join("b.md"), b"retired").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let source_id = ws.entry("a.md").unwrap().unwrap().file_id;
    let retired_id = ws.entry("b.md").unwrap().unwrap().file_id;
    let binding = ws
        .sync_bind_empty("https://sync.example", "remote", "test")
        .unwrap();
    let mut peer = Workspace::open(remote_root.path()).unwrap();
    let peer_binding = peer
        .sync_bind_download("https://sync.example", "remote", "test")
        .unwrap();
    let mut sequence = 0;
    replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence);
    fs::remove_file(root.path().join("b.md")).unwrap();
    ws.scan().unwrap();
    fs::rename(root.path().join("a.md"), root.path().join("b.md")).unwrap();
    ws.changed_path("b.md");
    assert_eq!(files(&mut ws)[0].file_id, source_id);
    assert!(ws.path_for_id(&retired_id).is_err());
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 2);
    let operations = replay(&mut ws, &binding, &mut peer, &peer_binding, &mut sequence);
    assert_eq!(
        operations,
        [
            ("delete".into(), retired_id.clone(), "b.md".into()),
            ("put".into(), source_id.clone(), "b.md".into())
        ]
    );
    assert_eq!(peer.path_for_id(&source_id).unwrap(), "b.md");
    assert!(peer.path_for_id(&retired_id).is_err());
    assert_eq!(peer.read("b.md").unwrap().content, "source");
    assert_eq!(files(&mut peer).len(), 1);
    ws.scan().unwrap();
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    assert_eq!(ws.path_for_id(&source_id).unwrap(), "b.md");
    assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
    assert_eq!(files(&mut ws).len(), 1);
}

#[test]
fn lost_events_and_closed_workspace_renames_use_persisted_identity() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("a.md"), b"same bytes").unwrap();
    fs::write(root.path().join("unrelated.md"), b"same bytes").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let id = ws.entry("a.md").unwrap().unwrap().file_id;
    let other_id = ws.entry("unrelated.md").unwrap().unwrap().file_id;
    // Host atomic writes must also refresh persistent OS identities.
    ws.write(
        "a.md",
        &super::super::hash(b"same bytes"),
        b"edited",
        "local",
    )
    .unwrap();
    drop(ws);
    fs::rename(root.path().join("a.md"), root.path().join("moved.md")).unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    assert_eq!(ws.path_for_id(&id).unwrap(), "moved.md");
    assert_eq!(ws.path_for_id(&other_id).unwrap(), "unrelated.md");
    assert_eq!(files(&mut ws).len(), 2);
    fs::rename(root.path().join("moved.md"), root.path().join("again.md")).unwrap();
    ws.cache.hints.lock().unwrap().paths.clear();
    ws.cache.last_metadata = Instant::now() - METADATA_INTERVAL;
    assert_eq!(files(&mut ws).len(), 2);
    assert_eq!(ws.path_for_id(&id).unwrap(), "again.md");
}

#[test]
fn delete_then_recreate_is_new_identity_and_read_cannot_resurrect_tombstone() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("a.md"), b"identical").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let old = ws.entry("a.md").unwrap().unwrap().file_id;
    fs::remove_file(root.path().join("a.md")).unwrap();
    ws.scan().unwrap();
    fs::write(root.path().join("a.md"), b"identical").unwrap();
    let current = ws.read("a.md").unwrap().entry.file_id;
    assert_ne!(old, current);
    assert!(ws.path_for_id(&old).is_err());
    assert_eq!(files(&mut ws)[0].file_id, current);
}

#[test]
fn simultaneous_path_swap_preserves_both_ids_in_one_transaction() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("a.md"), b"a").unwrap();
    std::thread::sleep(Duration::from_millis(10));
    fs::write(root.path().join("b.md"), b"b").unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let a = ws.entry("a.md").unwrap().unwrap().file_id;
    let b = ws.entry("b.md").unwrap().unwrap().file_id;
    fs::rename(root.path().join("a.md"), root.path().join("temporary")).unwrap();
    fs::rename(root.path().join("b.md"), root.path().join("a.md")).unwrap();
    fs::rename(root.path().join("temporary"), root.path().join("b.md")).unwrap();
    ws.scan().unwrap();
    assert_eq!(ws.path_for_id(&a).unwrap(), "b.md");
    assert_eq!(ws.path_for_id(&b).unwrap(), "a.md");
}

#[test]
fn ambiguous_hard_links_and_failed_commit_leave_identity_snapshot_unchanged() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("a.md"), b"content").unwrap();
    fs::hard_link(root.path().join("a.md"), root.path().join("b.md")).unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let a = ws.entry("a.md").unwrap().unwrap();
    let b = ws.entry("b.md").unwrap().unwrap();
    assert_ne!(a.file_id, b.file_id);
    fs::rename(root.path().join("a.md"), root.path().join("c.md")).unwrap();
    fs::rename(root.path().join("b.md"), root.path().join("d.md")).unwrap();
    assert_eq!(ws.scan().unwrap_err().code, "FILE_IDENTITY_CONFLICT");
    assert_eq!(ws.path_for_id(&a.file_id).unwrap(), "a.md");
    assert_eq!(ws.path_for_id(&b.file_id).unwrap(), "b.md");
    fs::rename(root.path().join("d.md"), root.path().join("b.md")).unwrap();
    fs::remove_file(root.path().join("b.md")).unwrap();
    // Remove the ambiguity deliberately, then fail the commit midway.
    ws.db
        .execute("UPDATE files SET deleted=1 WHERE id=?1", [&b.file_id])
        .unwrap();
    ws.db.execute_batch("CREATE TRIGGER fail_identity BEFORE UPDATE ON files WHEN NEW.path='c.md' BEGIN SELECT RAISE(ABORT,'fixture'); END").unwrap();
    assert_eq!(ws.scan().unwrap_err().code, "DATABASE_ERROR");
    assert_eq!(ws.path_for_id(&a.file_id).unwrap(), "a.md");
    assert_eq!(ws.cache.entries["a.md"].entry.file_id, a.file_id);
    ws.db.execute_batch("DROP TRIGGER fail_identity").unwrap();
    ws.scan().unwrap();
    assert_eq!(ws.path_for_id(&a.file_id).unwrap(), "c.md");
}

#[cfg(windows)]
#[test]
fn windows_reused_file_index_with_different_creation_time_is_not_a_rename() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("old.md"), b"same").unwrap();
    let ws = Workspace::open(root.path()).unwrap();
    let old = ws.entry("old.md").unwrap().unwrap().file_id;
    fs::remove_file(root.path().join("old.md")).unwrap();
    fs::write(root.path().join("new.md"), b"same").unwrap();
    let identity = Stamp::read(&root.path().join("new.md"))
        .unwrap()
        .identity
        .unwrap();
    let (file_index, _) = identity.rsplit_once(':').unwrap();
    ws.db
        .execute(
            "UPDATE file_disk_identity SET identity=?2 WHERE file_id=?1",
            params![old, format!("{file_index}:different-birth-time")],
        )
        .unwrap();
    drop(ws);
    let mut ws = Workspace::open(root.path()).unwrap();
    assert_ne!(files(&mut ws)[0].file_id, old);
    assert!(ws.path_for_id(&old).is_err());
}

#[test]
fn schema_fourteen_backup_preserves_old_database_and_sync_queue() {
    let root = tempfile::tempdir().unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    let id = ws.write("a.md", "", b"safe", "local").unwrap().file_id;
    let count = ws.pending_count().unwrap();
    ws.db
        .execute_batch("DROP TABLE file_disk_identity; PRAGMA user_version=14")
        .unwrap();
    drop(ws);
    let ws = Workspace::open(root.path()).unwrap();
    assert_eq!(ws.path_for_id(&id).unwrap(), "a.md");
    assert_eq!(ws.pending_count().unwrap(), count);
    assert_eq!(
        ws.db
            .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        17
    );
    let backup = fs::read_dir(root.path().join(".ainote"))
        .unwrap()
        .filter_map(|e| e.ok())
        .find(|e| {
            e.file_name()
                .to_string_lossy()
                .starts_with("host-schema14-")
        })
        .unwrap();
    let original = rusqlite::Connection::open(backup.path()).unwrap();
    assert_eq!(
        original
            .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        14
    );
    assert_eq!(
        original
            .query_row("SELECT id FROM files WHERE path='a.md'", [], |r| r
                .get::<_, String>(0))
            .unwrap(),
        id
    );
    assert!(!original
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='file_disk_identity')",
            [],
            |r| r.get::<_, bool>(0)
        )
        .unwrap());
    let restored_root = tempfile::tempdir().unwrap();
    fs::create_dir(restored_root.path().join(".ainote")).unwrap();
    fs::copy(
        backup.path(),
        restored_root.path().join(".ainote/host.sqlite3"),
    )
    .unwrap();
    fs::create_dir(restored_root.path().join(".ainote/sync-spool")).unwrap();
    for item in fs::read_dir(root.path().join(".ainote/sync-spool")).unwrap() {
        let item = item.unwrap();
        fs::copy(
            item.path(),
            restored_root
                .path()
                .join(".ainote/sync-spool")
                .join(item.file_name()),
        )
        .unwrap();
    }
    fs::write(restored_root.path().join("a.md"), b"safe").unwrap();
    let mut restored = Workspace::open(restored_root.path()).unwrap();
    assert_eq!(restored.path_for_id(&id).unwrap(), "a.md");
    assert_eq!(restored.pending_count().unwrap(), count);
    assert_eq!(restored.read("a.md").unwrap().content, "safe");
    let operation: String = restored
        .db
        .query_row("SELECT operation_id FROM outbox LIMIT 1", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(restored.payload(&operation, &[]).unwrap(), b"safe");
}

use crate::{sync_inbox::RemoteRevision, sync_state::Binding, workspace::Workspace};
use std::fs;

struct Pair {
    source_root: tempfile::TempDir,
    peer_root: tempfile::TempDir,
    source: Workspace,
    peer: Workspace,
    source_binding: Binding,
    peer_binding: Binding,
    sequence: i64,
}

impl Pair {
    fn new(files: &[(&str, &str)]) -> Self {
        let source_root = tempfile::tempdir().unwrap();
        let peer_root = tempfile::tempdir().unwrap();
        for (path, content) in files {
            let path = source_root.path().join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, content).unwrap();
        }
        let mut source = Workspace::open(source_root.path()).unwrap();
        let mut peer = Workspace::open(peer_root.path()).unwrap();
        let source_binding = source
            .sync_bind_empty("https://sync.example", "remote", "test")
            .unwrap();
        let peer_binding = peer
            .sync_bind_download("https://sync.example", "remote", "test")
            .unwrap();
        let mut pair = Self {
            source_root,
            peer_root,
            source,
            peer,
            source_binding,
            peer_binding,
            sequence: 0,
        };
        pair.drain(false);
        pair
    }

    fn rename(&self, from: &str, to: &str) {
        fs::rename(
            self.source_root.path().join(from),
            self.source_root.path().join(to),
        )
        .unwrap();
    }

    fn discover(&mut self) -> usize {
        self.source.scan().unwrap();
        let count = self.source.sync_discover(&self.source_binding.id).unwrap();
        self.source.sync_capture(&self.source_binding.id).unwrap();
        count
    }

    fn next(&mut self) -> Option<RemoteRevision> {
        let job = self.source.sync_next(&self.source_binding.id).unwrap()?;
        let payload = self.source.sync_commit_payload(&job).unwrap();
        self.sequence += 1;
        if job.operation == "put" {
            let bytes = fs::read(self.source.sync_spool(&job.hash).unwrap()).unwrap();
            assert_eq!(self.peer.sync_store_bytes(&bytes).unwrap(), job.hash);
        }
        Some(RemoteRevision {
            vault_id: "remote".into(),
            sequence: self.sequence,
            file_id: job.file_id,
            base_revision: payload["base_revision"].as_i64().unwrap(),
            path: job.path,
            operation: job.operation.clone(),
            hash: (job.operation == "put").then_some(job.hash),
            size: job.size,
            operation_id: job.operation_id,
        })
    }

    fn apply(&mut self, revision: &RemoteRevision) {
        self.peer
            .sync_set_boundary(&self.peer_binding.id, revision.sequence)
            .unwrap();
        self.peer
            .sync_stage(&self.peer_binding.id, revision)
            .unwrap();
        assert!(self.peer.sync_apply_pending(&self.peer_binding.id).unwrap());
    }

    fn ack(&mut self, revision: &RemoteRevision) {
        let job = self
            .source
            .sync_next(&self.source_binding.id)
            .unwrap()
            .unwrap();
        self.source
            .sync_ack(&job, &serde_json::to_value(revision).unwrap())
            .unwrap();
    }

    fn reopen(&mut self) {
        // Workspace::open takes an exclusive vault lock. Drop both old handles
        // before reopening, without leaving an unbound placeholder workspace.
        let scratch_source = tempfile::tempdir().unwrap();
        let replacement = Workspace::open(scratch_source.path()).unwrap();
        drop(std::mem::replace(&mut self.source, replacement));
        self.source = Workspace::open(self.source_root.path()).unwrap();
        let scratch_peer = tempfile::tempdir().unwrap();
        let replacement = Workspace::open(scratch_peer.path()).unwrap();
        drop(std::mem::replace(&mut self.peer, replacement));
        self.peer = Workspace::open(self.peer_root.path()).unwrap();
    }

    fn drain(&mut self, reopen: bool) -> Vec<RemoteRevision> {
        let mut revisions = Vec::new();
        while let Some(revision) = self.next() {
            self.apply(&revision);
            assert!(self
                .peer
                .sync_conflicts(&self.peer_binding.id)
                .unwrap()
                .is_empty());
            if reopen {
                self.reopen();
            }
            self.ack(&revision);
            assert_eq!(
                self.source.sync_discover(&self.source_binding.id).unwrap(),
                0
            );
            assert_eq!(self.peer.sync_discover(&self.peer_binding.id).unwrap(), 0);
            revisions.push(revision);
        }
        revisions
    }

    fn assert_file(&mut self, path: &str, content: &str, id: &str) {
        for workspace in [&mut self.source, &mut self.peer] {
            let document = workspace.read(path).unwrap();
            assert_eq!(document.content, content);
            assert_eq!(document.entry.file_id, id);
        }
    }
}

#[test]
fn external_rename_chain_releases_destinations_before_puts() {
    let mut pair = Pair::new(&[("a.md", "alpha"), ("b.md", "beta")]);
    let a = pair.source.read("a.md").unwrap().entry.file_id;
    let b = pair.source.read("b.md").unwrap().entry.file_id;
    pair.rename("b.md", "c.md");
    pair.rename("a.md", "b.md");
    assert_eq!(pair.discover(), 2);
    let revisions = pair.drain(true);
    assert_eq!(
        revisions
            .iter()
            .map(|r| r.path.as_str())
            .collect::<Vec<_>>(),
        ["c.md", "b.md"]
    );
    pair.assert_file("b.md", "alpha", &a);
    pair.assert_file("c.md", "beta", &b);
    assert!(!pair.peer_root.path().join("a.md").exists());
}

#[test]
fn external_rename_cycles_preserve_edited_payloads_across_each_restart() {
    for count in [2, 3] {
        let files = [("a.md", "alpha"), ("b.md", "beta"), ("c.md", "gamma")];
        let mut pair = Pair::new(&files[..count]);
        let ids: Vec<_> = files[..count]
            .iter()
            .map(|(path, _)| pair.source.read(path).unwrap().entry.file_id)
            .collect();
        pair.rename("a.md", "temporary.md");
        for index in 1..count {
            pair.rename(files[index].0, files[index - 1].0);
        }
        pair.rename("temporary.md", files[count - 1].0);
        for index in 0..count {
            fs::write(
                pair.source_root.path().join(files[index].0),
                format!("edited-{}", (index + 1) % count),
            )
            .unwrap();
        }
        assert_eq!(pair.discover(), count);
        assert_eq!(pair.source.pending_count().unwrap(), (count + 1) as i64);
        pair.reopen();
        let revisions = pair.drain(true);
        assert_eq!(revisions.len(), count + 1);
        assert!(revisions[0].path.starts_with("OpenNexus-sync-rename-"));
        assert!(revisions.iter().all(|r| r.operation == "put"));
        assert_eq!(revisions[0].file_id, revisions[count].file_id);
        assert_ne!(revisions[0].operation_id, revisions[count].operation_id);
        assert_eq!(revisions[count].base_revision, revisions[0].sequence);
        for index in 0..count {
            let original = (index + 1) % count;
            pair.assert_file(
                files[index].0,
                &format!("edited-{original}"),
                &ids[original],
            );
        }
        assert_eq!(pair.peer.sync_paths().unwrap().len(), count);
        assert_eq!(pair.source.sync_paths().unwrap().len(), count);
    }
}

#[test]
fn rename_plan_failure_rolls_back_all_outbox_and_observed_changes() {
    let mut pair = Pair::new(&[("a.md", "alpha"), ("b.md", "beta")]);
    pair.rename("a.md", "temporary.md");
    pair.rename("b.md", "a.md");
    pair.rename("temporary.md", "b.md");
    pair.source.scan().unwrap();
    pair.source.db.execute_batch("CREATE TRIGGER reject_plan BEFORE INSERT ON outbox WHEN NEW.path='a.md' BEGIN SELECT RAISE(ABORT,'injected failure'); END;").unwrap();
    assert!(pair.source.sync_discover(&pair.source_binding.id).is_err());
    assert_eq!(pair.source.pending_count().unwrap(), 0);
    pair.reopen();
    pair.source
        .db
        .execute_batch("DROP TRIGGER reject_plan")
        .unwrap();
    assert_eq!(pair.discover(), 2);
    assert_eq!(pair.drain(true).len(), 3);
}

#[test]
fn rename_cycle_does_not_overwrite_an_edited_receiver_occupant() {
    let mut pair = Pair::new(&[("a.md", "alpha"), ("b.md", "beta")]);
    pair.rename("a.md", "temporary.md");
    pair.rename("b.md", "a.md");
    pair.rename("temporary.md", "b.md");
    assert_eq!(pair.discover(), 2);
    let local = pair.peer.read("b.md").unwrap();
    pair.peer
        .write("b.md", &local.entry.hash, b"local edit", "local")
        .unwrap();
    while let Some(revision) = pair.next() {
        pair.apply(&revision);
        pair.ack(&revision);
    }
    assert!(!pair
        .peer
        .sync_conflicts(&pair.peer_binding.id)
        .unwrap()
        .is_empty());
    assert_eq!(pair.peer.read("b.md").unwrap().content, "local edit");
    assert!(pair.peer.sync_paths().unwrap().iter().any(|path| fs::read(
        pair.peer_root.path().join(path)
    )
    .unwrap()
        == b"alpha"));
}

#[test]
fn generated_staging_destination_keeps_receiver_collision_protection() {
    let mut pair = Pair::new(&[("a.md", "alpha"), ("b.md", "beta")]);
    pair.rename("a.md", "temporary.md");
    pair.rename("b.md", "a.md");
    pair.rename("temporary.md", "b.md");
    assert_eq!(pair.discover(), 2);
    let staged = pair.next().unwrap();
    assert!(staged.path.contains("OpenNexus-sync-rename-"));
    fs::write(pair.peer_root.path().join(&staged.path), b"occupied").unwrap();
    pair.apply(&staged);
    assert_eq!(
        fs::read(pair.peer_root.path().join(&staged.path)).unwrap(),
        b"occupied"
    );
    assert_eq!(pair.peer.read("a.md").unwrap().content, "alpha");
    assert_eq!(pair.peer.read("b.md").unwrap().content, "beta");
    assert_eq!(
        pair.peer
            .sync_conflicts(&pair.peer_binding.id)
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn rename_cycle_staging_retains_attachment_classification() {
    let mut pair = Pair::new(&[
        ("attachments/a.png", "alpha"),
        ("attachments/b.png", "beta"),
    ]);
    pair.rename("attachments/a.png", "attachments/temporary.png");
    pair.rename("attachments/b.png", "attachments/a.png");
    pair.rename("attachments/temporary.png", "attachments/b.png");
    assert_eq!(pair.discover(), 2);
    let revisions = pair.drain(true);
    assert_eq!(revisions.len(), 3);
    assert!(revisions[0]
        .path
        .starts_with("attachments/OpenNexus-sync-rename-"));
}

#[test]
fn rename_cycle_resumes_after_staging_rename_or_file_commit_before_cursor() {
    for boundary in ["stage", "rename", "file"] {
        let mut pair = Pair::new(&[("a.md", "alpha"), ("b.md", "beta")]);
        pair.rename("a.md", "temporary.md");
        pair.rename("b.md", "a.md");
        pair.rename("temporary.md", "b.md");
        assert_eq!(pair.discover(), 2);
        let revision = pair.next().unwrap();
        let frozen = pair
            .source
            .sync_commit_payload(
                &pair
                    .source
                    .sync_next(&pair.source_binding.id)
                    .unwrap()
                    .unwrap(),
            )
            .unwrap();
        pair.peer
            .sync_set_boundary(&pair.peer_binding.id, revision.sequence)
            .unwrap();
        pair.peer
            .sync_stage(&pair.peer_binding.id, &revision)
            .unwrap();
        let (operation, rename): (String, String) = pair
            .peer
            .db
            .query_row(
                "SELECT operation_id,rename_id FROM sync_inbox WHERE binding=?1 AND sequence=?2",
                rusqlite::params![pair.peer_binding.id, revision.sequence],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        let old_path = pair.peer.path_for_id(&revision.file_id).unwrap();
        let old_hash = pair.peer.read(&old_path).unwrap().entry.hash;
        if boundary != "stage" {
            pair.peer
                .mutate_with_origin(
                    "rename",
                    &old_path,
                    &revision.path,
                    &old_hash,
                    &rename,
                    "remote",
                )
                .unwrap();
        }
        if boundary == "file" {
            pair.peer
                .write_spooled_with_identity(
                    &revision.path,
                    &old_hash,
                    (revision.hash.as_deref().unwrap(), revision.size as u64),
                    "remote",
                    &operation,
                    Some(&revision.file_id),
                )
                .unwrap();
        }
        assert_eq!(
            pair.peer.sync_binding().unwrap().unwrap().cursor,
            revision.sequence - 1
        );
        pair.reopen();
        let job = pair
            .source
            .sync_next(&pair.source_binding.id)
            .unwrap()
            .unwrap();
        assert_eq!(pair.source.sync_commit_payload(&job).unwrap(), frozen);
        assert!(pair.peer.sync_apply_pending(&pair.peer_binding.id).unwrap());
        assert_eq!(
            pair.peer.sync_binding().unwrap().unwrap().cursor,
            revision.sequence
        );
        assert!(pair
            .peer
            .sync_conflicts(&pair.peer_binding.id)
            .unwrap()
            .is_empty());
        pair.ack(&revision);
        assert_eq!(pair.drain(true).len(), 2);
        assert_eq!(pair.peer.read("a.md").unwrap().content, "beta");
        assert_eq!(pair.peer.read("b.md").unwrap().content, "alpha");
        assert!(!pair.peer_root.path().join(&revision.path).exists());
    }
}

#[cfg(windows)]
#[test]
fn external_rename_chain_orders_case_insensitive_destination_occupants() {
    let mut pair = Pair::new(&[("A.md", "alpha"), ("B.md", "beta")]);
    pair.rename("B.md", "c.md");
    pair.rename("A.md", "b.md");
    assert_eq!(pair.discover(), 2);
    let revisions = pair.drain(true);
    assert_eq!(revisions[0].path, "c.md");
    assert_eq!(revisions[1].path, "b.md");
    assert_eq!(pair.peer.read("b.md").unwrap().content, "alpha");
    assert_eq!(pair.peer.read("c.md").unwrap().content, "beta");
}

#[test]
fn queued_local_edit_and_successive_cycles_keep_their_original_operation_order() {
    let mut pair = Pair::new(&[("a.md", "alpha"), ("b.md", "beta")]);
    let a = pair.source.read("a.md").unwrap();
    let b = pair.source.read("b.md").unwrap().entry.file_id;
    pair.source
        .write("a.md", &a.entry.hash, b"updated alpha", "local")
        .unwrap();
    pair.source.sync_capture(&pair.source_binding.id).unwrap();
    let original = pair
        .source
        .sync_next(&pair.source_binding.id)
        .unwrap()
        .unwrap();
    let original_payload = pair.source.sync_commit_payload(&original).unwrap();
    for _ in 0..2 {
        pair.rename("a.md", "temporary.md");
        pair.rename("b.md", "a.md");
        pair.rename("temporary.md", "b.md");
        assert_eq!(pair.discover(), 2);
    }
    pair.reopen();
    assert_eq!(
        pair.source.sync_commit_payload(&original).unwrap(),
        original_payload
    );
    let revisions = pair.drain(true);
    assert_eq!(revisions.len(), 7);
    assert_eq!(revisions[0].operation_id, original.operation_id);
    assert_eq!(revisions[0].path, "a.md");
    pair.assert_file("a.md", "updated alpha", &a.entry.file_id);
    pair.assert_file("b.md", "beta", &b);
    assert_eq!(pair.peer.sync_paths().unwrap().len(), 2);
}

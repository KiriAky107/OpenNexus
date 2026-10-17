//! 初始合并使用已确认的固定远程快照，并且从不重播过时的路径。
use crate::{
    sync_inbox::RemoteRevision,
    sync_state::Binding,
    workspace::{hash, Entry, HostError, Result, Workspace},
};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[cfg(test)]
use std::fs;
use uuid::Uuid;
#[derive(Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub boundary: i64,
    pub items: Vec<RemoteRevision>,
}
#[derive(Serialize)]
pub struct Preview {
    pub fingerprint: String,
    pub boundary: i64,
    pub items: Vec<PreviewItem>,
}
#[derive(Serialize)]
pub struct PreviewItem {
    pub path: String,
    pub action: String,
}
#[derive(Serialize)]
struct Local {
    path: String,
    hash: String,
    size: usize,
}
struct PreparedInitialFile<'a> {
    local: Local,
    operation: String,
    old: Option<Entry>,
    file_id: String,
    remote: Option<&'a RemoteRevision>,
}

/// Only created by verified preparation. Its exclusive workspace borrow keeps
/// edits/association changes out until this one-use commit is consumed/dropped.
/// No file contents, credentials, clone or WebView deserialization are exposed.
pub(crate) struct InitialCommit<'workspace, 'source> {
    workspace: &'workspace mut Workspace,
    endpoint: &'source str,
    remote: &'source str,
    account: &'source str,
    snapshot: &'source Snapshot,
    files: Vec<PreparedInitialFile<'source>>,
}
impl Workspace {
    fn initial_local_checked(&self, check: &dyn Fn() -> Result<()>) -> Result<Vec<Local>> {
        self.sync_paths_checked(check)?
            .into_iter()
            .map(|path| {
                let source = self.resolve(&path)?;
                let (hash, size) = crate::payloads::sync_file_info_checked(&source, &path, check)?;
                Ok(Local {
                    path,
                    hash,
                    size: size as usize,
                })
            })
            .collect()
    }
    pub fn sync_preview(
        &self,
        endpoint: &str,
        remote: &str,
        account: &str,
        snapshot: &Snapshot,
    ) -> Result<Preview> {
        self.sync_preview_checked(endpoint, remote, account, snapshot, &|| Ok(()))
    }
    /// Read-only file preparation. A native caller can check its original lease
    /// between paths/chunks without holding a lifecycle read guard over I/O.
    /// This does not authorize or apply a binding or any outgoing operation.
    pub(crate) fn sync_preview_checked(
        &self,
        endpoint: &str,
        remote: &str,
        account: &str,
        snapshot: &Snapshot,
        check: &dyn Fn() -> Result<()>,
    ) -> Result<Preview> {
        check()?;
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        let local = self.initial_local_checked(check)?;
        self.initial_preview_from_local(endpoint, remote, account, snapshot, &local, check)
    }
    fn initial_preview_from_local(
        &self,
        endpoint: &str,
        remote: &str,
        account: &str,
        snapshot: &Snapshot,
        local: &[Local],
        check: &dyn Fn() -> Result<()>,
    ) -> Result<Preview> {
        let binding = Binding {
            id: String::new(),
            endpoint: endpoint.into(),
            remote_vault: remote.into(),
            account: account.into(),
            cursor: 0,
        };
        let mut paths = BTreeMap::new();
        for item in &snapshot.items {
            check()?;
            item.validate(&binding)?;
            self.resolve(&item.path)?;
            if item.sequence > snapshot.boundary {
                return Err(HostError::new("SYNC_RESPONSE_INVALID"));
            }
            if item.operation == "put" && self.sync_path_enabled(&item.path)? {
                paths.insert(item.path.clone(), "download");
            }
        }
        for item in local {
            check()?;
            let remote = snapshot
                .items
                .iter()
                .find(|v| v.path == item.path && v.operation == "put");
            paths.insert(
                item.path.clone(),
                match remote {
                    Some(r) if r.operation == "put" && r.hash.as_deref() == Some(&item.hash) => {
                        "identical"
                    }
                    Some(_) => "conflict",
                    None => "upload",
                },
            );
        }
        let fingerprint = hash(
            &serde_json::to_vec(&(
                &self.vault_id,
                endpoint,
                remote,
                account,
                &local,
                snapshot,
                self.sync_optional_scope()?,
            ))
            .map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?,
        );
        check()?;
        Ok(Preview {
            fingerprint,
            boundary: snapshot.boundary,
            items: paths
                .into_iter()
                .map(|(path, action)| PreviewItem {
                    path,
                    action: action.into(),
                })
                .collect(),
        })
    }
    pub fn sync_bind_initial(
        &mut self,
        endpoint: &str,
        remote: &str,
        account: &str,
        snapshot: &Snapshot,
        expected: &str,
    ) -> Result<Binding> {
        self.sync_prepare_initial(endpoint, remote, account, snapshot, expected, &|| Ok(()))?
            .commit()
    }
    /// All hashing/spooling happens before a caller takes its short lifecycle
    /// commit guard. Only private immutable payload cache references are staged;
    /// no binding, alias, cursor or outgoing operation is published here.
    pub(crate) fn sync_prepare_initial<'workspace, 'source>(
        &'workspace mut self,
        endpoint: &'source str,
        remote: &'source str,
        account: &'source str,
        snapshot: &'source Snapshot,
        expected: &str,
        check: &dyn Fn() -> Result<()>,
    ) -> Result<InitialCommit<'workspace, 'source>> {
        check()?;
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        let local = self.initial_local_checked(check)?;
        if self
            .initial_preview_from_local(endpoint, remote, account, snapshot, &local, check)?
            .fingerprint
            != expected
        {
            return Err(HostError::new("SYNC_PREVIEW_CHANGED"));
        }
        let mut files = Vec::new();
        for item in local {
            check()?;
            let operation = Uuid::new_v4().to_string();
            self.store_payload_file_checked(
                &operation,
                &self.resolve(&item.path)?,
                &item.hash,
                check,
            )
            .map_err(|error| {
                if error.code == "REVISION_CONFLICT" {
                    HostError::new("SYNC_PREVIEW_CHANGED")
                } else {
                    error
                }
            })?;
            let old = self.entry(&item.path)?;
            let remote = snapshot
                .items
                .iter()
                .find(|r| r.path == item.path && r.operation == "put");
            let file_id = remote
                .map(|r| r.file_id.clone())
                .or_else(|| old.as_ref().map(|v| v.file_id.clone()))
                .unwrap_or_else(|| Uuid::new_v4().to_string());
            files.push(PreparedInitialFile {
                local: item,
                operation,
                old,
                file_id,
                remote,
            });
        }
        check()?;
        Ok(InitialCommit {
            workspace: self,
            endpoint,
            remote,
            account,
            snapshot,
            files,
        })
    }
    pub fn sync_initial_pending(&self, binding: &str) -> Result<Option<Vec<RemoteRevision>>> {
        self.check_binding(binding)?;
        let active: bool = self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM sync_initial WHERE binding=?1)",
            [binding],
            |r| r.get(0),
        )?;
        if !active {
            return Ok(None);
        }
        let mut statement=self.db.prepare("SELECT i.revision FROM sync_initial_items i WHERE binding=?1 AND NOT EXISTS(SELECT 1 FROM sync_inbox n WHERE n.binding=i.binding AND n.sequence=i.sequence AND n.state!='pending') ORDER BY sequence")?;
        let rows = statement
            .query_map([binding], |r| r.get::<_, String>(0))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(Some(
            rows.iter()
                .map(|v| {
                    serde_json::from_str(v).map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))
                })
                .collect::<Result<_>>()?,
        ))
    }
    pub(crate) fn initial_revision(&self, binding: &str, sequence: i64) -> Result<Option<String>> {
        Ok(self.db.query_row("SELECT revision FROM sync_initial_items WHERE binding=?1 AND sequence=?2 AND EXISTS(SELECT 1 FROM sync_initial WHERE binding=?1)",params![binding,sequence],|r|r.get(0)).optional()?)
    }
}

impl InitialCommit<'_, '_> {
    pub(crate) fn commit(self) -> Result<Binding> {
        self.commit_with(|_, _| Ok(()))
    }
    /// The native receipt callback joins the very same transaction as binding,
    /// file identities and outbox publication. A failure rolls all of them back.
    pub(crate) fn commit_with(
        self,
        receipt: impl FnOnce(&rusqlite::Transaction<'_>, &Binding) -> Result<()>,
    ) -> Result<Binding> {
        let Self {
            workspace,
            endpoint,
            remote,
            account,
            snapshot,
            files,
        } = self;
        if workspace.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_ALREADY_BOUND"));
        }
        let id = Uuid::new_v4().to_string();
        let tx = workspace.db.transaction()?;
        tx.execute(
            "UPDATE outbox SET state='archived' WHERE state IN ('pending','queued')",
            [],
        )?;
        tx.execute(
            "INSERT INTO sync_bindings VALUES (?1,?2,?3,?4,'active',0)",
            params![id, endpoint, remote, account],
        )?;
        tx.execute(
            "INSERT INTO sync_initial VALUES (?1,?2)",
            params![id, snapshot.boundary],
        )?;
        for remote in &snapshot.items {
            if remote.operation == "put" {
                tx.execute(
                    "INSERT INTO sync_initial_items VALUES (?1,?2,?3)",
                    params![
                        id,
                        remote.sequence,
                        serde_json::to_string(remote)
                            .map_err(|_| HostError::new("SYNC_RESPONSE_INVALID"))?
                    ],
                )?;
            } else {
                tx.execute(
                    "INSERT INTO sync_heads VALUES (?1,?2,?3,?4,'')",
                    params![id, remote.file_id, remote.sequence, remote.path],
                )?;
            }
        }
        for PreparedInitialFile {
            local: item,
            operation,
            old,
            file_id,
            remote,
        } in files
        {
            if let Some(old) = old {
                if old.file_id != file_id {
                    let occupied: bool = tx.query_row(
                        "SELECT EXISTS(SELECT 1 FROM files WHERE id=?1)",
                        [&file_id],
                        |r| r.get(0),
                    )?;
                    if occupied {
                        return Err(HostError::new("SYNC_IDENTITY_CONFLICT"));
                    }
                    tx.execute(
                        "UPDATE files SET id=?1 WHERE id=?2",
                        params![file_id, old.file_id],
                    )?;
                    tx.execute(
                        "UPDATE file_aliases SET file_id=?1 WHERE file_id=?2",
                        params![file_id, old.file_id],
                    )?;
                    tx.execute(
                        "INSERT OR REPLACE INTO file_aliases VALUES (?1,?2)",
                        params![old.file_id, file_id],
                    )?;
                    tx.execute("DELETE FROM sync_observed WHERE file_id=?1", [&old.file_id])?;
                }
            }
            tx.execute("INSERT INTO files VALUES (?1,?2,?3,1,0) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,deleted=0",params![file_id,item.path,item.hash])?;
            tx.execute("INSERT INTO sync_observed VALUES (?1,?2,?3,0) ON CONFLICT(file_id) DO UPDATE SET path=excluded.path,hash=excluded.hash,deleted=0",params![file_id,item.path,item.hash])?;
            if let Some(remote) = remote.filter(|r| r.hash.as_deref() == Some(&item.hash)) {
                tx.execute(
                    "INSERT INTO sync_heads VALUES (?1,?2,?3,?4,?5)",
                    params![id, file_id, remote.sequence, item.path, item.hash],
                )?;
            } else {
                tx.execute("INSERT INTO outbox SELECT ?1,id,revision,path,hash,'put',X'','pending' FROM files WHERE id=?2",params![operation,file_id])?;
            }
        }
        if snapshot.items.iter().all(|item| item.operation == "delete") {
            tx.execute(
                "UPDATE sync_bindings SET cursor=?2 WHERE id=?1",
                params![id, snapshot.boundary],
            )?;
            tx.execute("DELETE FROM sync_initial WHERE binding=?1", [&id])?;
        }
        let binding = Binding {
            id: id.clone(),
            endpoint: endpoint.into(),
            remote_vault: remote.into(),
            account: account.into(),
            cursor: if snapshot.items.iter().all(|item| item.operation == "delete") {
                snapshot.boundary
            } else {
                0
            },
        };
        receipt(&tx, &binding)?;
        tx.commit()?;
        workspace
            .sync_binding()?
            .ok_or_else(|| HostError::new("DATABASE_ERROR"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn staged_initial_binding_publishes_its_receipt_and_outbox_in_one_transaction() {
        for reject in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let old = ws
                .write("original.md", "", b"original\r\n", "local")
                .unwrap();
            ws.db.execute_batch("CREATE TABLE owned_binding_receipt(original_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL);").unwrap();
            let snapshot = Snapshot {
                boundary: 1,
                items: vec![revision(1, "original.md", b"remote-original")],
            };
            let fingerprint = ws
                .sync_preview("https://sync.example/", "remote", "reader", &snapshot)
                .unwrap()
                .fingerprint;
            let count: i64 = ws
                .db
                .query_row(
                    "SELECT COUNT(*) FROM outbox WHERE state='pending'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            let staged = ws
                .sync_prepare_initial(
                    "https://sync.example/",
                    "remote",
                    "reader",
                    &snapshot,
                    &fingerprint,
                    &|| Ok(()),
                )
                .unwrap();
            assert!(staged.workspace.sync_binding().unwrap().is_none());
            assert_eq!(
                staged
                    .workspace
                    .db
                    .query_row(
                        "SELECT COUNT(*) FROM outbox WHERE state='pending'",
                        [],
                        |row| row.get::<_, i64>(0)
                    )
                    .unwrap(),
                count
            );
            let result = staged.commit_with(|tx, binding| {
                tx.execute(
                    "INSERT INTO owned_binding_receipt VALUES ('original-bind',?1)",
                    [&binding.id],
                )?;
                if reject {
                    Err(HostError::new("OWNED_RECEIPT_REFUSED"))
                } else {
                    Ok(())
                }
            });
            if reject {
                assert_eq!(result.err().unwrap().code, "OWNED_RECEIPT_REFUSED");
                assert!(ws.sync_binding().unwrap().is_none());
                assert_eq!(
                    ws.entry("original.md").unwrap().unwrap().file_id,
                    old.file_id
                );
                assert_eq!(
                    ws.db
                        .query_row("SELECT COUNT(*) FROM owned_binding_receipt", [], |row| row
                            .get::<_, i64>(
                            0
                        ))
                        .unwrap(),
                    0
                );
                assert_eq!(
                    ws.db
                        .query_row(
                            "SELECT COUNT(*) FROM outbox WHERE state='pending'",
                            [],
                            |row| row.get::<_, i64>(0)
                        )
                        .unwrap(),
                    count
                );
            } else {
                let binding = result.unwrap();
                assert_eq!(ws.db.query_row("SELECT binding_id FROM owned_binding_receipt WHERE original_id='original-bind'",[],|row|row.get::<_,String>(0)).unwrap(),binding.id);
                assert_eq!(ws.sync_binding().unwrap().unwrap().id, binding.id);
                assert_eq!(
                    ws.entry("original.md").unwrap().unwrap().file_id,
                    snapshot.items[0].file_id
                );
                assert_eq!(
                    ws.sync_initial_pending(&binding.id).unwrap().unwrap().len(),
                    1
                );
            }
            assert_eq!(
                fs::read(root.path().join("original.md")).unwrap(),
                b"original\r\n"
            );
        }
    }
    #[cfg(feature = "sync-http")]
    #[test]
    fn native_stop_after_preparation_refuses_the_one_use_commit_without_a_binding_or_receipt() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("original.md", "", b"original\r\n", "local")
            .unwrap();
        let snapshot = Snapshot {
            boundary: 0,
            items: vec![],
        };
        let fingerprint = ws
            .sync_preview("https://sync.example/", "remote", "reader", &snapshot)
            .unwrap()
            .fingerprint;
        let foreground = crate::mobile_sync_foreground::Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        let staged = ws
            .sync_prepare_initial(
                "https://sync.example/",
                "remote",
                "reader",
                &snapshot,
                &fingerprint,
                &|| lease.check().map_err(|error| HostError::new(&error.code)),
            )
            .unwrap();
        foreground.on_stop();
        let result = lease.with_current(|| staged.commit().map_err(Into::into));
        assert_eq!(result.err().unwrap().code, "SYNC_FOREGROUND_CHANGED");
        assert!(ws.sync_binding().unwrap().is_none());
        assert_eq!(
            fs::read(root.path().join("original.md")).unwrap(),
            b"original\r\n"
        );
    }
    #[test]
    fn a_file_change_after_frozen_scan_is_not_recaptured_as_an_approved_binding() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("original.md", "", b"approved-original", "local")
            .unwrap();
        let snapshot = Snapshot {
            boundary: 0,
            items: vec![],
        };
        let scans = std::cell::Cell::new(0);
        let fingerprint = ws
            .sync_preview_checked(
                "https://sync.example/",
                "remote",
                "reader",
                &snapshot,
                &|| {
                    scans.set(scans.get() + 1);
                    Ok(())
                },
            )
            .unwrap()
            .fingerprint;
        let calls = std::cell::Cell::new(0);
        let changed = std::cell::Cell::new(false);
        let failure = ws.sync_prepare_initial(
            "https://sync.example/",
            "remote",
            "reader",
            &snapshot,
            &fingerprint,
            &|| {
                calls.set(calls.get() + 1);
                // Change after the checked preview's frozen scan/plan sequence:
                // the first spool's loop and payload-entry checks follow it.
                // The file must still match the already approved original digest.
                if calls.get() == scans.get() + 2 {
                    fs::write(root.path().join("original.md"), b"new-independent-edit")?;
                    changed.set(true);
                }
                Ok(())
            },
        );
        assert!(changed.get());
        assert_eq!(failure.err().unwrap().code, "SYNC_PREVIEW_CHANGED");
        assert!(ws.sync_binding().unwrap().is_none());
        assert_eq!(
            fs::read(root.path().join("original.md")).unwrap(),
            b"new-independent-edit"
        );
    }
    #[test]
    fn checked_initial_preview_has_the_same_fingerprint_and_cancellation_has_no_binding_or_outbox_effect(
    ) {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("original.md", "", b"original\r\n", "local")
            .unwrap();
        let snapshot = Snapshot {
            boundary: 0,
            items: vec![],
        };
        let original = ws
            .sync_preview("https://sync.example/", "remote", "reader", &snapshot)
            .unwrap();
        let checked = ws
            .sync_preview_checked(
                "https://sync.example/",
                "remote",
                "reader",
                &snapshot,
                &|| Ok(()),
            )
            .unwrap();
        assert_eq!(
            serde_json::to_value(&checked).unwrap(),
            serde_json::to_value(&original).unwrap()
        );
        let outbox: i64 = ws
            .db
            .query_row("SELECT COUNT(*) FROM outbox", [], |row| row.get(0))
            .unwrap();
        let calls = std::cell::Cell::new(0);
        let error = ws
            .sync_preview_checked(
                "https://sync.example/",
                "remote",
                "reader",
                &snapshot,
                &|| {
                    calls.set(calls.get() + 1);
                    if calls.get() == 4 {
                        Err(HostError::new("SYNC_FOREGROUND_CHANGED"))
                    } else {
                        Ok(())
                    }
                },
            )
            .err()
            .unwrap();
        assert_eq!(error.code, "SYNC_FOREGROUND_CHANGED");
        assert_eq!(calls.get(), 4);
        assert!(ws.sync_binding().unwrap().is_none());
        assert_eq!(
            ws.db
                .query_row("SELECT COUNT(*) FROM outbox", [], |row| row
                    .get::<_, i64>(0))
                .unwrap(),
            outbox
        );
        assert_eq!(
            fs::read(root.path().join("original.md")).unwrap(),
            b"original\r\n"
        );
        assert_eq!(
            ws.sync_preview("https://sync.example/", "remote", "reader", &snapshot)
                .unwrap()
                .fingerprint,
            original.fingerprint
        );
    }
    fn revision(sequence: i64, path: &str, content: &[u8]) -> RemoteRevision {
        RemoteRevision {
            vault_id: "remote".into(),
            sequence,
            file_id: Uuid::new_v4().to_string(),
            base_revision: 0,
            path: path.into(),
            operation: "put".into(),
            hash: Some(hash(content)),
            size: content.len() as i64,
            operation_id: Uuid::new_v4().to_string(),
        }
    }
    #[test]
    fn hundred_mib_discovery_preview_and_rebinding_preserve_all_current_files() {
        use std::io::{Seek, SeekFrom, Write};
        for initial in [false, true] {
            let root = tempfile::tempdir().unwrap();
            fs::create_dir(root.path().join("attachments")).unwrap();
            let source = root.path().join("large.md");
            let mut file = fs::File::create(&source).unwrap();
            let block = vec![b'a'; 64 * 1024];
            for _ in 0..1600 {
                file.write_all(&block).unwrap();
            }
            file.sync_all().unwrap();
            drop(file);
            fs::copy(&source, root.path().join("attachments/large.bin")).unwrap();
            let mut ws = Workspace::open(root.path()).unwrap();
            let snapshot = Snapshot {
                boundary: 0,
                items: Vec::new(),
            };
            let binding = if initial {
                let preview = ws
                    .sync_preview("https://sync.example", "remote", "account", &snapshot)
                    .unwrap();
                let mut file = fs::OpenOptions::new().write(true).open(&source).unwrap();
                file.seek(SeekFrom::End(-1)).unwrap();
                file.write_all(b"b").unwrap();
                file.sync_all().unwrap();
                drop(file);
                assert_eq!(
                    ws.sync_bind_initial(
                        "https://sync.example",
                        "remote",
                        "account",
                        &snapshot,
                        &preview.fingerprint
                    )
                    .err()
                    .unwrap()
                    .code,
                    "SYNC_PREVIEW_CHANGED"
                );
                let preview = ws
                    .sync_preview("https://sync.example", "remote", "account", &snapshot)
                    .unwrap();
                ws.sync_bind_initial(
                    "https://sync.example",
                    "remote",
                    "account",
                    &snapshot,
                    &preview.fingerprint,
                )
                .unwrap()
            } else {
                ws.sync_bind_empty("https://sync.example", "remote", "account")
                    .unwrap()
            };
            assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
            ws.sync_capture(&binding.id).unwrap();
            assert_eq!(ws.pending_count().unwrap(), 2);
            let identity = ws.entry("large.md").unwrap().unwrap().file_id;
            let mut file = fs::OpenOptions::new().write(true).open(&source).unwrap();
            file.seek(SeekFrom::End(-1)).unwrap();
            file.write_all(b"c").unwrap();
            file.sync_all().unwrap();
            drop(file);
            ws.scan().unwrap();
            assert_eq!(ws.sync_discover(&binding.id).unwrap(), 1);
            assert_eq!(ws.entry("large.md").unwrap().unwrap().file_id, identity);
            drop(ws);
            ws = Workspace::open(root.path()).unwrap();
            assert_eq!(ws.sync_discover(&binding.id).unwrap(), 0);
            assert_eq!(ws.pending_count().unwrap(), 3);
            ws.sync_unbind(&binding.id).unwrap();
            let rebound = ws
                .sync_bind_empty("https://sync.example", "another-vault", "another-account")
                .unwrap();
            assert_eq!(ws.pending_count().unwrap(), 2);
            assert_eq!(
                ws.db
                    .query_row(
                        "SELECT COUNT(*) FROM sync_jobs WHERE binding=?1",
                        [&rebound.id],
                        |r| r.get::<_, i64>(0)
                    )
                    .unwrap(),
                2
            );
            assert_eq!(ws.sync_discover(&rebound.id).unwrap(), 0);
            assert_eq!(ws.entry("large.md").unwrap().unwrap().file_id, identity);
        }
    }
    #[test]
    fn initial_snapshot_cursor_waits_for_all_files_and_recovers_twenty_rounds() {
        for _ in 0..20 {
            for committed in [false, true] {
                let root = tempfile::tempdir().unwrap();
                let mut ws = Workspace::open(root.path()).unwrap();
                ws.write("same.md", "", b"same", "local").unwrap();
                ws.write("local.md", "", b"local", "local").unwrap();
                let same = revision(2, "same.md", b"same");
                let new = revision(4, "remote.md", b"remote");
                let mut deleted = revision(5, "old.md", b"");
                deleted.operation = "delete".into();
                deleted.hash = None;
                let snapshot = Snapshot {
                    boundary: 5,
                    items: vec![same.clone(), new.clone(), deleted],
                };
                let preview = ws
                    .sync_preview("https://sync.example", "remote", "account", &snapshot)
                    .unwrap();
                let binding = ws
                    .sync_bind_initial(
                        "https://sync.example",
                        "remote",
                        "account",
                        &snapshot,
                        &preview.fingerprint,
                    )
                    .unwrap();
                ws.sync_stage(&binding.id, &same).unwrap();
                ws.sync_apply_pending(&binding.id).unwrap();
                assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
                ws.sync_store_bytes(b"remote").unwrap();
                ws.sync_stage(&binding.id, &new).unwrap();
                if committed {
                    let operation: String = ws
                        .db
                        .query_row(
                            "SELECT operation_id FROM sync_inbox WHERE sequence=4",
                            [],
                            |r| r.get(0),
                        )
                        .unwrap();
                    ws.write_with_identity(
                        "remote.md",
                        "",
                        b"remote",
                        "remote",
                        &operation,
                        Some(&new.file_id),
                    )
                    .unwrap();
                }
                drop(ws);
                let mut ws = Workspace::open(root.path()).unwrap();
                assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 0);
                ws.sync_apply_pending(&binding.id).unwrap();
                assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 5);
                assert!(ws.sync_initial_pending(&binding.id).unwrap().is_none());
                assert_eq!(ws.read("same.md").unwrap().entry.file_id, same.file_id);
                assert_eq!(ws.read("remote.md").unwrap().content, "remote");
                assert_eq!(ws.pending_count().unwrap(), 1);
                let deletes: i64 = ws
                    .db
                    .query_row(
                        "SELECT count(*) FROM outbox WHERE operation='delete'",
                        [],
                        |r| r.get(0),
                    )
                    .unwrap();
                assert_eq!(deletes, 0);
            }
        }
    }
    #[test]
    fn stale_preview_is_rejected_without_binding_or_file_changes() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let snapshot = Snapshot {
            boundary: 0,
            items: vec![],
        };
        let preview = ws
            .sync_preview("https://sync.example", "remote", "account", &snapshot)
            .unwrap();
        ws.write("new.md", "", b"new", "local").unwrap();
        assert_eq!(
            ws.sync_bind_initial(
                "https://sync.example",
                "remote",
                "account",
                &snapshot,
                &preview.fingerprint
            )
            .err()
            .unwrap()
            .code,
            "SYNC_PREVIEW_CHANGED"
        );
        assert!(ws.sync_binding().unwrap().is_none());
        assert_eq!(ws.read("new.md").unwrap().content, "new");
    }
}

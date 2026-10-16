//! Frozen rename/delete intents share the original Workspace receipt protocol.
use super::*;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct MutationIntent {
    pub operation_id: String,
    pub kind: String,
    pub file_id: String,
    pub path: String,
    pub destination: String,
    pub expected: String,
    pub state: String,
    pub error: Option<String>,
}

pub(super) fn initialize(db: &Connection) -> Result<()> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS mutation_intents (
        operation_id TEXT PRIMARY KEY,kind TEXT NOT NULL,file_id TEXT NOT NULL,path TEXT NOT NULL,
        destination TEXT NOT NULL,expected TEXT NOT NULL,state TEXT NOT NULL,error TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS active_file_mutation ON mutation_intents(file_id) WHERE state='prepared';")?;
    Ok(())
}

fn row(value: &rusqlite::Row<'_>) -> rusqlite::Result<MutationIntent> {
    Ok(MutationIntent {
        operation_id: value.get(0)?,
        kind: value.get(1)?,
        file_id: value.get(2)?,
        path: value.get(3)?,
        destination: value.get(4)?,
        expected: value.get(5)?,
        state: value.get(6)?,
        error: value.get(7)?,
    })
}
const COLUMNS: &str = "operation_id,kind,file_id,path,destination,expected,state,error";

impl DraftStore {
    pub fn pending_operations(&self, offset: usize, limit: usize) -> Result<serde_json::Value> {
        if !(1..=100).contains(&limit) || offset > i64::MAX as usize {
            return Err(HostError::new("MOBILE_PAGE_INVALID"));
        }
        let total:i64=self.db.query_row("SELECT (SELECT COUNT(*) FROM save_intents WHERE state='prepared')+(SELECT COUNT(*) FROM mutation_intents WHERE state='prepared')+(SELECT COUNT(*) FROM image_intents WHERE state='prepared')",[],|r|r.get(0))?;
        let mut query=self.db.prepare("SELECT operation_id,'save' AS kind,draft_id,path,'' AS destination,state,error FROM save_intents WHERE state='prepared'
            UNION ALL SELECT operation_id,kind,NULL AS draft_id,path,destination,state,error FROM mutation_intents WHERE state='prepared'
            UNION ALL SELECT operation_id,'image' AS kind,draft_id,path,asset_path AS destination,state,error FROM image_intents WHERE state='prepared'
            ORDER BY operation_id LIMIT ?1 OFFSET ?2")?;
        let items=query.query_map(params![limit as i64,offset as i64],|r|Ok(serde_json::json!({"operation_id":r.get::<_,String>(0)?,"kind":r.get::<_,String>(1)?,"draft_id":r.get::<_,Option<String>>(2)?,"path":r.get::<_,String>(3)?,"destination":r.get::<_,String>(4)?,"state":r.get::<_,String>(5)?,"error":r.get::<_,Option<String>>(6)?})))?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(serde_json::json!({"items":items,"total":total}))
    }

    pub fn pending_mutation(&self, file_id: &str) -> Result<Option<MutationIntent>> {
        Ok(self
            .db
            .query_row(
                &format!(
                    "SELECT {COLUMNS} FROM mutation_intents WHERE file_id=?1 AND state='prepared'"
                ),
                [file_id],
                row,
            )
            .optional()?)
    }

    pub fn mutation(&self, id: &str) -> Result<MutationIntent> {
        self.db
            .query_row(
                &format!("SELECT {COLUMNS} FROM mutation_intents WHERE operation_id=?1"),
                [id],
                row,
            )
            .optional()?
            .ok_or_else(|| HostError::new("OPERATION_NOT_FOUND"))
    }

    fn require_clean_file(&self, file_id: &str) -> Result<()> {
        let busy:bool=self.db.query_row("SELECT EXISTS(SELECT 1 FROM drafts WHERE file_id=?1 AND
            (generation<>saved_generation OR EXISTS(SELECT 1 FROM save_intents WHERE draft_id=drafts.id AND state='prepared') OR EXISTS(SELECT 1 FROM image_intents WHERE draft_id=drafts.id AND state='prepared')))",[file_id],|r|r.get(0))?;
        if busy {
            return Err(HostError::new("DRAFT_REQUIRES_SAVE"));
        }
        Ok(())
    }

    pub fn prepare_mutation(
        &mut self,
        ws: &mut Workspace,
        kind: &str,
        file_id: &str,
        expected: &str,
        destination: &str,
    ) -> Result<MutationIntent> {
        if !matches!(kind, "rename" | "delete") || (kind == "delete" && !destination.is_empty()) {
            return Err(HostError::new("INVALID_OPERATION"));
        }
        let pending = self
            .db
            .query_row(
                &format!(
                    "SELECT {COLUMNS} FROM mutation_intents WHERE file_id=?1 AND state='prepared'"
                ),
                [file_id],
                row,
            )
            .optional()?;
        if let Some(pending) = pending {
            let observed = self.observe_mutation(ws, &pending.operation_id)?;
            if observed.state == "prepared" {
                if observed.kind != kind
                    || observed.expected != expected
                    || observed.destination != destination
                {
                    return Err(HostError::new("PENDING_MUTATION_REQUIRES_REVIEW"));
                }
                return Ok(observed);
            }
        }
        self.require_clean_file(file_id)?;
        let entry = ws
            .tree()?
            .into_iter()
            .find(|e| e.file_id == file_id && !e.deleted && !e.is_folder)
            .ok_or_else(|| HostError::new("FILE_NOT_FOUND"))?;
        Self::validate_path(ws, &entry.path)?;
        if entry.hash != expected {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        if kind == "rename" {
            Self::validate_path(ws, destination)?;
            super::same_document_type(&entry.path, destination)?;
        }
        let id = Uuid::new_v4().to_string();
        self.db.execute(
            "INSERT INTO mutation_intents VALUES (?1,?2,?3,?4,?5,?6,'prepared',NULL)",
            params![id, kind, file_id, entry.path, destination, expected],
        )?;
        self.mutation(&id)
    }

    /// A missing receipt is only reported; this method never executes a mutation.
    pub fn observe_mutation(&mut self, ws: &Workspace, id: &str) -> Result<MutationIntent> {
        let intent = self.mutation(id)?;
        if intent.state == "committed" {
            return Ok(intent);
        }
        if let Some(receipt) = ws.operation(id)? {
            if receipt["state"] == "committed" {
                let entry: Entry = serde_json::from_value(receipt["result"].clone())
                    .map_err(|_| HostError::new("DATABASE_ERROR"))?;
                let path = if intent.kind == "rename" {
                    &intent.destination
                } else {
                    &intent.path
                };
                if entry.file_id != intent.file_id
                    || &entry.path != path
                    || entry.hash != intent.expected
                    || entry.deleted != (intent.kind == "delete")
                {
                    return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
                }
                let tx = self.db.transaction()?;
                tx.execute("UPDATE mutation_intents SET state='committed',error=NULL WHERE operation_id=?1",[id])?;
                if intent.kind == "rename" {
                    tx.execute(
                        "UPDATE drafts SET path=?1 WHERE file_id=?2 AND path=?3",
                        params![entry.path, entry.file_id, intent.path],
                    )?;
                } else {
                    // A late delete acknowledgement must retain later persisted typing.
                    tx.execute(
                        "DELETE FROM drafts WHERE file_id=?1 AND generation=saved_generation",
                        [entry.file_id],
                    )?;
                }
                tx.commit()?;
            }
        }
        self.mutation(id)
    }

    pub fn apply_mutation(&mut self, ws: &mut Workspace, id: &str) -> Result<MutationIntent> {
        let intent = self.observe_mutation(ws, id)?;
        if intent.state == "committed" {
            return Ok(intent);
        }
        if intent.state != "prepared" {
            return Err(HostError::new("MUTATION_REQUIRES_REVIEW"));
        }
        if ws.operation(id)?.is_none() {
            self.require_clean_file(&intent.file_id)?;
            ws.tree()?;
            if ws.path_for_id(&intent.file_id)? != intent.path {
                return Err(HostError::new("FILE_IDENTITY_CHANGED"));
            }
        }
        // An existing durable operation must recover through Workspace's original fingerprint,
        // even when its already-moved path no longer matches a fresh first-attempt preview.
        match ws.mutate_operation(
            &intent.kind,
            &intent.path,
            &intent.destination,
            &intent.expected,
            id,
        ) {
            Ok(_) => self.observe_mutation(ws, id),
            Err(error) => {
                let observed = self.observe_mutation(ws, id)?;
                if observed.state == "committed" {
                    return Ok(observed);
                }
                let known = matches!(
                    error.code.as_str(),
                    "REVISION_CONFLICT" | "INVALID_OPERATION" | "UNSAFE_PATH"
                ) && ws.operation(id)?.is_none();
                self.db.execute(
                    "UPDATE mutation_intents SET error=?1,state=?2 WHERE operation_id=?3",
                    params![error.code, if known { "rejected" } else { "prepared" }, id],
                )?;
                Err(error)
            }
        }
    }

    pub fn cancel_mutation(&mut self, ws: &Workspace, id: &str) -> Result<MutationIntent> {
        let observed = self.observe_mutation(ws, id)?;
        if observed.state == "committed" {
            return Ok(observed);
        }
        if ws.operation(id)?.is_some() {
            return Err(HostError::new("OPERATION_RECOVERY_REQUIRED"));
        }
        self.db.execute(
            "UPDATE mutation_intents SET state='cancelled' WHERE operation_id=?1",
            [id],
        )?;
        self.mutation(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn original_pending_rename_recovers_after_the_file_moved_before_receipt_commit() {
        for dirty in [false, true] {
            let temp = tempfile::tempdir().unwrap();
            let root = temp.path().join("vault");
            std::fs::create_dir(&root).unwrap();
            let mut ws = Workspace::open(&root).unwrap();
            let entry = ws.write("a.md", "", b"original", "local").unwrap();
            let mut store = DraftStore::open(temp.path()).unwrap();
            let editing = if dirty {
                Some(store.open_note(&mut ws, &entry.file_id).unwrap())
            } else {
                None
            };
            let intent = store
                .prepare_mutation(&mut ws, "rename", &entry.file_id, &entry.hash, "b.md")
                .unwrap();
            ws.mutate_operation("rename", "a.md", "b.md", &entry.hash, &intent.operation_id)
                .unwrap();
            // Reconstruct the durable pre-commit database boundary after the real file move.
            // This is a native fault-state test, not an actual Android process-kill claim.
            let tx = ws.db.transaction().unwrap();
            tx.execute(
                "UPDATE operations SET state='pending',result=NULL WHERE operation_id=?1",
                [&intent.operation_id],
            )
            .unwrap();
            tx.execute(
                "DELETE FROM outbox WHERE operation_id=?1",
                [&intent.operation_id],
            )
            .unwrap();
            tx.execute(
                "UPDATE files SET path='a.md',revision=?1 WHERE id=?2",
                params![entry.revision, entry.file_id],
            )
            .unwrap();
            tx.execute("INSERT INTO file_ops(id,kind,path,destination,hash,content,state,origin) VALUES (?1,'rename','a.md','b.md',?2,?3,'pending','local')",params![intent.operation_id,entry.hash,b"original".as_slice()]).unwrap();
            tx.commit().unwrap();
            if let Some(draft) = &editing {
                store
                    .persist(&draft.id, &draft.edit_session, 1, "later typing")
                    .unwrap();
            }
            assert_eq!(
                store
                    .observe_mutation(&ws, &intent.operation_id)
                    .unwrap()
                    .state,
                "prepared"
            );
            assert_eq!(
                store
                    .apply_mutation(&mut ws, &intent.operation_id)
                    .unwrap()
                    .state,
                "committed"
            );
            assert_eq!(std::fs::read(root.join("b.md")).unwrap(), b"original");
            assert_eq!(ws.read("b.md").unwrap().entry.file_id, entry.file_id);
            let count: i64 = ws
                .db
                .query_row(
                    "SELECT COUNT(*) FROM outbox WHERE operation_id=?1",
                    [&intent.operation_id],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(count, 1);
            if let Some(draft) = editing {
                assert_eq!(store.get(&draft.id).unwrap().content, "later typing");
            }
        }
    }

    #[test]
    fn rename_and_delete_keep_identity_and_never_execute_on_query() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let entry = ws.write("a.md", "", b"original\r\n", "local").unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let rename = store
            .prepare_mutation(&mut ws, "rename", &entry.file_id, &entry.hash, "中文/b.md")
            .unwrap();
        assert!(ws.operation(&rename.operation_id).unwrap().is_none());
        assert_eq!(
            store
                .observe_mutation(&ws, &rename.operation_id)
                .unwrap()
                .state,
            "prepared"
        );
        assert!(root.join("a.md").exists());
        store.apply_mutation(&mut ws, &rename.operation_id).unwrap();
        assert_eq!(
            store
                .apply_mutation(&mut ws, &rename.operation_id)
                .unwrap()
                .operation_id,
            rename.operation_id
        );
        assert_eq!(ws.read("中文/b.md").unwrap().entry.file_id, entry.file_id);
        assert_eq!(
            std::fs::read(root.join("中文/b.md")).unwrap(),
            b"original\r\n"
        );
        let delete = store
            .prepare_mutation(&mut ws, "delete", &entry.file_id, &entry.hash, "")
            .unwrap();
        assert!(root.join("中文/b.md").exists());
        store.apply_mutation(&mut ws, &delete.operation_id).unwrap();
        assert!(!root.join("中文/b.md").exists());
        assert_eq!(
            ws.operation(&delete.operation_id).unwrap().unwrap()["result"]["deleted"],
            true
        );
    }

    #[test]
    fn dirty_text_blocks_freezing_and_later_typing_blocks_execution() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let entry = ws.write("a.md", "", b"base", "local").unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.open_note(&mut ws, &entry.file_id).unwrap();
        let frozen = store
            .prepare_mutation(&mut ws, "delete", &entry.file_id, &entry.hash, "")
            .unwrap();
        store
            .persist(&draft.id, &draft.edit_session, 1, "later typing")
            .unwrap();
        assert_eq!(
            store
                .apply_mutation(&mut ws, &frozen.operation_id)
                .unwrap_err()
                .code,
            "DRAFT_REQUIRES_SAVE"
        );
        store.cancel_mutation(&ws, &frozen.operation_id).unwrap();
        assert_eq!(
            store
                .prepare_mutation(&mut ws, "rename", &entry.file_id, &entry.hash, "b.md")
                .unwrap_err()
                .code,
            "DRAFT_REQUIRES_SAVE"
        );
        assert_eq!(ws.read("a.md").unwrap().content, "base");
        assert_eq!(store.get(&draft.id).unwrap().content, "later typing");
    }

    #[test]
    fn concurrent_change_rejects_frozen_delete_without_a_receipt() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let entry = ws.write("a.md", "", b"base", "local").unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let frozen = store
            .prepare_mutation(&mut ws, "delete", &entry.file_id, &entry.hash, "")
            .unwrap();
        ws.write("a.md", &entry.hash, b"independent", "local")
            .unwrap();
        assert_eq!(
            store
                .apply_mutation(&mut ws, &frozen.operation_id)
                .unwrap_err()
                .code,
            "REVISION_CONFLICT"
        );
        assert!(ws.operation(&frozen.operation_id).unwrap().is_none());
        assert_eq!(ws.read("a.md").unwrap().content, "independent");
    }

    #[test]
    fn lost_rename_receipt_retains_later_acknowledged_draft() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let entry = ws.write("a.md", "", b"base", "local").unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.open_note(&mut ws, &entry.file_id).unwrap();
        let frozen = store
            .prepare_mutation(&mut ws, "rename", &entry.file_id, &entry.hash, "b.md")
            .unwrap();
        ws.mutate_operation("rename", "a.md", "b.md", &entry.hash, &frozen.operation_id)
            .unwrap();
        store
            .persist(&draft.id, &draft.edit_session, 1, "later typing")
            .unwrap();
        drop(store);
        drop(ws);
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        assert_eq!(
            store
                .observe_mutation(&ws, &frozen.operation_id)
                .unwrap()
                .state,
            "committed"
        );
        let recovered = store.get(&draft.id).unwrap();
        assert_eq!(recovered.path, "b.md");
        assert_eq!(recovered.content, "later typing");
        assert_eq!(recovered.saved_generation, 0);
        assert_eq!(recovered.generation, 1);
    }

    #[test]
    fn cancelled_original_cannot_later_delete_the_note() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let entry = ws.write("a.md", "", b"base", "local").unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let intent = store
            .prepare_mutation(&mut ws, "delete", &entry.file_id, &entry.hash, "")
            .unwrap();
        assert_eq!(
            store
                .cancel_mutation(&ws, &intent.operation_id)
                .unwrap()
                .state,
            "cancelled"
        );
        assert_eq!(
            store
                .apply_mutation(&mut ws, &intent.operation_id)
                .unwrap_err()
                .code,
            "MUTATION_REQUIRES_REVIEW"
        );
        assert!(ws.operation(&intent.operation_id).unwrap().is_none());
        assert_eq!(ws.read("a.md").unwrap().content, "base");
    }
}

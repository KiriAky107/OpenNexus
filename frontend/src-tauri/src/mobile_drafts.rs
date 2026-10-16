//! Mobile-private drafts and immutable save intents. Note bytes still belong to Workspace.
use crate::workspace::{hash, Document, Entry, HostError, Result, Workspace};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::path::Path;
use uuid::Uuid;

pub const MAX_NOTE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Draft {
    pub id: String,
    pub file_id: Option<String>,
    pub path: String,
    pub base_hash: String,
    pub generation: i64,
    pub saved_generation: i64,
    pub content: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct SaveIntent {
    pub operation_id: String,
    pub draft_id: String,
    pub generation: i64,
    pub file_id: Option<String>,
    pub path: String,
    pub expected: String,
    pub content: String,
    pub state: String,
    pub error: Option<String>,
}

pub struct DraftStore {
    db: Connection,
}

#[derive(Debug, Serialize)]
pub struct DraftSummary {
    pub id: String,
    pub file_id: Option<String>,
    pub path: String,
    pub generation: i64,
    pub saved_generation: i64,
    pub bytes: i64,
}

#[derive(Debug, Serialize)]
pub struct DraftPage {
    pub items: Vec<DraftSummary>,
    pub total: i64,
}

fn draft_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Draft> {
    Ok(Draft {
        id: row.get(0)?,
        file_id: row.get(1)?,
        path: row.get(2)?,
        base_hash: row.get(3)?,
        generation: row.get(4)?,
        saved_generation: row.get(5)?,
        content: row.get(6)?,
    })
}

fn intent_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<SaveIntent> {
    Ok(SaveIntent {
        operation_id: row.get(0)?,
        draft_id: row.get(1)?,
        generation: row.get(2)?,
        file_id: row.get(3)?,
        path: row.get(4)?,
        expected: row.get(5)?,
        content: row.get(6)?,
        state: row.get(7)?,
        error: row.get(8)?,
    })
}

const DRAFT_COLUMNS: &str = "id,file_id,path,base_hash,generation,saved_generation,content";
const INTENT_COLUMNS: &str =
    "operation_id,draft_id,generation,file_id,path,expected,content,state,error";

impl DraftStore {
    /// The caller supplies the native app data directory, never an IPC-controlled path.
    pub fn open(private_root: &Path) -> Result<Self> {
        std::fs::create_dir_all(private_root)?;
        let path = private_root.join("mobile-drafts.sqlite3");
        if path.exists() && std::fs::symlink_metadata(&path)?.file_type().is_symlink() {
            return Err(HostError::new("UNSAFE_PATH"));
        }
        let db = Connection::open(path)?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")?;
        let version: i64 = db.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version > 1 {
            return Err(HostError::new("SCHEMA_INCOMPATIBLE"));
        }
        db.execute_batch(
            "BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY,file_id TEXT UNIQUE,path TEXT NOT NULL UNIQUE,
                base_hash TEXT NOT NULL,generation INTEGER NOT NULL,
                saved_generation INTEGER NOT NULL,content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS save_intents (
                operation_id TEXT PRIMARY KEY,draft_id TEXT NOT NULL,generation INTEGER NOT NULL,
                file_id TEXT,path TEXT NOT NULL,expected TEXT NOT NULL,content TEXT NOT NULL,
                state TEXT NOT NULL,error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS active_draft_save
                ON save_intents(draft_id) WHERE state='prepared';
            PRAGMA user_version=1; COMMIT;",
        )?;
        Ok(Self { db })
    }

    fn validate_path(ws: &Workspace, path: &str) -> Result<()> {
        if path.len() > 1024 || !path.to_ascii_lowercase().ends_with(".md") {
            return Err(HostError::new("MOBILE_NOTE_PATH_INVALID"));
        }
        ws.resolve(path)?;
        Ok(())
    }

    pub fn get(&self, id: &str) -> Result<Draft> {
        self.db
            .query_row(
                &format!("SELECT {DRAFT_COLUMNS} FROM drafts WHERE id=?1"),
                [id],
                draft_row,
            )
            .optional()?
            .ok_or_else(|| HostError::new("DRAFT_NOT_FOUND"))
    }

    pub fn list(&self, offset: usize, limit: usize) -> Result<DraftPage> {
        if !(1..=100).contains(&limit) || offset > i64::MAX as usize {
            return Err(HostError::new("MOBILE_PAGE_INVALID"));
        }
        let total = self
            .db
            .query_row("SELECT COUNT(*) FROM drafts", [], |row| row.get(0))?;
        let mut query = self.db.prepare("SELECT id,file_id,path,generation,saved_generation,length(CAST(content AS BLOB)) FROM drafts ORDER BY path,id LIMIT ?1 OFFSET ?2")?;
        let items = query
            .query_map(params![limit as i64, offset as i64], |row| {
                Ok(DraftSummary {
                    id: row.get(0)?,
                    file_id: row.get(1)?,
                    path: row.get(2)?,
                    generation: row.get(3)?,
                    saved_generation: row.get(4)?,
                    bytes: row.get(5)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(DraftPage { items, total })
    }

    pub fn create(&self, ws: &Workspace, path: &str) -> Result<Draft> {
        Self::validate_path(ws, path)?;
        if ws.resolve(path)?.exists() {
            return Err(HostError::new("FILE_ALREADY_EXISTS"));
        }
        let existing: bool = self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM drafts WHERE path=?1)",
            [path],
            |r| r.get(0),
        )?;
        if existing {
            return Err(HostError::new("DRAFT_PATH_IN_USE"));
        }
        let id = Uuid::new_v4().to_string();
        self.db.execute(
            "INSERT INTO drafts VALUES (?1,NULL,?2,'',1,0,'')",
            params![id, path],
        )?;
        self.get(&id)
    }

    pub fn open_note(&self, ws: &mut Workspace, file_id: &str) -> Result<Draft> {
        let existing = self
            .db
            .query_row(
                &format!("SELECT {DRAFT_COLUMNS} FROM drafts WHERE file_id=?1"),
                [file_id],
                draft_row,
            )
            .optional()?;
        // A dirty recovered draft is never replaced by a later disk read.
        if let Some(draft) = existing {
            return Ok(draft);
        }
        ws.tree()?;
        let path = ws.path_for_id(file_id)?;
        Self::validate_path(ws, &path)?;
        if std::fs::metadata(ws.resolve(&path)?)?.len() > MAX_NOTE_BYTES as u64 {
            return Err(HostError::new("CORE_NOTE_TOO_LARGE"));
        }
        let Document { entry, content } = ws.read(&path)?;
        let id = Uuid::new_v4().to_string();
        self.db.execute(
            "INSERT INTO drafts VALUES (?1,?2,?3,?4,0,0,?5)",
            params![id, entry.file_id, entry.path, entry.hash, content],
        )?;
        self.get(&id)
    }

    pub fn persist(&mut self, id: &str, generation: i64, content: &str) -> Result<Draft> {
        if generation < 1 || content.len() > MAX_NOTE_BYTES {
            return Err(HostError::new("MOBILE_DRAFT_INVALID"));
        }
        let tx = self.db.transaction()?;
        let old = tx
            .query_row(
                &format!("SELECT {DRAFT_COLUMNS} FROM drafts WHERE id=?1"),
                [id],
                draft_row,
            )
            .optional()?
            .ok_or_else(|| HostError::new("DRAFT_NOT_FOUND"))?;
        if generation < old.generation || (generation == old.generation && content != old.content) {
            return Err(HostError::new("DRAFT_GENERATION_CONFLICT"));
        }
        if generation > old.generation {
            tx.execute(
                "UPDATE drafts SET generation=?1,content=?2 WHERE id=?3",
                params![generation, content, id],
            )?;
        }
        tx.commit()?;
        self.get(id)
    }

    pub fn intent(&self, operation_id: &str) -> Result<SaveIntent> {
        self.db
            .query_row(
                &format!("SELECT {INTENT_COLUMNS} FROM save_intents WHERE operation_id=?1"),
                [operation_id],
                intent_row,
            )
            .optional()?
            .ok_or_else(|| HostError::new("OPERATION_NOT_FOUND"))
    }

    pub fn pending(&self, draft_id: &str) -> Result<Option<SaveIntent>> {
        Ok(self.db.query_row(&format!("SELECT {INTENT_COLUMNS} FROM save_intents WHERE draft_id=?1 AND state='prepared'"), [draft_id], intent_row).optional()?)
    }

    /// Freezes exact bytes before execution; it does not write a note or outbox entry.
    pub fn prepare_save(
        &mut self,
        ws: &Workspace,
        draft_id: &str,
        generation: i64,
    ) -> Result<SaveIntent> {
        if let Some(pending) = self.pending(draft_id)? {
            let observed = self.observe_save(ws, &pending.operation_id)?;
            if observed.state == "prepared" {
                if observed.generation != generation {
                    return Err(HostError::new("PENDING_SAVE_REQUIRES_REVIEW"));
                }
                return Ok(observed);
            }
        }
        let draft = self.get(draft_id)?;
        if draft.generation != generation {
            return Err(HostError::new("DRAFT_GENERATION_CONFLICT"));
        }
        // A duplicate click after an acknowledged commit returns its original receipt identity.
        if draft.generation == draft.saved_generation {
            return self.db.query_row(&format!("SELECT {INTENT_COLUMNS} FROM save_intents WHERE draft_id=?1 AND generation=?2 AND state='committed' ORDER BY rowid DESC LIMIT 1"), params![draft_id,generation], intent_row)
                .optional()?.ok_or_else(|| HostError::new("NO_LOCAL_CHANGES"));
        }
        Self::validate_path(ws, &draft.path)?;
        let operation_id = Uuid::new_v4().to_string();
        self.db.execute(
            "INSERT INTO save_intents VALUES (?1,?2,?3,?4,?5,?6,?7,'prepared',NULL)",
            params![
                operation_id,
                draft.id,
                draft.generation,
                draft.file_id,
                draft.path,
                draft.base_hash,
                draft.content
            ],
        )?;
        self.intent(&operation_id)
    }

    /// Reads the original Workspace receipt only. It never retries a missing operation.
    pub fn observe_save(&mut self, ws: &Workspace, operation_id: &str) -> Result<SaveIntent> {
        let intent = self.intent(operation_id)?;
        if intent.state == "committed" {
            return Ok(intent);
        }
        if let Some(receipt) = ws.operation(operation_id)? {
            if receipt["state"] == "committed" {
                let entry: Entry = serde_json::from_value(receipt["result"].clone())
                    .map_err(|_| HostError::new("DATABASE_ERROR"))?;
                if entry.hash != hash(intent.content.as_bytes())
                    || entry.path != intent.path
                    || entry.deleted
                    || intent
                        .file_id
                        .as_ref()
                        .is_some_and(|id| id != &entry.file_id)
                {
                    return Err(HostError::new("OPERATION_PAYLOAD_CONFLICT"));
                }
                let tx = self.db.transaction()?;
                tx.execute(
                    "UPDATE save_intents SET state='committed',error=NULL WHERE operation_id=?1",
                    [operation_id],
                )?;
                // A late receipt may acknowledge old text, but must retain later typing and later bases.
                tx.execute("UPDATE drafts SET file_id=?1,base_hash=?2,saved_generation=?3 WHERE id=?4 AND base_hash=?5 AND saved_generation<=?3",
                    params![entry.file_id,entry.hash,intent.generation,intent.draft_id,intent.expected])?;
                tx.commit()?;
            }
        }
        self.intent(operation_id)
    }

    /// Called only for an explicit original-content apply/retry after observing its state.
    pub fn apply_save(&mut self, ws: &mut Workspace, operation_id: &str) -> Result<SaveIntent> {
        let intent = self.observe_save(ws, operation_id)?;
        if intent.state == "committed" {
            return Ok(intent);
        }
        if intent.state != "prepared" {
            return Err(HostError::new("SAVE_REQUIRES_REVIEW"));
        }
        if let Some(file_id) = &intent.file_id {
            ws.tree()?;
            if ws.path_for_id(file_id)? != intent.path {
                return Err(HostError::new("FILE_IDENTITY_CHANGED"));
            }
        }
        match ws.write_operation(
            &intent.path,
            &intent.expected,
            intent.content.as_bytes(),
            "local",
            operation_id,
        ) {
            Ok(_) => self.observe_save(ws, operation_id),
            Err(error) => {
                // A durable Workspace receipt wins over a lost/failed local acknowledgement.
                let observed = self.observe_save(ws, operation_id)?;
                if observed.state == "committed" {
                    return Ok(observed);
                }
                let known_rejection = matches!(
                    error.code.as_str(),
                    "REVISION_CONFLICT" | "INVALID_OPERATION" | "UNSAFE_PATH"
                ) && ws.operation(operation_id)?.is_none();
                self.db.execute(
                    "UPDATE save_intents SET error=?1,state=?2 WHERE operation_id=?3",
                    params![
                        error.code,
                        if known_rejection {
                            "rejected"
                        } else {
                            "prepared"
                        },
                        operation_id
                    ],
                )?;
                Err(error)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovered_draft_pages_are_bounded_metadata_without_note_bodies() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let first = store.create(&ws, "a.md").unwrap();
        store.persist(&first.id, 2, "私有正文 😀").unwrap();
        let second = store.create(&ws, "b.md").unwrap();
        let page = store.list(0, 1).unwrap();
        assert_eq!(page.total, 2);
        assert_eq!(page.items[0].id, first.id);
        assert_eq!(page.items[0].bytes, "私有正文 😀".len() as i64);
        assert!(!serde_json::to_string(&page).unwrap().contains("私有正文"));
        assert_eq!(store.list(1, 1).unwrap().items[0].id, second.id);
        assert!(store.list(0, 101).is_err());
        assert!(store.list(0, 0).is_err());
    }

    #[test]
    fn acknowledged_draft_survives_reopen_and_old_generations_cannot_replace_it() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.create(&ws, "中文 😀.md").unwrap();
        let expected = store
            .persist(&draft.id, 2, "第一行\r\n# 标题 😀\n")
            .unwrap();
        assert_eq!(
            store.persist(&draft.id, 1, "late").unwrap_err().code,
            "DRAFT_GENERATION_CONFLICT"
        );
        assert_eq!(
            store
                .persist(&draft.id, 2, "changed same generation")
                .unwrap_err()
                .code,
            "DRAFT_GENERATION_CONFLICT"
        );
        drop(store);
        let store = DraftStore::open(temp.path()).unwrap();
        assert_eq!(store.get(&draft.id).unwrap(), expected);
        assert!(!root.join(&draft.path).exists());
    }

    #[test]
    fn lost_receipt_is_observed_without_replaying_and_later_typing_survives() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.create(&ws, "a.md").unwrap();
        store
            .persist(&draft.id, 2, "acknowledged bytes\r\n")
            .unwrap();
        let intent = store.prepare_save(&ws, &draft.id, 2).unwrap();
        assert!(ws.operation(&intent.operation_id).unwrap().is_none());
        assert_eq!(
            store.observe_save(&ws, &intent.operation_id).unwrap().state,
            "prepared"
        );
        // Actual Workspace commit followed by process loss before mobile acknowledgement.
        ws.write_operation(
            &intent.path,
            &intent.expected,
            intent.content.as_bytes(),
            "local",
            &intent.operation_id,
        )
        .unwrap();
        store
            .persist(&draft.id, 3, "newer typing must remain")
            .unwrap();
        drop(store);
        drop(ws);
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        assert_eq!(
            store.observe_save(&ws, &intent.operation_id).unwrap().state,
            "committed"
        );
        let recovered = store.get(&draft.id).unwrap();
        assert_eq!(recovered.content, "newer typing must remain");
        assert_eq!(recovered.generation, 3);
        assert_eq!(recovered.saved_generation, 2);
        assert_eq!(recovered.base_hash, hash(intent.content.as_bytes()));
        let count: i64 = ws
            .db
            .query_row("SELECT COUNT(*) FROM outbox", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 1);
        assert_eq!(
            std::fs::read(root.join("a.md")).unwrap(),
            intent.content.as_bytes()
        );
    }

    #[test]
    fn duplicate_save_uses_original_operation_and_old_receipts_cannot_roll_back_the_base() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.create(&ws, "a.md").unwrap();
        store.persist(&draft.id, 2, "one").unwrap();
        let first = store.prepare_save(&ws, &draft.id, 2).unwrap();
        store.apply_save(&mut ws, &first.operation_id).unwrap();
        assert_eq!(
            store.prepare_save(&ws, &draft.id, 2).unwrap().operation_id,
            first.operation_id
        );
        store.persist(&draft.id, 3, "two").unwrap();
        let second = store.prepare_save(&ws, &draft.id, 3).unwrap();
        store.apply_save(&mut ws, &second.operation_id).unwrap();
        store.observe_save(&ws, &first.operation_id).unwrap();
        assert_eq!(store.get(&draft.id).unwrap().base_hash, hash(b"two"));
        assert_eq!(ws.read("a.md").unwrap().content, "two");
        let count: i64 = ws
            .db
            .query_row("SELECT COUNT(*) FROM outbox", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 2);
    }

    #[test]
    fn independent_modification_is_rejected_and_original_and_draft_are_both_kept() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let mut ws = Workspace::open(&root).unwrap();
        let entry = ws.write("a.md", "", b"base", "local").unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.open_note(&mut ws, &entry.file_id).unwrap();
        store.persist(&draft.id, 1, "local draft").unwrap();
        let intent = store.prepare_save(&ws, &draft.id, 1).unwrap();
        ws.write("a.md", &entry.hash, b"independent", "local")
            .unwrap();
        assert_eq!(
            store
                .apply_save(&mut ws, &intent.operation_id)
                .unwrap_err()
                .code,
            "REVISION_CONFLICT"
        );
        assert_eq!(ws.read("a.md").unwrap().content, "independent");
        assert_eq!(store.get(&draft.id).unwrap().content, "local draft");
        assert_eq!(
            store.open_note(&mut ws, &entry.file_id).unwrap().content,
            "local draft"
        );
        assert!(ws.operation(&intent.operation_id).unwrap().is_none());
    }

    #[test]
    fn a_pending_original_cannot_be_replaced_by_newer_content_before_review() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.create(&ws, "a.md").unwrap();
        store.persist(&draft.id, 2, "one").unwrap();
        let intent = store.prepare_save(&ws, &draft.id, 2).unwrap();
        store.persist(&draft.id, 3, "two").unwrap();
        assert_eq!(
            store.prepare_save(&ws, &draft.id, 3).unwrap_err().code,
            "PENDING_SAVE_REQUIRES_REVIEW"
        );
        assert_eq!(store.intent(&intent.operation_id).unwrap().content, "one");
        assert!(ws.operation(&intent.operation_id).unwrap().is_none());
    }

    #[test]
    fn private_database_capacity_failure_keeps_previously_acknowledged_text() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        let draft = store.create(&ws, "a.md").unwrap();
        let old = store.persist(&draft.id, 2, "keep").unwrap();
        let pages: i64 = store
            .db
            .query_row("PRAGMA page_count", [], |row| row.get(0))
            .unwrap();
        store
            .db
            .execute_batch(&format!("PRAGMA max_page_count={pages};"))
            .unwrap();
        assert_eq!(
            store
                .persist(&draft.id, 3, &"large".repeat(100000))
                .unwrap_err()
                .code,
            "QUOTA_EXCEEDED"
        );
        assert_eq!(store.get(&draft.id).unwrap(), old);
        assert!(!root.join("a.md").exists());
    }

    #[test]
    fn note_paths_and_draft_sizes_remain_bounded_before_a_save_intent_exists() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("vault");
        std::fs::create_dir(&root).unwrap();
        let ws = Workspace::open(&root).unwrap();
        let mut store = DraftStore::open(temp.path()).unwrap();
        for path in [
            "../outside.md",
            "/absolute.md",
            ".ainote/internal.md",
            "a.png",
        ] {
            assert!(store.create(&ws, path).is_err(), "{path}");
        }
        let draft = store.create(&ws, "a.md").unwrap();
        assert_eq!(
            store
                .persist(&draft.id, 2, &"x".repeat(MAX_NOTE_BYTES + 1))
                .unwrap_err()
                .code,
            "MOBILE_DRAFT_INVALID"
        );
        assert!(store.pending(&draft.id).unwrap().is_none());
        assert!(!root.join("a.md").exists());
    }
}

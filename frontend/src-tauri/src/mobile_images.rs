//! Original image bytes and the exact draft insertion survive a lost IPC reply.
use super::*;
use base64::{engine::general_purpose::STANDARD, Engine};

const MAX_IMAGE_BYTES: usize = 5 * 1024 * 1024;
const MAX_ENCODED_BYTES: usize = MAX_IMAGE_BYTES.div_ceil(3) * 4;
const COLUMNS: &str = "operation_id,draft_id,path,generation,asset_path,reference,hash,size,content_before,content_after,state,error";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ImageIntent {
    pub operation_id: String,
    pub draft_id: String,
    pub path: String,
    pub generation: i64,
    pub asset_path: String,
    pub reference: String,
    pub hash: String,
    pub size: usize,
    pub content_before: String,
    pub content_after: String,
    pub state: String,
    pub error: Option<String>,
}

pub(super) fn initialize(db: &Connection) -> Result<()> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS image_intents (
        operation_id TEXT PRIMARY KEY,draft_id TEXT NOT NULL,path TEXT NOT NULL,generation INTEGER NOT NULL,
        asset_path TEXT NOT NULL,reference TEXT NOT NULL,hash TEXT NOT NULL,size INTEGER NOT NULL,
        content_before TEXT NOT NULL,content_after TEXT NOT NULL,state TEXT NOT NULL,error TEXT,bytes BLOB NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS active_draft_image ON image_intents(draft_id) WHERE state='prepared';")?;
    Ok(())
}

fn row(r: &rusqlite::Row<'_>) -> rusqlite::Result<ImageIntent> {
    Ok(ImageIntent {
        operation_id: r.get(0)?,
        draft_id: r.get(1)?,
        path: r.get(2)?,
        generation: r.get(3)?,
        asset_path: r.get(4)?,
        reference: r.get(5)?,
        hash: r.get(6)?,
        size: r.get(7)?,
        content_before: r.get(8)?,
        content_after: r.get(9)?,
        state: r.get(10)?,
        error: r.get(11)?,
    })
}

// Browser editor positions count UTF-16 units. Never split an emoji's surrogate pair.
fn byte_position(text: &str, utf16: usize) -> Result<usize> {
    let mut position = 0;
    for (byte, c) in text.char_indices() {
        if position == utf16 {
            return Ok(byte);
        }
        position += c.len_utf16();
        if position > utf16 {
            return Err(HostError::new("MOBILE_SELECTION_INVALID"));
        }
    }
    if position == utf16 {
        Ok(text.len())
    } else {
        Err(HostError::new("MOBILE_SELECTION_INVALID"))
    }
}

fn image_extension(bytes: &[u8]) -> Result<&'static str> {
    if bytes.is_empty() || bytes.len() > MAX_IMAGE_BYTES {
        return Err(HostError::new("MOBILE_IMAGE_TOO_LARGE"));
    }
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        Ok("png")
    } else if bytes.starts_with(b"\xff\xd8\xff") {
        Ok("jpg")
    } else if bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a") {
        Ok("gif")
    } else if bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        Ok("webp")
    } else {
        Err(HostError::new("WORKSPACE_IMAGE_UNSUPPORTED"))
    }
}

impl DraftStore {
    pub fn image(&self, operation_id: &str) -> Result<ImageIntent> {
        self.db
            .query_row(
                &format!("SELECT {COLUMNS} FROM image_intents WHERE operation_id=?1"),
                [operation_id],
                row,
            )
            .optional()?
            .ok_or_else(|| HostError::new("OPERATION_NOT_FOUND"))
    }

    pub fn pending_image(&self, draft_id: &str) -> Result<Option<ImageIntent>> {
        Ok(self
            .db
            .query_row(
                &format!(
                    "SELECT {COLUMNS} FROM image_intents WHERE draft_id=?1 AND state='prepared'"
                ),
                [draft_id],
                row,
            )
            .optional()?)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn prepare_image(
        &self,
        ws: &Workspace,
        draft_id: &str,
        edit_session: &str,
        generation: i64,
        from: usize,
        to: usize,
        content_base64: &str,
    ) -> Result<ImageIntent> {
        if content_base64.len() > MAX_ENCODED_BYTES {
            return Err(HostError::new("MOBILE_IMAGE_TOO_LARGE"));
        }
        let bytes = STANDARD
            .decode(content_base64)
            .map_err(|_| HostError::new("WORKSPACE_IMAGE_UNSUPPORTED"))?;
        let extension = image_extension(&bytes)?;
        let draft = self.get(draft_id)?;
        if draft.edit_session != edit_session || Uuid::parse_str(edit_session).is_err() {
            return Err(HostError::new("DRAFT_SESSION_CHANGED"));
        }
        if draft.generation != generation {
            return Err(HostError::new("DRAFT_GENERATION_CONFLICT"));
        }
        if self.pending(draft_id)?.is_some() {
            return Err(HostError::new("PENDING_SAVE_REQUIRES_REVIEW"));
        }
        if draft
            .file_id
            .as_deref()
            .map(|id| self.pending_mutation(id))
            .transpose()?
            .flatten()
            .is_some()
        {
            return Err(HostError::new("PENDING_MUTATION_REQUIRES_REVIEW"));
        }
        Self::validate_path(ws, &draft.path)?;
        let hash = hash(&bytes);
        let asset_path = format!("attachments/{}/{}.{}", &hash[..2], hash, extension);
        let reference = format!(
            "{}{}",
            "../".repeat(draft.path.split('/').count() - 1),
            asset_path
        );
        let (start, end) = (
            byte_position(&draft.content, from)?,
            byte_position(&draft.content, to)?,
        );
        if start > end {
            return Err(HostError::new("MOBILE_SELECTION_INVALID"));
        }
        let after = format!(
            "{}![]({}){}",
            &draft.content[..start],
            reference,
            &draft.content[end..]
        );
        if after.len() > MAX_NOTE_BYTES || generation == i64::MAX {
            return Err(HostError::new("MOBILE_DRAFT_INVALID"));
        }
        if let Some(pending) = self.pending_image(draft_id)? {
            if pending.generation == generation
                && pending.hash == hash
                && pending.content_after == after
            {
                return Ok(pending);
            }
            return Err(HostError::new("PENDING_IMAGE_REQUIRES_REVIEW"));
        }
        let id = Uuid::new_v4().to_string();
        self.db.execute(
            "INSERT INTO image_intents VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,'prepared',NULL,?11)",
            params![
                id,
                draft.id,
                draft.path,
                generation,
                asset_path,
                reference,
                hash,
                bytes.len(),
                draft.content,
                after,
                bytes
            ],
        )?;
        self.image(&id)
    }

    /// Reading an image intent never writes the object or inserts its Markdown link.
    pub fn observe_image(&self, operation_id: &str) -> Result<ImageIntent> {
        self.image(operation_id)
    }

    pub fn apply_image(&mut self, ws: &mut Workspace, operation_id: &str) -> Result<ImageIntent> {
        let intent = self.image(operation_id)?;
        if intent.state == "committed" {
            return Ok(intent);
        }
        if intent.state != "prepared" {
            return Err(HostError::new("INVALID_OPERATION"));
        }
        let draft = self.get(&intent.draft_id)?;
        // A new view may recover the original intent, but later text must never be replaced.
        if draft.generation != intent.generation
            || draft.content != intent.content_before
            || draft.path != intent.path
        {
            return Err(HostError::new("DRAFT_GENERATION_CONFLICT"));
        }
        let bytes: Vec<u8> = self.db.query_row(
            "SELECT bytes FROM image_intents WHERE operation_id=?1",
            [operation_id],
            |r| r.get(0),
        )?;
        if hash(&bytes) != intent.hash {
            return Err(HostError::new("DATABASE_ERROR"));
        }
        if ws.operation(operation_id)?.is_some() {
            ws.write_operation(&intent.asset_path, "", &bytes, "local", operation_id)?;
        } else {
            crate::workspace_broker::dispatch(ws,&serde_json::json!({"rpc":"workspace.assets.write","params":{
                "vault_id":ws.vault_id,"path":intent.asset_path,"content_base64":STANDARD.encode(&bytes),"operation_id":operation_id
            }})).map_err(|code|HostError::new(&code))?;
        }
        let actual = std::fs::read(ws.resolve(&intent.asset_path)?)?;
        if actual != bytes {
            return Err(HostError::new("REVISION_CONFLICT"));
        }
        let tx = self.db.transaction()?;
        tx.execute(
            "UPDATE drafts SET content=?1,generation=generation+1 WHERE id=?2",
            params![intent.content_after, intent.draft_id],
        )?;
        tx.execute(
            "UPDATE image_intents SET state='committed',error=NULL,bytes=X'' WHERE operation_id=?1",
            [operation_id],
        )?;
        tx.commit()?;
        self.image(operation_id)
    }

    pub fn cancel_image(&self, operation_id: &str) -> Result<ImageIntent> {
        let intent = self.image(operation_id)?;
        if intent.state == "prepared" {
            // An already imported object stays in the Vault/outbox; cancellation removes no user data.
            self.db.execute(
                "UPDATE image_intents SET state='cancelled',bytes=X'' WHERE operation_id=?1",
                [operation_id],
            )?;
        }
        self.image(operation_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const PNG: &[u8] = b"\x89PNG\r\n\x1a\noriginal-test-pixel-bytes";
    fn workspace(root: &Path) -> Workspace {
        std::fs::create_dir_all(root.join("vault")).unwrap();
        Workspace::open(&root.join("vault")).unwrap()
    }

    #[test]
    fn image_original_intent_preserves_bytes_and_relative_reference_after_reopen() {
        let temp = tempfile::tempdir().unwrap();
        let mut ws = workspace(temp.path());
        let (draft_id, id, path, after) = {
            let store = DraftStore::open(temp.path()).unwrap();
            let draft = store
                .create_with_content(&ws, "中文/重复.md", "# 重复\r\n😀 end\r\n")
                .unwrap();
            let prepared = store
                .prepare_image(
                    &ws,
                    &draft.id,
                    &draft.edit_session,
                    1,
                    8,
                    8,
                    &STANDARD.encode(PNG),
                )
                .unwrap();
            assert!(prepared.reference.starts_with("../attachments/"));
            assert_eq!(
                store
                    .prepare_image(
                        &ws,
                        &draft.id,
                        &draft.edit_session,
                        1,
                        8,
                        8,
                        &STANDARD.encode(PNG)
                    )
                    .unwrap(),
                prepared
            );
            assert_eq!(
                store.observe_image(&prepared.operation_id).unwrap().state,
                "prepared"
            );
            assert!(!ws.resolve(&prepared.asset_path).unwrap().exists());
            (
                draft.id,
                prepared.operation_id,
                prepared.asset_path,
                prepared.content_after,
            )
        };
        let mut store = DraftStore::open(temp.path()).unwrap();
        store.checkout(&draft_id).unwrap();
        let result = store.apply_image(&mut ws, &id).unwrap();
        assert_eq!(result.state, "committed");
        assert_eq!(std::fs::read(ws.resolve(&path).unwrap()).unwrap(), PNG);
        let draft = store.get(&draft_id).unwrap();
        assert_eq!(draft.generation, 2);
        assert_eq!(draft.saved_generation, 0);
        assert_eq!(draft.content, after);
        store.apply_image(&mut ws, &id).unwrap();
        assert_eq!(store.get(&draft_id).unwrap().generation, 2);
        let save = store
            .prepare_save(&ws, &draft_id, &draft.edit_session, 2)
            .unwrap();
        store.apply_save(&mut ws, &save.operation_id).unwrap();
        assert_eq!(ws.read("中文/重复.md").unwrap().content, after);
    }

    #[test]
    fn image_recovery_after_object_commit_cannot_duplicate_or_overwrite_later_text() {
        let temp = tempfile::tempdir().unwrap();
        let mut ws = workspace(temp.path());
        let mut store = DraftStore::open(temp.path()).unwrap();
        let d = store.create_with_content(&ws, "a.md", "original").unwrap();
        let intent = store
            .prepare_image(&ws, &d.id, &d.edit_session, 1, 8, 8, &STANDARD.encode(PNG))
            .unwrap();
        ws.write_operation(&intent.asset_path, "", PNG, "local", &intent.operation_id)
            .unwrap();
        assert_eq!(
            store.observe_image(&intent.operation_id).unwrap().state,
            "prepared"
        );
        assert_eq!(store.get(&d.id).unwrap().content, "original");
        store
            .persist(&d.id, &d.edit_session, 2, "later typing")
            .unwrap();
        assert_eq!(
            store
                .apply_image(&mut ws, &intent.operation_id)
                .unwrap_err()
                .code,
            "DRAFT_GENERATION_CONFLICT"
        );
        assert_eq!(store.get(&d.id).unwrap().content, "later typing");
        store.cancel_image(&intent.operation_id).unwrap();
        assert_eq!(
            std::fs::read(ws.resolve(&intent.asset_path).unwrap()).unwrap(),
            PNG
        );
        let next = store
            .prepare_image(
                &ws,
                &d.id,
                &d.edit_session,
                2,
                12,
                12,
                &STANDARD.encode(PNG),
            )
            .unwrap();
        store.apply_image(&mut ws, &next.operation_id).unwrap();
        assert_eq!(store.get(&d.id).unwrap().generation, 3);
        assert_eq!(
            ws.tree()
                .unwrap()
                .iter()
                .filter(|e| !e.is_folder && e.path.starts_with("attachments/"))
                .count(),
            1
        );
    }

    #[test]
    fn image_object_commit_before_draft_ack_recovers_original_once() {
        let temp = tempfile::tempdir().unwrap();
        let mut ws = workspace(temp.path());
        let (d, id) = {
            let mut store = DraftStore::open(temp.path()).unwrap();
            let d = store.create_with_content(&ws, "a.md", "before").unwrap();
            let image = store
                .prepare_image(&ws, &d.id, &d.edit_session, 1, 6, 6, &STANDARD.encode(PNG))
                .unwrap();
            assert_eq!(
                store
                    .prepare_save(&ws, &d.id, &d.edit_session, 1)
                    .unwrap_err()
                    .code,
                "PENDING_IMAGE_REQUIRES_REVIEW"
            );
            let page = store.pending_operations(0, 20).unwrap();
            assert_eq!(page["total"], 1);
            assert_eq!(page["items"][0]["kind"], "image");
            assert!(!page.to_string().contains("before"));
            ws.write_operation(&image.asset_path, "", PNG, "local", &image.operation_id)
                .unwrap();
            (d, image.operation_id)
        };
        let mut store = DraftStore::open(temp.path()).unwrap();
        store.checkout(&d.id).unwrap();
        assert_eq!(store.observe_image(&id).unwrap().state, "prepared");
        assert_eq!(store.get(&d.id).unwrap().content, "before");
        store.apply_image(&mut ws, &id).unwrap();
        let first = store.get(&d.id).unwrap();
        assert!(first.content.starts_with("before![]("));
        store.apply_image(&mut ws, &id).unwrap();
        assert_eq!(first, store.get(&d.id).unwrap());
        assert_eq!(store.pending_operations(0, 20).unwrap()["total"], 0);
    }

    #[test]
    fn image_preparation_rejects_oversize_svg_invalid_selection_and_old_lease() {
        let temp = tempfile::tempdir().unwrap();
        let ws = workspace(temp.path());
        let store = DraftStore::open(temp.path()).unwrap();
        let d = store.create_with_content(&ws, "a.md", "😀").unwrap();
        assert_eq!(
            store
                .prepare_image(&ws, &d.id, &d.edit_session, 1, 1, 1, &STANDARD.encode(PNG))
                .unwrap_err()
                .code,
            "MOBILE_SELECTION_INVALID"
        );
        assert_eq!(
            store
                .prepare_image(
                    &ws,
                    &d.id,
                    &d.edit_session,
                    1,
                    0,
                    0,
                    &STANDARD.encode(b"<svg onload='bad()'/>")
                )
                .unwrap_err()
                .code,
            "WORKSPACE_IMAGE_UNSUPPORTED"
        );
        assert_eq!(
            store
                .prepare_image(
                    &ws,
                    &d.id,
                    &d.edit_session,
                    1,
                    0,
                    0,
                    &"A".repeat(MAX_ENCODED_BYTES + 1)
                )
                .unwrap_err()
                .code,
            "MOBILE_IMAGE_TOO_LARGE"
        );
        store.checkout(&d.id).unwrap();
        assert_eq!(
            store
                .prepare_image(&ws, &d.id, &d.edit_session, 1, 0, 0, &STANDARD.encode(PNG))
                .unwrap_err()
                .code,
            "DRAFT_SESSION_CHANGED"
        );
        assert_eq!(
            store
                .db
                .query_row("SELECT COUNT(*) FROM image_intents", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
    }
}

//! Narrow mobile API over the native-owned Vault. No desktop RPC or caller-supplied root.
use crate::mobile_drafts::{
    Draft, DraftStore, ImageIntent, MutationIntent, SaveIntent, MAX_NOTE_BYTES,
};
use crate::workspace::{Document, HostError, Result, Workspace};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::Path;

pub type MobileResponse = Value;

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum MobileRequest {
    ListNotes {
        offset: usize,
        limit: usize,
        query: String,
        directory: String,
    },
    ListDrafts {
        offset: usize,
        limit: usize,
    },
    ListPending {
        offset: usize,
        limit: usize,
    },
    OpenNote {
        file_id: String,
    },
    OpenDraft {
        draft_id: String,
    },
    DraftStatus {
        draft_id: String,
    },
    ReadNote {
        file_id: String,
    },
    CreateDraft {
        path: String,
        content: Option<String>,
    },
    PersistDraft {
        draft_id: String,
        edit_session: String,
        generation: i64,
        content: String,
    },
    PrepareSave {
        draft_id: String,
        edit_session: String,
        generation: i64,
    },
    ObserveSave {
        operation_id: String,
    },
    ApplySave {
        operation_id: String,
    },
    CancelSave {
        operation_id: String,
    },
    CopyDraft {
        draft_id: String,
        edit_session: String,
        path: String,
    },
    DiscardDraft {
        draft_id: String,
        edit_session: String,
    },
    PrepareMutation {
        action: String,
        file_id: String,
        expected: String,
        destination: String,
    },
    ObserveMutation {
        operation_id: String,
    },
    ApplyMutation {
        operation_id: String,
    },
    CancelMutation {
        operation_id: String,
    },
    PrepareImage {
        draft_id: String,
        edit_session: String,
        generation: i64,
        from: usize,
        to: usize,
        content_base64: String,
    },
    ObserveImage {
        operation_id: String,
    },
    ApplyImage {
        operation_id: String,
    },
    CancelImage {
        operation_id: String,
    },
    ReadImage {
        path: String,
    },
}

#[derive(Serialize)]
pub struct DraftView {
    pub draft: Draft,
    pub current: Option<Document>,
    pub current_error: Option<String>,
    pub pending_save: Option<SaveIntent>,
    pub pending_mutation: Option<MutationIntent>,
    pub pending_image: Option<ImageIntent>,
}

pub struct MobileWorkspace {
    pub workspace: Workspace,
    pub drafts: DraftStore,
}

fn value<T: Serialize>(value: T) -> Result<Value> {
    serde_json::to_value(value).map_err(|_| HostError::new("HOST_SERIALIZE_FAILED"))
}

impl MobileWorkspace {
    pub fn open(private_root: &Path) -> Result<Self> {
        let vault = private_root.join("vault");
        std::fs::create_dir_all(&vault)?;
        let workspace = Workspace::open(&vault)?;
        let drafts = DraftStore::open(private_root)?;
        Ok(Self { workspace, drafts })
    }

    pub fn snapshot(&mut self) -> Result<Value> {
        let note_count = self
            .workspace
            .tree()?
            .iter()
            .filter(|entry| {
                !entry.deleted
                    && !entry.is_folder
                    && entry.path.to_ascii_lowercase().ends_with(".md")
            })
            .count();
        Ok(json!({"vault_id":self.workspace.vault_id,"note_count":note_count}))
    }

    fn read(&mut self, file_id: &str) -> Result<Document> {
        self.workspace.tree()?;
        let path = self.workspace.path_for_id(file_id)?;
        if !path.to_ascii_lowercase().ends_with(".md") {
            return Err(HostError::new("CORE_NOTE_TYPE_UNSUPPORTED"));
        }
        if std::fs::metadata(self.workspace.resolve(&path)?)?.len() > MAX_NOTE_BYTES as u64 {
            return Err(HostError::new("CORE_NOTE_TOO_LARGE"));
        }
        self.workspace.read(&path)
    }

    fn draft_view(&mut self, draft_id: &str, checkout: bool) -> Result<DraftView> {
        if let Some(intent) = self.drafts.pending(draft_id)? {
            self.drafts
                .observe_save(&self.workspace, &intent.operation_id)?;
        }
        let draft = if checkout {
            self.drafts.checkout(draft_id)?
        } else {
            self.drafts.get(draft_id)?
        };
        let mut pending_mutation = None;
        if let Some(file_id) = &draft.file_id {
            if let Some(intent) = self.drafts.pending_mutation(file_id)? {
                let observed = self
                    .drafts
                    .observe_mutation(&self.workspace, &intent.operation_id)?;
                if observed.state == "prepared" {
                    pending_mutation = Some(observed);
                }
            }
        }
        // Mutation acknowledgements may have updated a path, but never the dirty text.
        let draft = self.drafts.get(draft_id)?;
        let (current, current_error) = match &draft.file_id {
            Some(file_id) => match self.read(file_id) {
                Ok(document) => (Some(document), None),
                Err(error) => (None, Some(error.code)),
            },
            None => (None, None),
        };
        let pending_save = self.drafts.pending(draft_id)?;
        let pending_image = self.drafts.pending_image(draft_id)?;
        Ok(DraftView {
            draft,
            current,
            current_error,
            pending_save,
            pending_mutation,
            pending_image,
        })
    }

    fn list_notes(
        &mut self,
        offset: usize,
        limit: usize,
        query: &str,
        directory: &str,
    ) -> Result<Value> {
        if !(1..=100).contains(&limit) || query.len() > 256 || directory.len() > 1024 {
            return Err(HostError::new("MOBILE_PAGE_INVALID"));
        }
        if !directory.is_empty() {
            self.workspace.resolve(directory)?;
        }
        let prefix = if directory.is_empty() {
            String::new()
        } else {
            format!("{}/", directory.trim_end_matches('/'))
        };
        let query = query.to_lowercase();
        let mut entries = self.workspace.tree()?;
        entries.retain(|e| {
            !e.deleted
                && !e.is_folder
                && e.path.to_ascii_lowercase().ends_with(".md")
                && e.path.starts_with(&prefix)
        });
        entries.sort_by(|a, b| a.path.cmp(&b.path));
        let mut directories = std::collections::BTreeSet::new();
        let mut matching = Vec::new();
        let mut unsearched_large_notes = 0;
        for entry in entries {
            let relative = &entry.path[prefix.len()..];
            if let Some((child, _)) = relative.split_once('/') {
                directories.insert(format!("{prefix}{child}"));
            }
            if query.is_empty() {
                if !relative.contains('/') {
                    matching.push(entry);
                }
            } else if entry.path.to_lowercase().contains(&query) {
                matching.push(entry);
            } else {
                let path = self.workspace.resolve(&entry.path)?;
                if std::fs::metadata(path)?.len() > MAX_NOTE_BYTES as u64 {
                    unsearched_large_notes += 1;
                    continue;
                }
                let document = self.workspace.read(&entry.path)?;
                if document.content.to_lowercase().contains(&query) {
                    matching.push(document.entry);
                }
            }
        }
        let total = matching.len();
        Ok(
            json!({"items":matching.into_iter().skip(offset).take(limit).collect::<Vec<_>>(),"total":total,"directories":directories,"unsearched_large_notes":unsearched_large_notes}),
        )
    }

    pub fn dispatch(&mut self, request: MobileRequest) -> Result<Value> {
        use MobileRequest::*;
        match request {
            ListNotes {
                offset,
                limit,
                query,
                directory,
            } => self.list_notes(offset, limit, &query, &directory),
            ListDrafts { offset, limit } => value(self.drafts.list(offset, limit)?),
            ListPending { offset, limit } => self.drafts.pending_operations(offset, limit),
            ReadNote { file_id } => value(self.read(&file_id)?),
            OpenNote { file_id } => {
                let draft = self.drafts.open_note(&mut self.workspace, &file_id)?;
                value(self.draft_view(&draft.id, false)?)
            }
            OpenDraft { draft_id } => value(self.draft_view(&draft_id, true)?),
            DraftStatus { draft_id } => value(self.draft_view(&draft_id, false)?),
            CreateDraft { path, content } => {
                let draft = self.drafts.create_with_content(
                    &self.workspace,
                    &path,
                    content.as_deref().unwrap_or(""),
                )?;
                value(self.draft_view(&draft.id, false)?)
            }
            PersistDraft {
                draft_id,
                edit_session,
                generation,
                content,
            } => value(
                self.drafts
                    .persist(&draft_id, &edit_session, generation, &content)?,
            ),
            PrepareSave {
                draft_id,
                edit_session,
                generation,
            } => value(self.drafts.prepare_save(
                &self.workspace,
                &draft_id,
                &edit_session,
                generation,
            )?),
            ObserveSave { operation_id } => {
                value(self.drafts.observe_save(&self.workspace, &operation_id)?)
            }
            ApplySave { operation_id } => {
                value(self.drafts.apply_save(&mut self.workspace, &operation_id)?)
            }
            CancelSave { operation_id } => {
                value(self.drafts.cancel_save(&self.workspace, &operation_id)?)
            }
            CopyDraft {
                draft_id,
                edit_session,
                path,
            } => {
                let draft =
                    self.drafts
                        .copy_as(&self.workspace, &draft_id, &edit_session, &path)?;
                value(self.draft_view(&draft.id, false)?)
            }
            DiscardDraft {
                draft_id,
                edit_session,
            } => {
                self.drafts
                    .discard(&self.workspace, &draft_id, &edit_session)?;
                Ok(json!({"discarded":true}))
            }
            PrepareMutation {
                action,
                file_id,
                expected,
                destination,
            } => value(self.drafts.prepare_mutation(
                &mut self.workspace,
                &action,
                &file_id,
                &expected,
                &destination,
            )?),
            ObserveMutation { operation_id } => value(
                self.drafts
                    .observe_mutation(&self.workspace, &operation_id)?,
            ),
            ApplyMutation { operation_id } => value(
                self.drafts
                    .apply_mutation(&mut self.workspace, &operation_id)?,
            ),
            CancelMutation { operation_id } => value(
                self.drafts
                    .cancel_mutation(&self.workspace, &operation_id)?,
            ),
            PrepareImage {
                draft_id,
                edit_session,
                generation,
                from,
                to,
                content_base64,
            } => value(self.drafts.prepare_image(
                &self.workspace,
                &draft_id,
                &edit_session,
                generation,
                from,
                to,
                &content_base64,
            )?),
            ObserveImage { operation_id } => value(self.drafts.observe_image(&operation_id)?),
            ApplyImage { operation_id } => value(
                self.drafts
                    .apply_image(&mut self.workspace, &operation_id)?,
            ),
            CancelImage { operation_id } => value(self.drafts.cancel_image(&operation_id)?),
            ReadImage { path } => {
                let request = json!({"rpc":"workspace.assets.read","params":{"vault_id":self.workspace.vault_id,"path":path}});
                crate::workspace_broker::dispatch(&mut self.workspace, &request)
                    .map_err(|code| HostError::new(&code))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_mobile_image_import_and_read_keep_original_bytes_and_deny_paths() {
        use base64::{engine::general_purpose::STANDARD, Engine};
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        let draft = engine
            .drafts
            .create_with_content(&engine.workspace, "notes/a.md", "before")
            .unwrap();
        let bytes = b"\x89PNG\r\n\x1a\noriginal-image-bytes";
        let request = json!({"kind":"prepare_image","draft_id":draft.id,"edit_session":draft.edit_session,"generation":1,"from":6,"to":6,"content_base64":STANDARD.encode(bytes)});
        let mut denied = request.clone();
        denied["root"] = json!("/other");
        assert!(serde_json::from_value::<MobileRequest>(denied).is_err());
        let prepared = engine
            .dispatch(serde_json::from_value(request).unwrap())
            .unwrap();
        let id = prepared["operation_id"].as_str().unwrap().to_owned();
        assert!(engine
            .dispatch(MobileRequest::DraftStatus {
                draft_id: draft.id.clone()
            })
            .unwrap()["pending_image"]
            .is_object());
        engine
            .dispatch(MobileRequest::ApplyImage {
                operation_id: id.clone(),
            })
            .unwrap();
        let path = prepared["asset_path"].as_str().unwrap().to_owned();
        let actual = engine.dispatch(MobileRequest::ReadImage { path }).unwrap();
        assert_eq!(actual["content_base64"], STANDARD.encode(bytes));
        assert_eq!(
            engine
                .dispatch(MobileRequest::ReadImage {
                    path: "../.ainote/host.sqlite3".into()
                })
                .unwrap_err()
                .code,
            "WORKSPACE_REQUEST_INVALID"
        );
        assert_eq!(
            engine
                .dispatch(MobileRequest::ObserveImage { operation_id: id })
                .unwrap()["state"],
            "committed"
        );
        assert_eq!(
            engine.drafts.get(&draft.id).unwrap().content,
            format!("before![]({})", prepared["reference"].as_str().unwrap())
        );
        assert!(!engine.workspace.resolve("notes/a.md").unwrap().exists());
    }

    #[test]
    fn pending_recovery_pages_expose_original_ids_without_bodies_or_execution() {
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        let draft = engine
            .drafts
            .create_with_content(&engine.workspace, "a.md", "private recovery body")
            .unwrap();
        let saved = engine
            .drafts
            .prepare_save(&engine.workspace, &draft.id, &draft.edit_session, 1)
            .unwrap();
        let entry = engine
            .workspace
            .write("b.md", "", b"saved", "local")
            .unwrap();
        let mutation = engine
            .drafts
            .prepare_mutation(
                &mut engine.workspace,
                "delete",
                &entry.file_id,
                &entry.hash,
                "",
            )
            .unwrap();
        let page = engine
            .dispatch(MobileRequest::ListPending {
                offset: 0,
                limit: 1,
            })
            .unwrap();
        assert_eq!(page["total"], 2);
        assert_eq!(page["items"].as_array().unwrap().len(), 1);
        assert!(!page.to_string().contains("private recovery body"));
        assert!(engine
            .workspace
            .operation(&saved.operation_id)
            .unwrap()
            .is_none());
        assert!(engine
            .workspace
            .operation(&mutation.operation_id)
            .unwrap()
            .is_none());
        assert!(!engine.workspace.root.join("a.md").exists());
        assert!(engine.workspace.root.join("b.md").exists());
    }

    #[test]
    fn serialized_mobile_save_requires_explicit_apply_and_reopens_exact_markdown() {
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        let opened = engine
            .dispatch(MobileRequest::CreateDraft {
                path: "目录/笔记.md".into(),
                content: None,
            })
            .unwrap();
        let draft: Draft = serde_json::from_value(opened["draft"].clone()).unwrap();
        let content = "# 相同标题\r\n\r\n## 相同标题\r\n中文 😀\n";
        engine
            .dispatch(MobileRequest::PersistDraft {
                draft_id: draft.id.clone(),
                edit_session: draft.edit_session.clone(),
                generation: 2,
                content: content.into(),
            })
            .unwrap();
        let prepared = engine
            .dispatch(MobileRequest::PrepareSave {
                draft_id: draft.id.clone(),
                edit_session: draft.edit_session,
                generation: 2,
            })
            .unwrap();
        let operation_id = prepared["operation_id"].as_str().unwrap().to_owned();
        assert_eq!(
            engine
                .dispatch(MobileRequest::ObserveSave {
                    operation_id: operation_id.clone()
                })
                .unwrap()["state"],
            "prepared"
        );
        assert!(!engine.workspace.root.join("目录/笔记.md").exists());
        assert_eq!(
            engine
                .dispatch(MobileRequest::ApplySave { operation_id })
                .unwrap()["state"],
            "committed"
        );
        let saved = engine.drafts.get(&draft.id).unwrap();
        let file_id = saved.file_id.unwrap();
        drop(engine);
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        let reopened = engine
            .dispatch(MobileRequest::OpenNote { file_id })
            .unwrap();
        assert_eq!(reopened["draft"]["content"], content);
        assert_eq!(reopened["current"]["content"], content);
        assert_eq!(engine.drafts.list(0, 20).unwrap().total, 0);
        assert_eq!(
            std::fs::read(engine.workspace.root.join("目录/笔记.md")).unwrap(),
            content.as_bytes()
        );
    }

    #[test]
    fn typed_mobile_requests_deny_extra_roots_unknown_methods_and_private_paths() {
        assert!(serde_json::from_value::<MobileRequest>(
            json!({"kind":"create_draft","path":"a.md","root":"C:/user"})
        )
        .is_err());
        assert!(serde_json::from_value::<MobileRequest>(
            json!({"kind":"workspace.experiments.run"})
        )
        .is_err());
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        for path in ["../outside.md", "/absolute.md", ".ainote/internal.md"] {
            assert!(engine
                .dispatch(MobileRequest::CreateDraft {
                    path: path.into(),
                    content: None
                })
                .is_err());
        }
    }

    #[test]
    fn native_search_and_directories_preserve_original_content_without_an_outbox_write() {
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        engine
            .workspace
            .write("根笔记.md", "", "中文 😀\r\n".as_bytes(), "local")
            .unwrap();
        engine
            .workspace
            .write("目录/another.md", "", "unique正文\n".as_bytes(), "local")
            .unwrap();
        let root = engine.list_notes(0, 20, "", "").unwrap();
        assert_eq!(root["total"], 1);
        assert_eq!(root["directories"][0], "目录");
        let found = engine.list_notes(0, 20, "正文", "").unwrap();
        assert_eq!(found["total"], 1);
        assert_eq!(found["items"][0]["path"], "目录/another.md");
        assert_eq!(
            engine.workspace.read("根笔记.md").unwrap().content,
            "中文 😀\r\n"
        );
        let count: i64 = engine
            .workspace
            .db
            .query_row("SELECT COUNT(*) FROM outbox", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 2);
    }

    #[test]
    fn discarded_pending_draft_cannot_be_saved_by_a_late_apply_and_copy_keeps_both_texts() {
        let temp = tempfile::tempdir().unwrap();
        let mut engine = MobileWorkspace::open(temp.path()).unwrap();
        let draft = engine.drafts.create(&engine.workspace, "a.md").unwrap();
        engine
            .drafts
            .persist(&draft.id, &draft.edit_session, 2, "keep me")
            .unwrap();
        let intent = engine
            .drafts
            .prepare_save(&engine.workspace, &draft.id, &draft.edit_session, 2)
            .unwrap();
        let copy = engine
            .drafts
            .copy_as(&engine.workspace, &draft.id, &draft.edit_session, "副本.md")
            .unwrap();
        assert_eq!(copy.content, "keep me");
        assert_eq!(engine.drafts.get(&draft.id).unwrap().content, "keep me");
        engine
            .drafts
            .discard(&engine.workspace, &draft.id, &draft.edit_session)
            .unwrap();
        assert_eq!(
            engine
                .drafts
                .apply_save(&mut engine.workspace, &intent.operation_id)
                .unwrap_err()
                .code,
            "SAVE_REQUIRES_REVIEW"
        );
        assert!(!engine.workspace.root.join("a.md").exists());
        assert_eq!(engine.drafts.get(&copy.id).unwrap().content, "keep me");
    }
}

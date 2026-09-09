//! 可选逻辑数据的设备本地选择；从未导出为同步记录。
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OptionalScope {
    pub persona: bool,
    pub layout: bool,
    pub conversations: bool,
    pub agent_history: bool,
    pub provider_settings: bool,
    pub extension_installations: bool,
}

impl OptionalScope {
    pub fn includes(&self, path: &str) -> bool {
        match path {
            "opennexus-records/v1/persona/default.json" => self.persona,
            "opennexus-records/v1/layout/sidebars.json" => self.layout,
            path if path.starts_with("opennexus-records/v1/conversations/") => self.conversations,
            path if path.starts_with("opennexus-records/v1/agent-history/") => self.agent_history,
            path if path.starts_with("opennexus-records/v1/provider-settings/") => {
                self.provider_settings
            }
            path if path.starts_with("opennexus-records/v1/extension-installations/") => {
                self.extension_installations
            }
            _ => true,
        }
    }
}

impl Workspace {
    pub fn sync_path_enabled(&self, path: &str) -> Result<bool> {
        Ok(crate::sync_discovery::allowed(path) && self.sync_optional_scope()?.includes(path))
    }

    pub fn sync_optional_scope(&self) -> Result<OptionalScope> {
        Ok(self
            .db
            .query_row(
                "SELECT persona,layout,conversations,agent_history,provider_settings,extension_installations FROM sync_optional_scope WHERE id=1",
                [],
                |row| {
                    Ok(OptionalScope {
                        persona: row.get(0)?,
                        layout: row.get(1)?,
                        conversations: row.get(2)?,
                        agent_history: row.get(3)?,
                        provider_settings: row.get(4)?,
                        extension_installations: row.get(5)?,
                    })
                },
            )
            .optional()?
            .unwrap_or_default())
    }

    pub fn sync_set_optional_scope(&mut self, scope: OptionalScope) -> Result<()> {
        if self.sync_binding()?.is_some() {
            return Err(HostError::new("SYNC_SCOPE_REBIND_REQUIRED"));
        }
        self.db.execute(
            "INSERT INTO sync_optional_scope (id,persona,layout,conversations,agent_history,provider_settings,extension_installations) VALUES (1,?1,?2,?3,?4,?5,?6) ON CONFLICT(id) DO UPDATE SET persona=excluded.persona,layout=excluded.layout,conversations=excluded.conversations,agent_history=excluded.agent_history,provider_settings=excluded.provider_settings,extension_installations=excluded.extension_installations",
            rusqlite::params![scope.persona, scope.layout, scope.conversations, scope.agent_history, scope.provider_settings, scope.extension_installations],
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn old_queued_optional_jobs_cannot_bypass_missing_consent() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.sync_set_optional_scope(OptionalScope {
            persona: true,
            layout: false,
            ..OptionalScope::default()
        })
        .unwrap();
        let data = serde_json::to_vec(&serde_json::json!({"schema":1,"kind":"persona","id":"default","data":{"version":0,"name":"local","system_prompt":"private","dialogue_pairs":[]}})).unwrap();
        let path = "opennexus-records/v1/persona/default.json";
        ws.write(path, "", &data, "local").unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        let job = ws.sync_next(&binding.id).unwrap().unwrap();
        // 对模式迁移后范围内数据库的待处理作业进行建模。
        ws.db
            .execute("DELETE FROM sync_optional_scope", [])
            .unwrap();
        assert_eq!(
            ws.sync_commit_payload(&job).unwrap_err().code,
            "SYNC_SCOPE_DISABLED"
        );
        assert!(ws.sync_next(&binding.id).unwrap().is_none());
        ws.write("note.md", "", b"still synchronized", "local")
            .unwrap();
        ws.sync_capture(&binding.id).unwrap();
        assert_eq!(ws.sync_next(&binding.id).unwrap().unwrap().path, "note.md");
        assert_eq!(std::fs::read(root.path().join(path)).unwrap(), data);
    }
    #[test]
    fn excluded_records_neither_upload_nor_require_object_bytes_to_advance_cursor() {
        use crate::sync_inbox::RemoteRevision;
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let path = "opennexus-records/v1/persona/default.json";
        let local = serde_json::to_vec(&serde_json::json!({"schema":1,"kind":"persona","id":"default","data":{"version":0,"name":"Local only","system_prompt":"private","dialogue_pairs":[]}})).unwrap();
        ws.write(path, "", &local, "local").unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example", "remote", "account")
            .unwrap();
        ws.sync_capture(&binding.id).unwrap();
        assert!(ws.sync_next(&binding.id).unwrap().is_none());
        let revision = RemoteRevision {
            vault_id: "remote".into(),
            sequence: 1,
            file_id: uuid::Uuid::new_v4().to_string(),
            base_revision: 0,
            path: path.into(),
            operation: "put".into(),
            hash: Some("a".repeat(64)),
            size: 123,
            operation_id: uuid::Uuid::new_v4().to_string(),
        };
        ws.sync_set_boundary(&binding.id, 1).unwrap();
        ws.sync_stage(&binding.id, &revision).unwrap();
        assert!(ws.sync_apply_pending(&binding.id).unwrap());
        assert_eq!(ws.sync_binding().unwrap().unwrap().cursor, 1);
        assert_eq!(std::fs::read(root.path().join(path)).unwrap(), local);
        assert!(!ws
            .sync_spool(revision.hash.as_ref().unwrap())
            .unwrap()
            .exists());
        assert!(ws.sync_conflicts(&binding.id).unwrap().is_empty());
        ws.sync_unbind(&binding.id).unwrap();
        let snapshot = crate::sync_initial::Snapshot {
            boundary: 1,
            items: vec![revision],
        };
        let before = ws
            .sync_preview("https://sync.example", "remote", "account", &snapshot)
            .unwrap();
        assert!(before.items.is_empty());
        ws.sync_set_optional_scope(OptionalScope {
            persona: true,
            layout: false,
            ..OptionalScope::default()
        })
        .unwrap();
        assert_eq!(
            ws.sync_bind_initial(
                "https://sync.example",
                "remote",
                "account",
                &snapshot,
                &before.fingerprint
            )
            .err()
            .unwrap()
            .code,
            "SYNC_PREVIEW_CHANGED"
        );
        let after = ws
            .sync_preview("https://sync.example", "remote", "account", &snapshot)
            .unwrap();
        assert_eq!(after.items.len(), 1);
        assert_eq!(after.items[0].action, "conflict");
    }
    #[test]
    fn schema_ten_upgrade_preserves_vault_and_does_not_infer_consent() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let entry = ws.write("note.md", "", b"retained", "local").unwrap();
        let vault = ws.vault_id.clone();
        ws.db
            .execute_batch("DROP TABLE sync_optional_scope; PRAGMA user_version=10;")
            .unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.vault_id, vault);
        assert_eq!(ws.read("note.md").unwrap().entry.file_id, entry.file_id);
        assert_eq!(ws.read("note.md").unwrap().content, "retained");
        assert_eq!(ws.pending_count().unwrap(), 1);
        assert_eq!(ws.sync_optional_scope().unwrap(), OptionalScope::default());
        assert_eq!(
            ws.db
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            13
        );
    }
    #[test]
    fn optional_scope_is_off_by_default_local_durable_and_frozen_while_bound() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(a.path()).unwrap();
        let off = ws.sync_optional_scope().unwrap();
        assert!(!off.includes("opennexus-records/v1/persona/default.json"));
        assert!(!off.includes("opennexus-records/v1/layout/sidebars.json"));
        assert!(!off.includes(
            "opennexus-records/v1/conversations/conversation_00000000000000000000000000000001.json"
        ));
        assert!(!off.includes(
            "opennexus-records/v1/agent-history/agent_run_00000000000000000000000000000001.json"
        ));
        assert!(!off.includes(
            "opennexus-records/v1/provider-settings/provider_00000000000000000000000000000001.json"
        ));
        assert!(!off.includes("opennexus-records/v1/extension-installations/extension_00000000000000000000000000000001.json"));
        assert!(off.includes("notes/example.md"));
        let chosen = OptionalScope {
            persona: true,
            layout: false,
            conversations: true,
            agent_history: true,
            provider_settings: true,
            extension_installations: true,
        };
        ws.sync_set_optional_scope(chosen).unwrap();
        assert_eq!(ws.pending_count().unwrap(), 0);
        drop(ws);
        let mut ws = Workspace::open(a.path()).unwrap();
        assert_eq!(ws.sync_optional_scope().unwrap(), chosen);
        assert_eq!(
            Workspace::open(b.path())
                .unwrap()
                .sync_optional_scope()
                .unwrap(),
            off
        );
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        assert_eq!(
            ws.sync_set_optional_scope(off).unwrap_err().code,
            "SYNC_SCOPE_REBIND_REQUIRED"
        );
        assert_eq!(ws.sync_optional_scope().unwrap(), chosen);
        ws.sync_unbind(&binding.id).unwrap();
        ws.sync_set_optional_scope(off).unwrap();
        assert_eq!(ws.sync_optional_scope().unwrap(), off);
    }

    #[test]
    fn scope_rejects_unknown_fields_instead_of_authorizing_future_categories() {
        assert!(serde_json::from_str::<OptionalScope>(
            r#"{"persona":true,"layout":false,"conversations":false,"agent_history":false,"provider_settings":false,"extension_installations":false,"credentials":true}"#
        )
        .is_err());
    }
}

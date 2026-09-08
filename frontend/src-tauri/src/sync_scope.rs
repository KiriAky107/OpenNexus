//! Device-local choices for optional logical data; never exported as sync records.
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OptionalScope {
    pub persona: bool,
    pub layout: bool,
}

impl OptionalScope {
    pub fn includes(&self, path: &str) -> bool {
        match path {
            "opennexus-records/v1/persona/default.json" => self.persona,
            "opennexus-records/v1/layout/sidebars.json" => self.layout,
            _ => true,
        }
    }
}

impl Workspace {
    pub fn sync_optional_scope(&self) -> Result<OptionalScope> {
        Ok(self
            .db
            .query_row(
                "SELECT persona,layout FROM sync_optional_scope WHERE id=1",
                [],
                |row| {
                    Ok(OptionalScope {
                        persona: row.get(0)?,
                        layout: row.get(1)?,
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
            "INSERT INTO sync_optional_scope VALUES (1,?1,?2) ON CONFLICT(id) DO UPDATE SET persona=excluded.persona,layout=excluded.layout",
            rusqlite::params![scope.persona, scope.layout],
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
            11
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
        assert!(off.includes("notes/example.md"));
        let chosen = OptionalScope {
            persona: true,
            layout: false,
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
            r#"{"persona":true,"layout":false,"credentials":true}"#
        )
        .is_err());
    }
}

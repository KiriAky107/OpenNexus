//! 用户明确选择过的 Vault 授权记录；只保存身份和路径，不读取或复制笔记正文。

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

use crate::workspace::{portable_path, portable_path_string};

const MAX_RECENT_VAULTS: i64 = 20;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecentVault {
    pub vault_id: String,
    pub path: String,
    pub name: String,
}

pub struct RecentVaultStore {
    db: Connection,
}

impl RecentVaultStore {
    pub fn open(path: &Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        }
        let db = Connection::open(path).map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
             CREATE TABLE IF NOT EXISTS recent_vaults (
               path TEXT PRIMARY KEY NOT NULL,
               vault_id TEXT NOT NULL,
               name TEXT NOT NULL,
               ordering INTEGER NOT NULL
             );",
        )
        .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        Ok(Self { db })
    }

    pub fn list(&self) -> Result<Vec<RecentVault>, String> {
        let mut query = self
            .db
            .prepare("SELECT vault_id,path,name FROM recent_vaults ORDER BY ordering DESC LIMIT ?")
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        let rows = query
            .query_map([MAX_RECENT_VAULTS], |row| {
                Ok(RecentVault {
                    vault_id: row.get(0)?,
                    path: row.get(1)?,
                    name: row.get(2)?,
                })
            })
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .map(|vaults| {
                vaults
                    .into_iter()
                    .map(|mut vault| {
                        vault.path = portable_path_string(Path::new(&vault.path));
                        vault
                    })
                    .collect()
            })
            .map_err(|_| "RECENT_VAULT_STORE_ERROR".into())
    }

    pub fn remember(&mut self, vault: &RecentVault) -> Result<(), String> {
        let (canonical, portable) = path_forms(Path::new(&vault.path))?;
        let transaction = self
            .db
            .transaction()
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        let ordering: i64 = transaction
            .query_row(
                "SELECT COALESCE(MAX(ordering),0)+1 FROM recent_vaults",
                [],
                |row| row.get(0),
            )
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        transaction
            .execute(
                "DELETE FROM recent_vaults WHERE path=? OR path=?",
                params![canonical, portable],
            )
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        transaction
            .execute(
                "INSERT INTO recent_vaults(path,vault_id,name,ordering) VALUES(?,?,?,?)
                 ON CONFLICT(path) DO UPDATE SET vault_id=excluded.vault_id,name=excluded.name,ordering=excluded.ordering",
                params![portable, vault.vault_id, vault.name, ordering],
            )
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        transaction
            .execute(
                "DELETE FROM recent_vaults WHERE path IN (
                   SELECT path FROM recent_vaults ORDER BY ordering DESC LIMIT -1 OFFSET ?
                 )",
                [MAX_RECENT_VAULTS],
            )
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        transaction
            .commit()
            .map_err(|_| "RECENT_VAULT_STORE_ERROR".into())
    }

    pub fn authorized(&self, path: &Path) -> Result<Option<RecentVault>, String> {
        let (canonical, portable) = path_forms(path)?;
        let result = self.db
            .query_row(
                "SELECT vault_id,path,name FROM recent_vaults WHERE path=? OR path=? ORDER BY ordering DESC LIMIT 1",
                params![canonical, portable],
                |row| {
                    Ok(RecentVault {
                        vault_id: row.get(0)?,
                        path: row.get(1)?,
                        name: row.get(2)?,
                    })
                },
            )
            .optional()
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        Ok(result.map(|mut vault| {
            vault.path = portable_path_string(Path::new(&vault.path));
            vault
        }))
    }

    pub fn revoke(&mut self, path: &Path) -> Result<(), String> {
        let (canonical, portable) = path_forms(path)?;
        self.db
            .execute(
                "DELETE FROM recent_vaults WHERE path=? OR path=?",
                params![canonical, portable],
            )
            .map_err(|_| "RECENT_VAULT_STORE_ERROR")?;
        Ok(())
    }
}

fn path_forms(path: &Path) -> Result<(String, String), String> {
    let accessible = portable_path(path);
    let canonical = accessible
        .canonicalize()
        .map_err(|_| "VAULT_PATH_UNSUPPORTED")?;
    Ok((
        canonical.to_string_lossy().into_owned(),
        portable_path_string(&canonical),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vault(root: &Path, id: usize) -> RecentVault {
        let path = root.join(format!("vault-{id}"));
        fs::create_dir(&path).unwrap();
        RecentVault {
            vault_id: format!("id-{id}"),
            path: portable_path_string(&path.canonicalize().unwrap()),
            name: format!("Vault {id}"),
        }
    }

    #[test]
    fn persists_order_authorization_and_revocation() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("state/host.sqlite3");
        let first = vault(temporary.path(), 1);
        let second = vault(temporary.path(), 2);
        let mut store = RecentVaultStore::open(&database).unwrap();
        store.remember(&first).unwrap();
        store.remember(&second).unwrap();
        store.remember(&first).unwrap();
        assert_eq!(store.list().unwrap(), vec![first.clone(), second]);
        drop(store);

        let mut reopened = RecentVaultStore::open(&database).unwrap();
        assert_eq!(
            reopened.authorized(Path::new(&first.path)).unwrap(),
            Some(first.clone())
        );
        reopened.revoke(Path::new(&first.path)).unwrap();
        assert_eq!(reopened.authorized(Path::new(&first.path)).unwrap(), None);
    }

    #[test]
    fn keeps_only_the_most_recent_authorizations() {
        let temporary = tempfile::tempdir().unwrap();
        let mut store = RecentVaultStore::open(&temporary.path().join("host.sqlite3")).unwrap();
        for id in 0..25 {
            store.remember(&vault(temporary.path(), id)).unwrap();
        }
        let items = store.list().unwrap();
        assert_eq!(items.len(), MAX_RECENT_VAULTS as usize);
        assert_eq!(items[0].vault_id, "id-24");
        assert_eq!(items[19].vault_id, "id-5");
    }

    #[cfg(windows)]
    #[test]
    fn reads_and_replaces_legacy_verbatim_paths() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("host.sqlite3");
        let current = vault(temporary.path(), 1);
        let legacy = Path::new(&current.path)
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        assert!(legacy.starts_with(r"\\?\"));

        let mut store = RecentVaultStore::open(&database).unwrap();
        store
            .db
            .execute(
                "INSERT INTO recent_vaults VALUES (?,?,?,1)",
                params![legacy, current.vault_id, current.name],
            )
            .unwrap();

        assert_eq!(store.list().unwrap(), vec![current.clone()]);
        assert_eq!(
            store.authorized(Path::new(&current.path)).unwrap(),
            Some(current.clone())
        );
        store.remember(&current).unwrap();
        let stored: String = store
            .db
            .query_row("SELECT path FROM recent_vaults", [], |row| row.get(0))
            .unwrap();
        assert_eq!(stored, current.path);
    }
}

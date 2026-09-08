//! Atomic package/configuration pointers. A pointer is never a runtime permission.
use crate::workspace::{hash, HostError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub slot: String,
    pub package_key: String,
    pub directory: String,
    pub tree_sha256: String,
    pub configuration: serde_json::Value,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Change {
    pub target: Target,
    pub expected_revision: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Active {
    pub target: Target,
    pub revision: String,
    pub pending_operation: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Receipt {
    pub operation_id: String,
    pub state: String,
}
pub fn schema(db: &Connection) -> Result<()> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS extension_active(slot TEXT PRIMARY KEY,target TEXT NOT NULL,revision TEXT NOT NULL,pending_operation TEXT);
        CREATE TABLE IF NOT EXISTS extension_transactions(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,state TEXT NOT NULL);")?;
    Ok(())
}
pub fn active(db: &Connection, slot: &str) -> Result<Option<Active>> {
    let row: Option<(String, String, Option<String>)> = db
        .query_row(
            "SELECT target,revision,pending_operation FROM extension_active WHERE slot=?1",
            [slot],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .optional()?;
    row.map(|(target, revision, pending_operation)| {
        Ok(Active {
            target: serde_json::from_str(&target)
                .map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?,
            revision,
            pending_operation,
        })
    })
    .transpose()
}
fn encoded<T: Serialize>(value: &T) -> Result<String> {
    serde_json::to_string(value).map_err(|_| HostError::new("EXTENSION_TRANSACTION_INVALID"))
}
pub fn switch(db: &mut Connection, operation: &str, changes: &[Change]) -> Result<Receipt> {
    switch_inner(db, operation, changes, |_| Ok(()))
}
fn switch_inner(
    db: &mut Connection,
    operation: &str,
    changes: &[Change],
    mut checkpoint: impl FnMut(&str) -> Result<()>,
) -> Result<Receipt> {
    if uuid::Uuid::parse_str(operation).is_err() || changes.is_empty() || changes.len() > 200 {
        return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
    }
    let serialized = encoded(&changes)?;
    if serialized.len() > 4 * 1024 * 1024 {
        return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
    }
    let fingerprint = hash(serialized.as_bytes());
    let transaction = db.transaction()?;
    let prior: Option<(String, String)> = transaction
        .query_row(
            "SELECT fingerprint,state FROM extension_transactions WHERE id=?1",
            [operation],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    if let Some((previous, state)) = prior {
        if previous != fingerprint {
            return Err(HostError::new("OPERATION_REUSED"));
        }
        return Ok(Receipt {
            operation_id: operation.into(),
            state,
        });
    }
    let mut slots = BTreeSet::new();
    let mut before = Vec::new();
    for change in changes {
        let target = &change.target;
        for digest in [&target.slot, &target.package_key, &target.tree_sha256] {
            if digest.len() != 64
                || !digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
            }
        }
        if uuid::Uuid::parse_str(&target.directory)
            .map(|v| v.to_string())
            .ok()
            .as_ref()
            != Some(&target.directory)
            || !target.configuration.is_object()
            || !slots.insert(&target.slot)
        {
            return Err(HostError::new("EXTENSION_TRANSACTION_INVALID"));
        }
        let previous = active(&transaction, &target.slot)?;
        if previous
            .as_ref()
            .is_some_and(|p| p.pending_operation.is_some())
        {
            return Err(HostError::new("EXTENSION_TRANSACTION_BUSY"));
        }
        if previous.as_ref().map(|p| &p.revision) != change.expected_revision.as_ref() {
            return Err(HostError::new("EXTENSION_INSTALL_CONFLICT"));
        }
        before.push(previous);
    }
    transaction.execute(
        "INSERT INTO extension_transactions VALUES (?1,?2,?3,?4,'checking')",
        params![operation, fingerprint, encoded(&before)?, serialized],
    )?;
    checkpoint("journal_recorded")?;
    for change in changes {
        let target = encoded(&change.target)?;
        transaction.execute("INSERT INTO extension_active VALUES (?1,?2,?3,?4) ON CONFLICT(slot) DO UPDATE SET target=excluded.target,revision=excluded.revision,pending_operation=excluded.pending_operation",
            params![change.target.slot,target,hash(target.as_bytes()),operation])?;
        checkpoint("pointer_recorded")?;
    }
    transaction.commit()?;
    checkpoint("switch_committed")?;
    Ok(Receipt {
        operation_id: operation.into(),
        state: "checking".into(),
    })
}

/// `healthy` must come from the Host's matching package/config health probe.
/// Recovery calls this with false; it never reissues any execution permits.
pub fn finish(db: &mut Connection, operation: &str, healthy: bool) -> Result<Receipt> {
    let tx = db.transaction()?;
    let (before, after, state): (String, String, String) = tx.query_row(
        "SELECT before_state,after_state,state FROM extension_transactions WHERE id=?1",
        [operation],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
    )?;
    if state != "checking" {
        return Ok(Receipt {
            operation_id: operation.into(),
            state,
        });
    }
    let changes: Vec<Change> =
        serde_json::from_str(&after).map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
    let previous: Vec<Option<Active>> =
        serde_json::from_str(&before).map_err(|_| HostError::new("EXTENSION_STORE_CORRUPT"))?;
    if previous.len() != changes.len() {
        return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
    }
    for (change, old) in changes.iter().zip(previous) {
        let current = active(&tx, &change.target.slot)?
            .ok_or_else(|| HostError::new("EXTENSION_STORE_CORRUPT"))?;
        if current.pending_operation.as_deref() != Some(operation)
            || current.target != change.target
        {
            return Err(HostError::new("EXTENSION_STORE_CORRUPT"));
        }
        if healthy {
            tx.execute(
                "UPDATE extension_active SET pending_operation=NULL WHERE slot=?1",
                [&change.target.slot],
            )?;
        } else if let Some(old) = old {
            tx.execute("UPDATE extension_active SET target=?2,revision=?3,pending_operation=NULL WHERE slot=?1",
                params![change.target.slot,encoded(&old.target)?,old.revision])?;
        } else {
            tx.execute(
                "DELETE FROM extension_active WHERE slot=?1",
                [&change.target.slot],
            )?;
        }
    }
    let state = if healthy { "complete" } else { "rolled_back" };
    tx.execute(
        "UPDATE extension_transactions SET state=?2 WHERE id=?1",
        params![operation, state],
    )?;
    tx.commit()?;
    Ok(Receipt {
        operation_id: operation.into(),
        state: state.into(),
    })
}
pub fn recover(db: &mut Connection) -> Result<usize> {
    let ids: Vec<String> = db
        .prepare("SELECT id FROM extension_transactions WHERE state='checking' ORDER BY id")?
        .query_map([], |r| r.get(0))?
        .collect::<std::result::Result<_, _>>()?;
    for id in &ids {
        finish(db, id, false)?;
    }
    Ok(ids.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn open(path: &std::path::Path) -> Connection {
        let db = Connection::open(path).unwrap();
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")
            .unwrap();
        schema(&db).unwrap();
        db
    }
    fn change(slot: char, version: u8, previous: Option<String>) -> Change {
        Change {
            target: Target {
                slot: slot.to_string().repeat(64),
                package_key: format!("{version:x}").repeat(64),
                directory: uuid::Uuid::new_v4().to_string(),
                tree_sha256: "a".repeat(64),
                configuration: serde_json::json!({"version": version}),
            },
            expected_revision: previous,
        }
    }
    #[test]
    fn group_switch_crashes_recover_matching_packages_and_configuration() {
        for boundary in ["journal_recorded", "pointer_recorded", "switch_committed"] {
            for _ in 0..20 {
                let temp = tempfile::tempdir().unwrap();
                let path = temp.path().join("state.sqlite3");
                let mut db = open(&path);
                let old = vec![change('a', 1, None), change('b', 1, None)];
                let id = uuid::Uuid::new_v4().to_string();
                switch(&mut db, &id, &old).unwrap();
                finish(&mut db, &id, true).unwrap();
                let next: Vec<_> = old
                    .iter()
                    .map(|c| {
                        change(
                            c.target.slot.chars().next().unwrap(),
                            2,
                            Some(active(&db, &c.target.slot).unwrap().unwrap().revision),
                        )
                    })
                    .collect();
                let update = uuid::Uuid::new_v4().to_string();
                assert!(
                    switch_inner(&mut db, &update, &next, |at| if at == boundary {
                        Err(HostError::new("INJECTED"))
                    } else {
                        Ok(())
                    })
                    .is_err()
                );
                drop(db);
                let mut db = open(&path);
                recover(&mut db).unwrap();
                for original in &old {
                    let current = active(&db, &original.target.slot).unwrap().unwrap();
                    assert_eq!(current.target, original.target);
                    assert!(current.pending_operation.is_none());
                }
                assert_eq!(recover(&mut db).unwrap(), 0);
            }
        }
    }
    #[test]
    fn cas_busy_replay_and_failed_first_install() {
        let mut db = Connection::open_in_memory().unwrap();
        schema(&db).unwrap();
        let c = vec![change('a', 1, None)];
        let id = uuid::Uuid::new_v4().to_string();
        switch(&mut db, &id, &c).unwrap();
        assert!(switch(&mut db, &uuid::Uuid::new_v4().to_string(), &c).is_err());
        assert_eq!(switch(&mut db, &id, &c).unwrap().state, "checking");
        finish(&mut db, &id, false).unwrap();
        assert!(active(&db, &c[0].target.slot).unwrap().is_none());
        assert_eq!(switch(&mut db, &id, &c).unwrap().state, "rolled_back");
        assert_eq!(finish(&mut db, &id, true).unwrap().state, "rolled_back");
        let id = uuid::Uuid::new_v4().to_string();
        switch(&mut db, &id, &c).unwrap();
        finish(&mut db, &id, true).unwrap();
        assert!(switch(&mut db, &uuid::Uuid::new_v4().to_string(), &c).is_err());
        let mut different = c.clone();
        different[0].target.configuration = serde_json::json!({"changed":true});
        assert!(switch(&mut db, &id, &different).is_err());
    }
}

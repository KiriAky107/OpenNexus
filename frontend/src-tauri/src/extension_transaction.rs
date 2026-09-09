//! 原子包/配置指针。指针从来都不是运行时权限。
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

/// “healthy”必须来自 Host 的匹配包/配置运行状况探测。恢复称其为 false；它从不重新签发任何执行许可证。
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
    use std::{
        path::Path,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };
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
    fn seeded(path: &Path) -> (Vec<Change>, Vec<Change>) {
        let mut db = open(path);
        let old = vec![change('a', 1, None), change('b', 1, None)];
        let id = uuid::Uuid::new_v4().to_string();
        switch(&mut db, &id, &old).unwrap();
        finish(&mut db, &id, true).unwrap();
        let next = old
            .iter()
            .map(|current| {
                change(
                    current.target.slot.chars().next().unwrap(),
                    2,
                    Some(active(&db, &current.target.slot).unwrap().unwrap().revision),
                )
            })
            .collect();
        (old, next)
    }
    fn assert_complete_generation(db: &Connection) {
        let active: Vec<_> = ['a', 'b']
            .into_iter()
            .map(|slot| active(db, &slot.to_string().repeat(64)).unwrap().unwrap())
            .collect();
        let versions: BTreeSet<_> = active
            .iter()
            .map(|item| item.target.configuration["version"].as_u64().unwrap())
            .collect();
        assert_eq!(versions.len(), 1, "package/config generations were mixed");
        let version = *versions.first().unwrap();
        assert!(matches!(version, 1 | 2));
        for item in active {
            assert_eq!(item.target.package_key, format!("{version:x}").repeat(64));
            assert!(item.pending_operation.is_none());
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
    #[ignore = "parent acceptance oracle hard-terminates this helper at a durable boundary"]
    fn power_cut_worker() {
        let Some(path) = std::env::var_os("OPENNEXUS_D03_DATABASE") else {
            return;
        };
        let boundary = std::env::var("OPENNEXUS_D03_BOUNDARY").unwrap();
        let marker = std::path::PathBuf::from(std::env::var_os("OPENNEXUS_D03_MARKER").unwrap());
        let mut db = open(Path::new(&path));
        let old: Vec<_> = ['a', 'b']
            .into_iter()
            .map(|slot| {
                let current = active(&db, &slot.to_string().repeat(64)).unwrap().unwrap();
                Change {
                    target: current.target.clone(),
                    expected_revision: Some(current.revision),
                }
            })
            .collect();
        let next: Vec<_> = old
            .iter()
            .map(|current| {
                change(
                    current.target.slot.chars().next().unwrap(),
                    2,
                    current.expected_revision.clone(),
                )
            })
            .collect();
        let operation = uuid::Uuid::new_v4().to_string();
        let _ = switch_inner(&mut db, &operation, &next, |at| {
            if at == boundary {
                let file = std::fs::File::create(&marker).unwrap();
                file.sync_all().unwrap();
                loop {
                    thread::sleep(Duration::from_secs(60));
                }
            }
            Ok(())
        });
        panic!("power-cut helper passed the requested boundary");
    }
    #[test]
    fn group_switch_survives_hard_termination_twenty_times_per_boundary() {
        for boundary in ["journal_recorded", "pointer_recorded", "switch_committed"] {
            for round in 0..20 {
                let temp = tempfile::tempdir().unwrap();
                let path = temp.path().join("state.sqlite3");
                seeded(&path);
                let marker = temp.path().join(format!("{boundary}-{round}.ready"));
                let mut child = Command::new(std::env::current_exe().unwrap())
                    .args([
                        "--ignored",
                        "--exact",
                        "extension_transaction::tests::power_cut_worker",
                        "--nocapture",
                    ])
                    .env("OPENNEXUS_D03_DATABASE", &path)
                    .env("OPENNEXUS_D03_BOUNDARY", boundary)
                    .env("OPENNEXUS_D03_MARKER", &marker)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .unwrap();
                let started = Instant::now();
                while !marker.is_file() {
                    assert!(
                        child.try_wait().unwrap().is_none(),
                        "helper exited before {boundary}"
                    );
                    assert!(
                        started.elapsed() < Duration::from_secs(10),
                        "helper did not reach {boundary}"
                    );
                    thread::sleep(Duration::from_millis(5));
                }
                child.kill().unwrap();
                assert!(!child.wait().unwrap().success());
                let mut db = open(&path);
                recover(&mut db).unwrap();
                assert_complete_generation(&db);
                assert_eq!(recover(&mut db).unwrap(), 0);
            }
        }
    }
    #[test]
    fn disk_full_and_configuration_migration_failures_cover_every_boundary() {
        let mapped: HostError = rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_FULL),
            None,
        )
        .into();
        assert_eq!(mapped.code, "QUOTA_EXCEEDED");
        for failure in ["QUOTA_EXCEEDED", "EXTENSION_CONFIG_MIGRATION_FAILED"] {
            for boundary in ["journal_recorded", "pointer_recorded", "switch_committed"] {
                for _ in 0..20 {
                    let temp = tempfile::tempdir().unwrap();
                    let path = temp.path().join("state.sqlite3");
                    let (_, next) = seeded(&path);
                    let mut db = open(&path);
                    let update = uuid::Uuid::new_v4().to_string();
                    let error = switch_inner(&mut db, &update, &next, |at| {
                        if at == boundary {
                            Err(HostError::new(failure))
                        } else {
                            Ok(())
                        }
                    })
                    .unwrap_err();
                    assert_eq!(error.code, failure);
                    drop(db);
                    let mut db = open(&path);
                    recover(&mut db).unwrap();
                    assert_complete_generation(&db);
                    assert_eq!(recover(&mut db).unwrap(), 0);
                }
            }
        }
    }
    #[test]
    fn failed_switch_cannot_expand_an_existing_execution_permit() {
        use crate::extension_permit::{Authority, Claims, Environment, ExecutionKind};
        use std::collections::BTreeMap;

        let authority = Authority::default();
        let mut claims = Claims {
            kind: ExecutionKind::Mcp,
            source: "https://catalog.example/".into(),
            namespace: "examples".into(),
            package_id: "note-reviewer".into(),
            version: "1.0.0".into(),
            archive_sha256: "a".repeat(64),
            tree_sha256: "b".repeat(64),
            signer_sha256: "c".repeat(64),
            entry: "entry.exe".into(),
            arguments: vec!["--stdio".into()],
            environment: BTreeMap::from([(
                "MODE".into(),
                Environment::Literal("production".into()),
            )]),
            permissions: BTreeSet::from(["notes.read".into()]),
            vault_id: uuid::Uuid::new_v4().to_string(),
            platform: "windows".into(),
            policy_version: "1".into(),
            expires_at_ms: 10_000,
        };
        let permit = authority.issue(&claims, 1).unwrap();
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("state.sqlite3");
        let (_, next) = seeded(&path);
        let mut db = open(&path);
        let error = switch_inner(&mut db, &uuid::Uuid::new_v4().to_string(), &next, |at| {
            if at == "switch_committed" {
                Err(HostError::new("EXTENSION_CONFIG_MIGRATION_FAILED"))
            } else {
                Ok(())
            }
        })
        .unwrap_err();
        assert_eq!(error.code, "EXTENSION_CONFIG_MIGRATION_FAILED");
        recover(&mut db).unwrap();
        authority.verify(&permit, &claims, 2).unwrap();
        claims.permissions.insert("notes.write".into());
        assert_eq!(
            authority.verify(&permit, &claims, 2).unwrap_err().code,
            "PERMISSION_CHANGED"
        );
        assert_complete_generation(&db);
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

//! The issuing native device is part of an original mobile network attempt.
//! A new login cannot reuse a prior device's upload or uncertain commit. A pull
//! may still acknowledge its original operation through the existing inbox.
use crate::{
    mobile_sync_session::Scope,
    sync_client::identifier,
    sync_state::Job,
    workspace::{HostError, Result, Workspace},
};
use rusqlite::{params, OptionalExtension};

impl Workspace {
    pub(crate) fn mobile_sync_attempt_start(
        &self,
        job: &Job,
        scope: &Scope,
        device: &str,
    ) -> Result<()> {
        self.check_job(job)?;
        identifier(device).map_err(|_| HostError::new("SYNC_AUTH_IDENTITY_CHANGED"))?;
        let binding = self
            .sync_binding()?
            .ok_or_else(|| HostError::new("SYNC_NOT_BOUND"))?;
        if binding.endpoint != scope.endpoint() || binding.account != scope.account() {
            return Err(HostError::new("SYNC_AUTH_IDENTITY_CHANGED"));
        }
        // This optional metadata and the existing attempt counter commit as
        // one transaction. Old workspaces keep their original tables/version.
        let tx = self.db.unchecked_transaction()?;
        tx.execute_batch(
            "CREATE TABLE IF NOT EXISTS mobile_sync_attempt_devices (
            binding TEXT NOT NULL,operation_id TEXT NOT NULL,device_id TEXT NOT NULL,
            PRIMARY KEY(binding,operation_id));",
        )?;
        let original: Option<String> = tx.query_row(
            "SELECT device_id FROM mobile_sync_attempt_devices WHERE binding=?1 AND operation_id=?2",
            params![job.binding,job.operation_id],|row|row.get(0)).optional()?;
        match original {
            Some(original) if original != device => {
                return Err(HostError::new("SYNC_OPERATION_DEVICE_CHANGED"))
            }
            None => {
                let attempted: bool=tx.query_row(
                    "SELECT EXISTS(SELECT 1 FROM sync_attempts WHERE binding=?1 AND operation_id=?2)",
                    params![job.binding,job.operation_id],|row|row.get(0))?;
                if attempted {
                    return Err(HostError::new("SYNC_OPERATION_DEVICE_UNKNOWN"));
                }
                tx.execute(
                    "INSERT INTO mobile_sync_attempt_devices VALUES (?1,?2,?3)",
                    params![job.binding, job.operation_id, device],
                )?;
            }
            _ => {}
        }
        crate::sync_retry::start_attempt(&tx, job)?;
        tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn setup() -> (
        tempfile::TempDir,
        Workspace,
        Scope,
        crate::sync_state::Binding,
        Job,
    ) {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("original.md", "", b"original\r\n", "local")
            .unwrap();
        let scope = Scope::new("https://sync.example", "owned-reader").unwrap();
        let binding = ws
            .sync_bind_empty(scope.endpoint(), "owned-vault", scope.account())
            .unwrap();
        let job = ws.sync_next(&binding.id).unwrap().unwrap();
        (root, ws, scope, binding, job)
    }
    #[test]
    fn original_device_survives_restart_and_rejects_new_device_without_changing_receipt_payload_or_counter(
    ) {
        let (root, ws, scope, binding, job) = setup();
        ws.mobile_sync_attempt_start(&job, &scope, "device-a")
            .unwrap();
        ws.sync_upload(&job, Some("original-upload")).unwrap();
        let payload = ws.sync_commit_payload(&job).unwrap();
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        let original = ws.sync_next(&binding.id).unwrap().unwrap();
        let before = ws.sync_attempts(&binding.id).unwrap();
        assert_eq!(
            ws.mobile_sync_attempt_start(&original, &scope, "device-b")
                .unwrap_err()
                .code,
            "SYNC_OPERATION_DEVICE_CHANGED"
        );
        assert_eq!(ws.sync_attempts(&binding.id).unwrap(), before);
        assert_eq!(
            ws.sync_next(&binding.id)
                .unwrap()
                .unwrap()
                .upload_id
                .as_deref(),
            Some("original-upload")
        );
        assert_eq!(ws.sync_commit_payload(&original).unwrap(), payload);
        ws.mobile_sync_attempt_start(&original, &scope, "device-a")
            .unwrap();
        assert_eq!(ws.sync_attempts(&binding.id).unwrap()[0]["attempts"], 2);
        assert_eq!(
            std::fs::read(ws.root.join("original.md")).unwrap(),
            b"original\r\n"
        );
    }
    #[test]
    fn legacy_attempt_device_is_never_guessed() {
        let (_root, ws, scope, binding, job) = setup();
        ws.sync_attempt_start(&job).unwrap();
        let before = ws.sync_attempts(&binding.id).unwrap();
        assert_eq!(
            ws.mobile_sync_attempt_start(&job, &scope, "device-a")
                .unwrap_err()
                .code,
            "SYNC_OPERATION_DEVICE_UNKNOWN"
        );
        assert_eq!(ws.sync_attempts(&binding.id).unwrap(), before);
        assert_eq!(
            ws.sync_next(&binding.id).unwrap().unwrap().operation_id,
            job.operation_id
        );
        assert_eq!(ws.db.query_row("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mobile_sync_attempt_devices'",[],|r|r.get::<_,i64>(0)).unwrap(),0);
    }
    #[test]
    fn an_unattempted_original_job_can_start_with_the_current_native_device() {
        let (_root, ws, scope, binding, job) = setup();
        ws.mobile_sync_attempt_start(&job, &scope, "device-b")
            .unwrap();
        assert_eq!(ws.sync_attempts(&binding.id).unwrap()[0]["attempts"], 1);
        assert_eq!(
            ws.sync_next(&binding.id).unwrap().unwrap().operation_id,
            job.operation_id
        );
        assert_eq!(ws.db.query_row("SELECT device_id FROM mobile_sync_attempt_devices WHERE binding=?1 AND operation_id=?2",params![binding.id,job.operation_id],|r|r.get::<_,String>(0)).unwrap(),"device-b");
    }
    #[test]
    fn other_account_or_endpoint_cannot_create_device_metadata_or_an_attempt() {
        let (_root, ws, scope, binding, job) = setup();
        for other in [
            Scope::new(scope.endpoint(), "other-reader").unwrap(),
            Scope::new("https://other.example", scope.account()).unwrap(),
        ] {
            assert_eq!(
                ws.mobile_sync_attempt_start(&job, &other, "device-a")
                    .unwrap_err()
                    .code,
                "SYNC_AUTH_IDENTITY_CHANGED"
            );
        }
        assert!(ws.sync_attempts(&binding.id).unwrap().is_empty());
        assert_eq!(ws.db.query_row("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mobile_sync_attempt_devices'",[],|r|r.get::<_,i64>(0)).unwrap(),0);
    }
}

//! 绑定范围内的重试决定在重启后仍然有效；系统时钟变化后也会约束实际经过时间。
use crate::workspace::{Result, Workspace};
use rusqlite::{params, OptionalExtension};
use serde::Serialize;

#[derive(Default, Debug, Serialize)]
pub struct Retry {
    pub error: Option<String>,
    pub failures: u32,
    pub retry_at: Option<i64>,
    pub halted: bool,
}
impl Retry {
    pub fn remaining(&self, now: i64) -> u64 {
        self.retry_at
            .map(|at| at.saturating_sub(now).clamp(0, 3600) as u64)
            .unwrap_or(0)
    }
}
fn machine_code(code: &str) -> &str {
    if !code.is_empty()
        && code.len() <= 80
        && code
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
    {
        code
    } else {
        "SYNC_REMOTE_ERROR"
    }
}
impl Workspace {
    pub fn sync_attempt_start(&self, job: &crate::sync_state::Job) -> Result<()> {
        self.check_job(job)?;
        self.db.execute("INSERT INTO sync_attempts VALUES (?1,?2,1,'running',NULL) ON CONFLICT(binding,operation_id) DO UPDATE SET attempts=MIN(attempts+1,2147483647),outcome='running',error=NULL", params![job.binding, job.operation_id])?;
        Ok(())
    }
    pub fn sync_attempt_interrupt(&self, job: &crate::sync_state::Job) -> Result<()> {
        self.check_job(job)?;
        self.db.execute("UPDATE sync_attempts SET outcome=CASE WHEN EXISTS(SELECT 1 FROM sync_jobs j WHERE j.binding=?1 AND j.operation_id=?2 AND j.state='acked') THEN 'succeeded' ELSE 'interrupted' END WHERE binding=?1 AND operation_id=?2 AND outcome='running'", params![job.binding,job.operation_id])?;
        Ok(())
    }
    pub fn sync_attempt_finish(
        &self,
        job: &crate::sync_state::Job,
        error: Option<&str>,
    ) -> Result<()> {
        self.check_job(job)?;
        self.db.execute(
            "UPDATE sync_attempts SET outcome=?3,error=?4 WHERE binding=?1 AND operation_id=?2",
            params![
                job.binding,
                job.operation_id,
                if error.is_some() {
                    "failed"
                } else {
                    "succeeded"
                },
                error.map(machine_code)
            ],
        )?;
        Ok(())
    }
    pub fn sync_attempts(&self, binding: &str) -> Result<Vec<serde_json::Value>> {
        self.check_binding(binding)?;
        let mut statement = self.db.prepare("SELECT a.operation_id,j.path,a.attempts,a.outcome,a.error,j.state FROM sync_attempts a JOIN sync_jobs j ON j.binding=a.binding AND j.operation_id=a.operation_id WHERE a.binding=?1 AND j.state NOT IN ('acked','archived') ORDER BY a.rowid LIMIT 20")?;
        let rows = statement.query_map([binding], |row| Ok(serde_json::json!({"operation_id":row.get::<_,String>(0)?,"path":row.get::<_,String>(1)?,"attempts":row.get::<_,i64>(2)?,"outcome":row.get::<_,String>(3)?,"error":row.get::<_,Option<String>>(4)?,"state":row.get::<_,String>(5)?})))?;
        Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
    }

    pub fn sync_retry(&self, binding: &str) -> Result<Retry> {
        self.check_binding(binding)?;
        Ok(self
            .db
            .query_row(
                "SELECT error,failures,retry_at,halted FROM sync_retry WHERE binding=?1",
                [binding],
                |row| {
                    Ok(Retry {
                        error: row.get(0)?,
                        failures: row.get(1)?,
                        retry_at: row.get(2)?,
                        halted: row.get(3)?,
                    })
                },
            )
            .optional()?
            .unwrap_or_default())
    }
    pub fn sync_retry_clear(&mut self, binding: &str) -> Result<()> {
        self.check_binding(binding)?;
        self.db
            .execute("DELETE FROM sync_retry WHERE binding=?1", [binding])?;
        Ok(())
    }
    pub fn sync_retry_fail(
        &mut self,
        binding: &str,
        code: &str,
        status: u16,
        retry_after: Option<u64>,
        now: i64,
    ) -> Result<()> {
        let previous = self.sync_retry(binding)?;
        if code == "SYNC_CANCELLED" {
            return Ok(());
        }
        // 仅保留有界机器代码，从不保留任意远程响应字符串。
        let code = machine_code(code);
        let failures = if code == "CREDENTIALS_LOCKED" {
            0
        } else {
            previous.failures.saturating_add(1)
        };
        let halted = matches!(status, 401 | 403 | 413 | 426 | 507)
            || matches!(code, "PROTOCOL_INCOMPATIBLE" | "SYNC_LOGIN_REQUIRED");
        let retry_at = now.saturating_add(
            retry_after
                .unwrap_or(2u64.saturating_pow(failures.min(8)))
                .clamp(1, 3600) as i64,
        );
        self.db.execute("INSERT INTO sync_retry VALUES (?1,?2,?3,?4,?5) ON CONFLICT(binding) DO UPDATE SET error=excluded.error,failures=excluded.failures,retry_at=excluded.retry_at,halted=excluded.halted", params![binding,code,failures,retry_at,halted])?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn interrupted_job_attempts_survive_twenty_restarts_without_changing_commit_base() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        ws.write("attempt.md", "", b"body", "local").unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        let job = ws.sync_next(&binding.id).unwrap().unwrap();
        let payload = ws.sync_commit_payload(&job).unwrap();
        for count in 1..=20 {
            ws.sync_attempt_start(&job).unwrap();
            drop(ws);
            ws = Workspace::open(dir.path()).unwrap();
            let rows = ws.sync_attempts(&binding.id).unwrap();
            assert_eq!(rows[0]["attempts"], count);
            assert_eq!(rows[0]["outcome"], "interrupted");
            assert_eq!(ws.sync_commit_payload(&job).unwrap(), payload);
        }
        ws.sync_attempt_finish(&job, Some("response with secret body"))
            .unwrap();
        assert_eq!(
            ws.sync_attempts(&binding.id).unwrap()[0]["error"],
            "SYNC_REMOTE_ERROR"
        );
        ws.sync_attempt_start(&job).unwrap();
        let mut revision = payload.clone();
        revision["hash"] = payload["content_hash"].clone();
        revision["vault_id"] = serde_json::json!("remote");
        revision["sequence"] = serde_json::json!(1);
        ws.sync_ack(&job, &revision).unwrap();
        drop(ws);
        ws = Workspace::open(dir.path()).unwrap();
        assert!(ws.sync_attempts(&binding.id).unwrap().is_empty());
        assert_eq!(
            ws.db
                .query_row("SELECT outcome FROM sync_attempts", [], |r| r
                    .get::<_, String>(0))
                .unwrap(),
            "succeeded"
        );
        ws.sync_unbind(&binding.id).unwrap();
        assert!(ws.sync_attempt_finish(&job, None).is_err());
    }
    #[test]
    fn retry_history_survives_twenty_reopens_and_isolates_bindings() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        let b = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        for attempt in 1..=20 {
            ws.sync_retry_fail(&b.id, "NETWORK_ERROR", 0, Some(120), 1000)
                .unwrap();
            drop(ws);
            ws = Workspace::open(dir.path()).unwrap();
            let state = ws.sync_retry(&b.id).unwrap();
            assert_eq!(state.failures, attempt);
            assert_eq!(state.remaining(1010), 110);
            assert_eq!(state.remaining(-10000), 3600);
            assert_eq!(state.remaining(1200), 0);
        }
        ws.sync_retry_fail(&b.id, "SYNC_CANCELLED", 0, None, 1100)
            .unwrap();
        assert_eq!(ws.sync_retry(&b.id).unwrap().failures, 20);
        ws.sync_retry_fail(&b.id, "UNAUTHORIZED", 401, None, 1100)
            .unwrap();
        drop(ws);
        ws = Workspace::open(dir.path()).unwrap();
        assert!(ws.sync_retry(&b.id).unwrap().halted);
        ws.sync_retry_clear(&b.id).unwrap();
        assert!(ws.sync_retry(&b.id).unwrap().error.is_none());
        ws.sync_retry_fail(&b.id, "secret response body", 500, None, 1100)
            .unwrap();
        assert_eq!(
            ws.sync_retry(&b.id).unwrap().error.as_deref(),
            Some("SYNC_REMOTE_ERROR")
        );
        ws.sync_unbind(&b.id).unwrap();
        let next = ws
            .sync_bind_empty("https://sync.example", "other", "account")
            .unwrap();
        assert!(ws.sync_retry(&next.id).unwrap().error.is_none());
        assert!(ws.sync_retry_clear(&b.id).is_err());
    }
}

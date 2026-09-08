//! Binding-scoped retry decisions survive restart; wall time is bounded after clock changes.
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
impl Workspace {
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
        // Only retain a bounded machine code, never an arbitrary remote response string.
        let code = if !code.is_empty()
            && code.len() <= 80
            && code
                .bytes()
                .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
        {
            code
        } else {
            "SYNC_REMOTE_ERROR"
        };
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

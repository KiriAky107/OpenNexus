//! Per-cycle observations are distinct from durable queue and server receipts.
use crate::workspace::{HostError, Result, Workspace};
use rusqlite::{params, OptionalExtension};
use serde::Serialize;

pub(crate) const SCHEMA: &str = "CREATE TABLE IF NOT EXISTS sync_cycle_success(binding TEXT PRIMARY KEY,completed_at INTEGER NOT NULL);";
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    Upload,
    Download,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TransferPhase {
    Sending,
    Receiving,
    Verified,
    Cached,
    Committing,
}
#[derive(Clone, Debug, Serialize)]
pub struct Transfer {
    pub direction: Direction,
    pub path: String,
    pub total_bytes: u64,
    pub bytes_done: u64,
    pub resumed_bytes: u64,
    pub transferred_bytes: u64,
    pub phase: TransferPhase,
}
impl Transfer {
    pub fn new(direction: Direction, path: &str, total_bytes: u64, phase: TransferPhase) -> Self {
        Self {
            direction,
            path: path.into(),
            total_bytes,
            bytes_done: 0,
            resumed_bytes: 0,
            transferred_bytes: 0,
            phase,
        }
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    #[default]
    Idle,
    Handshake,
    Discover,
    Pull,
    Push,
    Complete,
    Failed,
    Cancelled,
}
#[derive(Clone, Debug)]
pub enum Event {
    Phase(Phase),
    Transfer(Transfer),
    Processed(Direction),
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Activity {
    #[serde(skip)]
    pub generation: u64,
    pub running: bool,
    pub phase: Phase,
    pub transfer: Option<Transfer>,
    pub uploaded_revisions: u64,
    pub received_revisions: u64,
    pub started_at: Option<i64>,
    pub finished_at: Option<i64>,
}
impl Activity {
    pub fn start(generation: u64, now: i64) -> Self {
        Self {
            generation,
            running: true,
            phase: Phase::Handshake,
            started_at: Some(now),
            ..Self::default()
        }
    }
    pub fn observe(&mut self, generation: u64, event: Event) {
        if !self.running || self.generation != generation {
            return;
        }
        match event {
            Event::Phase(phase) => {
                self.phase = phase;
                self.transfer = None;
            }
            Event::Transfer(value) => {
                if value.bytes_done > value.total_bytes
                    || value.resumed_bytes > value.bytes_done
                    || value.transferred_bytes > value.bytes_done
                {
                    return;
                }
                self.phase = match value.direction {
                    Direction::Upload => Phase::Push,
                    Direction::Download => Phase::Pull,
                };
                self.transfer = Some(value);
            }
            Event::Processed(Direction::Upload) => self.uploaded_revisions += 1,
            Event::Processed(Direction::Download) => self.received_revisions += 1,
        }
    }
    pub fn finish(&mut self, generation: u64, now: i64, error: Option<&str>) {
        if self.generation != generation {
            return;
        }
        self.running = false;
        self.finished_at = Some(now);
        self.phase = match error {
            None => Phase::Complete,
            Some("SYNC_CANCELLED" | "SYNC_BINDING_CHANGED" | "VAULT_CHANGED") => Phase::Cancelled,
            Some(_) => Phase::Failed,
        };
    }
}
impl Workspace {
    pub fn sync_last_cycle_success(&self, binding: &str) -> Result<Option<i64>> {
        self.check_binding(binding)?;
        Ok(self
            .db
            .query_row(
                "SELECT completed_at FROM sync_cycle_success WHERE binding=?1",
                [binding],
                |row| row.get(0),
            )
            .optional()?)
    }
    pub fn sync_cycle_succeeded(&self, binding: &str, now: i64) -> Result<()> {
        self.check_binding(binding)?;
        if now < 0 {
            return Err(HostError::new("SYNC_TIME_INVALID"));
        }
        self.db.execute("INSERT INTO sync_cycle_success VALUES (?1,?2) ON CONFLICT(binding) DO UPDATE SET completed_at=MAX(completed_at,excluded.completed_at)", params![binding,now])?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn late_or_invalid_observations_cannot_reopen_a_finished_or_new_cycle() {
        let mut activity = Activity::start(4, 100);
        activity.observe(3, Event::Processed(Direction::Upload));
        assert_eq!(activity.uploaded_revisions, 0);
        let mut transfer = Transfer::new(
            Direction::Upload,
            "experiments/a.py",
            10,
            TransferPhase::Sending,
        );
        transfer.bytes_done = 11;
        activity.observe(4, Event::Transfer(transfer));
        assert!(activity.transfer.is_none());
        activity.observe(4, Event::Processed(Direction::Download));
        activity.finish(3, 110, None);
        assert!(activity.running);
        activity.finish(4, 120, Some("SYNC_CANCELLED"));
        activity.observe(4, Event::Processed(Direction::Upload));
        assert_eq!(activity.phase, Phase::Cancelled);
        assert_eq!(activity.received_revisions, 1);
        assert_eq!(activity.uploaded_revisions, 0);
        assert_eq!(activity.finished_at, Some(120));
    }
    #[test]
    fn last_success_survives_reopening_and_cannot_leak_to_another_binding() {
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws
            .sync_bind_download("https://sync.example", "remote", "account")
            .unwrap();
        assert_eq!(ws.sync_last_cycle_success(&binding.id).unwrap(), None);
        ws.sync_cycle_succeeded(&binding.id, 100).unwrap();
        ws.sync_cycle_succeeded(&binding.id, 90).unwrap();
        drop(ws);
        let mut ws = Workspace::open(root.path()).unwrap();
        assert_eq!(ws.sync_last_cycle_success(&binding.id).unwrap(), Some(100));
        ws.sync_unbind(&binding.id).unwrap();
        assert!(ws.sync_last_cycle_success(&binding.id).is_err());
        let next = ws
            .sync_bind_download("https://other.example", "remote", "account")
            .unwrap();
        assert_eq!(ws.sync_last_cycle_success(&next.id).unwrap(), None);
    }
}

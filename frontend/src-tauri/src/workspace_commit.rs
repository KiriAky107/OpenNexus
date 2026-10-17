//! Order only a short SQLite commit against an original native foreground epoch.
use crate::workspace::Result;
use rusqlite::Transaction;

pub(crate) trait CommitGuard {
    fn check(&self) -> Result<()>;
    fn commit(&self, transaction: Transaction<'_>) -> Result<()>;
}

/// Existing trusted callers already own the workspace and their authorization.
/// Native foreground work supplies its original lease instead.
pub(crate) struct UnscopedCommit;
impl CommitGuard for UnscopedCommit {
    fn check(&self) -> Result<()> {
        Ok(())
    }
    fn commit(&self, transaction: Transaction<'_>) -> Result<()> {
        transaction.commit()?;
        Ok(())
    }
}

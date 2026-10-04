# External rename synchronization

Sync discovery compares the current filesystem identities with `sync_observed`,
which records the last locally queued or remotely applied paths. It plans all
new deletions and puts before publishing them to the outbox.

- A rename chain releases each occupied destination first. For `b.md → c.md`
  followed by `a.md → b.md`, the queue sends `put c.md`, then `put b.md`.
- A cycle retains every file ID and uses one temporary put per cycle. A two-file
  swap sends one file to `OpenNexus-sync-rename-<UUID>.<extension>`, moves the
  other file, then sends the first file to its final destination. The temporary
  path uses the destination's parent and extension and must pass normal sync
  scope/path checks. It is also checked against current and queued local paths.
- All final payloads are verified and stored before the complete ordered outbox
  plan and final observations commit in one SQLite transaction. Each temporary
  and final put has its own operation ID. An interrupted send resumes the same
  persisted queue and frozen remote base revisions after reopening.
- The temporary name is never created in the source vault or passed to reference
  rewriting. A receiving vault can briefly show it while the cycle is in flight;
  the final put removes it through the existing durable rename operation. Local
  scans do not echo intermediate remote names as new outgoing changes.
- Receivers still apply ordinary sync v1 puts, verify local hashes and queued
  edits, and reject occupied destinations. An unrelated file at the temporary
  or final destination is preserved as a real conflict. Existing queue entries,
  wire formats, and recovery journals retain their normal behavior.

Windows path comparisons resolve actual filesystem aliases instead of folding
Unicode characters. When NTFS transfers a recently vacated name's creation time
to a renamed file, identity reconciliation also recognizes a changed-case alias
of that vacated name; it still requires the original native file index and the
recorded destination creation time.

Reconciliation indexes the old native file indices once. New native identities
never trigger destination alias resolution. A canonical destination index is
built lazily once if a known source needs a changed-case lookup, then shared
across the batch. Counter-based regressions verify that 4,096 new identities
perform zero destination queries and 1,024 alias lookups resolve each old path
only once, without depending on machine-specific timing thresholds.

Regression coverage in `src-tauri/src/sync_rename_tests.rs` includes rename chains,
two- and three-file cycles with simultaneous edits, SQLite transaction rollback,
reopening after every received revision, interruption after staging/rename/file
commit but before cursor advancement, local edit and destination collision
protection, image attachments, Windows case aliases, and consecutive cycles
behind a previously queued local write.

# Workspace refresh and diagnostics

Desktop Hosts watch the active vault and merge notifications on a 250 ms background tick. Only one background refresh runs at a time; a busy write lock defers it. Switching or closing a vault drops its watcher and hints. On Windows the watcher drains canceled overlapped reads before closing directory handles, including during immediate directory cleanup.

The tree caches stable identities, revisions, hashes and file stamps. A change hint invalidates the affected paths even if file size and modification time are unchanged. Directory hints enumerate that subtree. Unambiguous OS file identities preserve an externally renamed file's identity while the vault is open. Atomic replacement at the same path retains its logical identity. A restart rebuilds the snapshot from disk and the existing identity database.

Events are hints, not a guarantee. Every 30 seconds a metadata scan finds missed creations, removals or changed stamps. Every five minutes a full hash verification also catches lost same-size, preserved-timestamp changes. An overflow or explicit refresh verifies immediately. Focus/visibility resume requests explicit verification. If the watcher is unavailable or fails, metadata checks fall back to two seconds and full verification to 30 seconds. Web mode checks the visible workspace every five seconds and on focus; hidden pages stop requesting work.

The frontend subscribes to `workspace-changed` with a vault ID and snapshot revision. Its 30 second desktop fallback reads a cached snapshot if no disk work is due. The editor rereads its body only when its tree hash changes. Backlinks and folder summaries retain their existing content-hash caches; canvas preview caching is managed separately.

`workspace_watch_status` returns the active vault ID, mode (`watch` or `poll-fallback`), last error, revision and cumulative scan counters. `stats.hashed_files` / `hashed_bytes` count snapshot and sync-discovery hashing, excluding required mutation CAS and payload verification. `last_scan_micros` measures snapshot refresh work while holding the workspace lock. These diagnostics contain no document bodies or credentials. `workspace_tree` accepts `force: true` for an explicit full rescan.

Sync discovery caches validated digests with file stamps and bounded age; hints invalidate them. Changed sync payloads are still copied and verified against their digest. Write, move, delete, folder operations and recovery continue to hash actual disk contents before commit. Cached revisions and notifications never authorize a mutation.

Synthetic performance/regression fixtures live in `workspace::watch::tests`. The mixed fixture contains Markdown, Canvas, ordinary images and an immutable attachment. Run `cargo test --lib --locked workspace::watch:: -- --nocapture` to record the same-input full-scan baseline, metadata checks, idle checks and read counters without using a real knowledge vault.

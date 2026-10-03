# 0.5.9-beta2 validation record

This record covers the beta2 fixes and performance changes. Native checks use
isolated synthetic vaults or copies of demonstration files, with their own
application storage and WebView profile.

## Automated coverage

The complete backend suite passed 1,176 tests on Windows. The frontend suite
passed 765 tests in 144 source test files, excluding ignored local audit
fixtures that are absent from clean CI checkouts. Type checking and the
production Vite build passed. The frontend output includes existing refused
requests to a local development server from component fixtures; no test failed.

The Windows Rust library suite passed 125 tests, with 12 ignored and the
unchanged production-KDF B04 crash test filtered out. Formatting and Clippy
with warnings denied passed. S02 was also executed separately and passed
80 real process terminations across its four persistence boundaries. Ignored
tests and the filtered test are not counted as passes.

Targeted regression coverage includes:

- Canonical paths across note links, AI citations, anchors, tabs and tree refresh.
- Typing during delayed reads and saves, navigation cancellation, returning to
  the current document, save failures and callbacks from disposed editors.
- Concurrent chat edits and regeneration through the actual request handler,
  transaction rollback, exact parent chains, cancelled streams and vault scope.
- Benchmark migration from beta1, complete reports after restart, atomic case
  progress, cancellation during persistence, failed writes and SSE closure.
- Background highlighting, stale replies, bounded queues and caches, Unicode
  offsets, multiline syntax and all 242 bundled grammars in both GitHub themes.
- Native filename and directory case changes, reused deleted names, swapped
  paths, missed notifications, restart identity, ambiguous hard links and
  transaction rollback. Schema 14 is backed up before the schema 15 upgrade.
- Two-vault sync after a directory case change, interrupted after its first
  child, then restarted and resumed without local echo or deletion. Recovery
  also preserves a sibling edited through atomic file replacement.

See [document navigation](document-navigation.md), [chat windows](chat-windows.md),
[benchmark persistence](benchmark-summaries.md) and
[editable highlighting](code-highlighting.md) for the implementation details.

## Performance measurements

The same-machine Benchmark comparison used the real service and SQLite, with
synthetic 1,024-character queries and an empty retrieval response. Cumulative
SQL string parameters decreased from 12.947/49.656/302.471 MB to
0.315/0.621/1.542 MB for 100/200/500 cases. Each input case was serialized once,
and each run had at most one pending background write. These are parameter
counts, not physical disk writes. The full fixture and measurement method are
described in [benchmark persistence](benchmark-summaries.md).

Native highlighting measurements and package verification are recorded after
the versioned desktop candidate is built and exercised.

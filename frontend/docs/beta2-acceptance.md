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

On Windows 11 build 26100 / WebView2 154.0.4258.53, the published beta1 Host
and versioned beta2 Host ran the same TypeScript fixture, with 12 consecutive
real CodeMirror transactions per size:

| Lines | beta1 mean transaction | beta2 mean transaction | beta1 next-frame P95 | beta2 next-frame P95 | Main-thread tasks over 50 ms, before / after |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 19.30 ms | 1.02 ms | 27.9 ms | 10.8 ms | 0 / 0 |
| 1,000 | 181.61 ms | 0.84 ms | 269.6 ms | 11.2 ms | 12 / 0 |
| 5,000 | 881.28 ms | 0.67 ms | 963.4 ms | 13.2 ms | 12 / 0 |

These are one-machine WebView observations, not OS keyboard latency or a
performance guarantee. The actual production Worker loaded and produced
colored tokens before each measurement; input text and complete saves were
checked. Background full-document tokenization still takes time, as recorded
separately by the Node benchmark in [editable highlighting](code-highlighting.md).

## Native feature checks

The versioned release Host/Core passed checks using a copy of 22 original Demo
documents plus synthetic cases. Original files and the application storage
pointer were verified unchanged, and owned processes were closed. Coverage:

- Mounted editor input while native navigation is pending, correct save target,
  and cancellation when returning to the current document. Deterministic
  delayed-read and delayed-save cases are covered by store regressions.
- Native watcher filename case changes and reuse of a deleted destination,
  retaining the source ID and one tree entry.
- Actual note-body Ctrl-click and the mounted AI citation handler, with a
  synthetic citation payload, canonical tabs and no false moved-file notice.
- Exact long CRLF reference edits during rename, external refresh, AI preview
  approval and history restore using the mock provider and real Host writes.
- Canvas group keyboard movement, one undo, save and unknown fields; three
  themes, inspector toggles, focus mode, narrow layout and backlink focus return.
- Saved chat search, two actual RAG runs, comparison and complete reports after
  restarting the Host/Core. No page errors were reported.

Canvas fixtures use the bundled logo for visible image checks. A repeated black
one-pixel placeholder was removed from native acceptance data; ordinary Canvas
backgrounds continue to follow the selected theme. Existing README Canvas and
folder screenshots retain the collapsed file tree.

## CI and Windows packages

[CI on c185187](https://github.com/KiriAky107/OpenNexus/actions/runs/37138854069)
passed all five jobs: documentation, backend, frontend, Linux Rust and Windows
Host. Linux Rust passed 91 tests, with 4 ignored and 1 filtered. Windows
workspace tests passed 38; sync passed 29, with 2 ignored. Ignored and filtered
tests are reported separately.

The local GNU release binary was built from product code/version commit
`c185187abb5dfcfd5f751c8f7b6e0103d3e799bd`. Subsequent changes cover validation
documentation and the native test image; source archives use the final release
commit. The installation payload was independently extracted and exercised:

- Installer: `OpenNexus_0.5.9-beta2_x64-setup.exe`, 103,200,050 bytes,
  SHA-256 `98cb8a66cf743430db1ed20a33309c0524d62d77b02494e5566a727f741465a6`.
- All 2,166 Core files and hashes, embedded Host manifest and dependency lock
  matched. Runtime Core, Host, frontend and Cargo lock identify `0.5.9-beta2`;
  the Python lock uses `0.5.9b2`. Frozen Core startup, budget continuation and
  duplicate approval with one tool execution passed.
- Extracted-payload native startup passed on Windows 11 build 26100 /
  WebView2 154.0.4258.53 with System32-only PATH and no developer Python settings.
  Private storage, Canvas, inspector, backlinks, exit and the dynamic Loader
  missing-DLL negative control passed; test files and storage pointer matched.
- SDK 0.38.2 supplied the x64 `WebView2Loader.dll`, SHA-256
  `8427b1fc58ec707813e5c0a51eb5d69397bb333250a7b891be4d3b123f1e0f1c`.
  Its Microsoft signature and the embedded Runtime bootstrapper were verified.

The [clean-checkout Windows package job](https://github.com/KiriAky107/OpenNexus/actions/runs/37138855793)
also passed on Server 2022 build 20348 / WebView2 131.0.2903.86. MSVC uses the
static Loader; its installer hash is
`44e164984c23604503422542d498866c9fd0ad97adbdb9e29f19f64bdfe69a0b`.
It verified the same Core file count, actual runtime versions, Microsoft
components, private storage, Canvas and exit. GNU and MSVC hashes are separate.

See [Windows package verification](windows-package.md) for the shared commands.

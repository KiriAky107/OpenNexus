# 0.5.9-beta2 validation record

Native checks use isolated synthetic vaults or copies of demonstration files,
with their own application storage and WebView profiles. The original Demo and
user storage settings are preserved.

## 2026-10-04 update

The update fixes external-body refresh retries, real citation/search passage
navigation, batch rename synchronization, literal Canvas filenames, interrupted
chat tool states, asynchronous chat persistence, and background preview highlighting.
Product code for the update is fixed at
`3ef7c932575281be4a95dfc4aec95b61721d88ff`.

### Automated checks

- The complete Windows backend suite passed 1,199 tests. Finalization tests
  exercise a real SQLite write lock, repeated task cancellation, AnyIO level
  cancellation, shutdown draining, cross-vault scope and write-failure SSE order.
- The local frontend suite passed 816 tests in 147 files. A subsequent 31-test
  navigation/refresh regression run covers the final body-hash revision checks,
  including an independent tree refresh and unmount/remount after a failed read.
  Type checking and the production build passed. The subsequent clean-checkout
  GitHub CI run after the focus fix passed all 819 tests in the same 147 files.
- Native checking then exposed a first-mount visual-editor scroll omission:
  the selected passage was correct, but ProseMirror ignored the scroll request
  while its DOM selection still belonged elsewhere. The fix waits for visible
  Vue layout and focuses before dispatch. Its new regression fails on the old
  loading/focus order; the 57-test editor/navigation run and type checking pass.
- Windows release Rust passed 139 tests with two test threads; 14 tests were
  ignored and the unchanged B04 production-KDF crash test was filtered out.
  Formatting and Clippy with warnings denied passed. Two existing extension
  containment tests timed out in the initial high-concurrency run; both passed
  individually and in the complete two-thread rerun, without assertion changes.
- The ordinary S02 recovery test passed 80 real process terminations. A second
  S02 test passed 80 terminations during a filename swap: 20 each after staging,
  rename, file commit and cursor commit. Reopening preserves the frozen outgoing
  job, edited contents, file identities and final paths without a local echo.
- The bilingual release-note checker passed. Negative controls reject missing
  English details and stale download URLs. Documentation links also passed.
- The rebuilt frozen Core contains 2,166 files. Isolated startup, budget pause,
  continuation, duplicate approval and single tool execution passed.

Citation fixtures come from the real Core block parser, including UTF-16
positions, CRLF, frontmatter, emoji, formatted text, lists, quotes, code and
repeated paragraphs. Component checks inspect real CodeMirror decorations and
Milkdown selections rather than replacing editor positioning with mocks.

### Native navigation, Canvas and refresh

The final product Host, SHA-256
`67eb675ab53cb38fa166f80d077f7fea1161ee3bc8a2f9113becb6b3e3be3b60`, passed
the following checks in a fresh synthetic vault:

- Real citation-card clicks and real FTS search-result clicks select the exact
  target passage in both editors and scroll it into view. Source offsets were
  3434–3482; rendered ProseMirror offsets were 3398–3442. Outdated block IDs
  display the recovery notice and return to the start of the file.
- Canvas creates, saves, previews and opens `C# lesson.md`, `100%.md` and
  `literal%2F%23.md`, retaining the independent heading fragment. Literal image
  filenames also work as node previews and group backgrounds.
- Three controlled `workspace_read` IPC errors exercise retrying an already
  observed tree revision, a separate caller refreshing the tree first, and
  unmounting/remounting AppShell after a failed read. Removing the fault restores
  the real body; idle checks cause no additional body reads. Only the IPC failure
  response is injected; files, tree hashes, stores and editors are real.
- No page errors or AI generation calls occurred. Only test-owned processes
  were stopped and the original application storage pointer was restored.

[CI for the final product commit](https://github.com/KiriAky107/OpenNexus/actions/runs/37206192085)
passed all five jobs: frontend, backend, Linux Rust, Windows Host and documentation.

### Final Windows installer

The GNU installer built from the final product commit is 103,208,677 bytes,
SHA-256 `7d33ad7249c2090940c277e5a51b142947c2a67401e1e748f550963fb46b9d82`.
The final package was extracted and verified independently of the build folder:

- All 2,166 Core files match the manifest embedded in the Host; the dependency
  lock and Host/Core runtime versions match `0.5.9-beta2`.
- The x64 SDK Loader and embedded Runtime bootstrapper have valid Microsoft
  signatures. Loader SHA-256 remains
  `8427b1fc58ec707813e5c0a51eb5d69397bb333250a7b891be4d3b123f1e0f1c`.
- The extracted Host starts and exits with System32-only PATH on Windows 11
  build 26100 / WebView2 154.0.4258.53. Canvas, private storage, unchanged vault
  documents, restored storage pointer and the missing-Loader negative control
  pass. Its Host hash matches the native navigation run above.

A packaging retry was needed because an early native test held the Host open
during the bundler's final write. Repeating only the bundle step with no running
Host completed successfully. The repeated-patch warning was checked against the
actual binary's `__TAURI_BUNDLE_TYPE` string pointer: it already contained the
NSIS marker, as defined by the installed Tauri source. Final payload verification
then passed; no source recompilation or test assertion change was needed.

The [clean-checkout Windows package workflow](https://github.com/KiriAky107/OpenNexus/actions/runs/37206212516)
also passed for the final product commit. Its MSVC package verified all 2,166
Core files and native startup on Server 2022 build 20348 / WebView2 131.0.2903.86.
That separate MSVC installer has SHA-256
`064409655f4900ab57fe32f51f866fa9d8107a88afea9f548629be72e8c0d1f9`;
the downloadable GNU installer uses the hash stated above.

### Preview Worker measurements

The production preview Worker produced HTML byte-for-byte identical to the
synchronous dual-theme Shiki result for 1,000 and 5,000 TypeScript lines. Three
samples per size separate main-thread message posting from background work:

| Lines | Synchronous Shiki CPU median | Worker post median | Worker round-trip median |
| --- | ---: | ---: | ---: |
| 1,000 | 971.53 ms | 0.197 ms | 924.20 ms |
| 5,000 | 8,685.82 ms | 0.429 ms | 8,754.59 ms |

These Windows / Node 24.12.0 measurements are not browser input latency. The
first 5,000-line run exceeded the harness's 30-second deadline while the full
Core build and test suites were running; it passed when repeated after those
jobs ended. Markdown parsing, sanitization and DOM updates still run on the
main thread. See [the reproducible preview harness](code-highlighting.md).

In the native WebView, the unchanged preview code was also exercised in a real
streaming chat component with 100, 1,000 and 5,000 lines. The first text appeared
after 74.6 / 118.1 / 309.1 ms; colors arrived after 489.1 / 1,130.5 / 5,674.5 ms.
All rendered lines and copyable source matched, the small unchanged block
required no extra Worker job, and delayed results never replaced the final text.
No page errors occurred. This run used candidate Host SHA-256
`64523e5ea1ca7e9cb1e5474393534b26738681a8435aa032b56b75363a15356a`, before
the independent visual-editor focus fix.

The largest fixture still produced a 1,880 ms main-thread task while processing
preview HTML/DOM; moving Shiki to a Worker does not eliminate that separate cost.
This is a remaining opportunity for incremental DOM rendering, rather than a
claim that large previews have no frame stalls.

## Earlier beta2 build

The following measurements and package hashes describe the original beta2
build, retained for comparison with the update above.

### Automated coverage

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

### Performance measurements

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

### Native feature checks

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

### CI and Windows packages

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

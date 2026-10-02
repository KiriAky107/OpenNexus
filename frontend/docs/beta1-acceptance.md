# 0.5.9-beta1 acceptance record

This record distinguishes feature verification from installation acceptance.
Checks below were run on the beta1 implementation before the version bump;
the final versioned installer must pass package and native startup verification
again. Windows 10/11 installation scenarios remain pending until explicitly
completed. The screenshots use demonstration vault copies with their file tree
collapsed for Canvas and folder views.

## Automated and native coverage

[CI on fdf454a](https://github.com/KiriAky107/OpenNexus/actions/runs/37058846688)
passed documentation links, backend (1,144 passed, 3 skipped), frontend
(720 tests across 140 files), type checking, production UI build, Rust tests,
formatting and Clippy. The skips are reported as skips, not passes.

Native feature acceptance used isolated storage and copies of demonstration
vaults on Windows 11 x64 build 26100, WebView2 154.0.4258.48. It covered:

- Exact reference edits in a long CRLF note, preserving code, titles, anchors,
  and unrelated bytes; rename/move review shows every proposed edit.
- AI preview approval, restore history, durable receipt reconciliation after a
  later restore, and rejection of the second of two stale Agent previews.
- External file changes, folder views, saved conversation search and branch
  navigation, actual RAG runs and report comparison, and restart persistence.
- Canvas grouped keyboard movement, one-step undo, save, unknown-field
  preservation, inspector toggles, backlinks, keyboard activation and focus
  return; light, dark, sepia, focus mode and a 700 × 720 window.
- Two native Host/Core processes synchronizing Markdown, an image and Canvas
  through a controlled local server; stable file identity, return edits and
  vault-scoped benchmark history were checked.

Original vault content and the product storage pointer were unchanged after
these tests. Owned test processes were closed. The native repeated-crash
acceptance also passed: S02 used 80 process kills across four persistence
boundaries; B04 used 100 across five boundaries with the production KDF.

## Performance evidence

These are observations with the same synthetic input on one machine, not
latency guarantees. Algorithm counts and actual desktop timings are separate.

| Scenario | Previous implementation | Beta1 implementation |
| --- | --- | --- |
| Five scans, 114 mixed files | 570 hashes, 49,234,495 bytes read | Metadata checks, 0 content bytes read |
| 10,000-message conversation | 10,000 body rows, 31,737,780 bytes, 148.650 ms | 60 body rows, 211,290-byte JSON payload, 38.347 ms |
| 2,000 nodes / 4,000 edges, 90 drag frames | 8,004,000 identity lookups | 2,000 indexed identities, 8,000 indexed endpoints, 360 affected-edge calculations |
| Native Canvas drag, same scene | Mean 52.93 ms, P95 80.1 ms; 43 frames over 50 ms | Mean 10.17 ms, P95 10.1 ms; 0 frames over 50 ms |
| 15 benchmark reports / 3,000 cases each | 15 full-report reads, 431.821 ms | 0 full-report reads, 5,160-byte summary payload, 20.307 ms |

Conversation queries still traverse branch metadata when locating a window;
full saved context is reconstructed when generating a reply. Canvas heap
snapshots do not prove absence of leaks. Workspace changes have bounded fallback
scans in addition to native notifications. Details and limits:
[chat windows](chat-windows.md), [Canvas performance](canvas-performance.md),
[benchmark summaries](benchmark-summaries.md).

## Windows package coverage and pending matrix

The [clean-checkout Windows build on fdf454a](https://github.com/KiriAky107/OpenNexus/actions/runs/37058847454)
passed actual NSIS extraction and native startup on Windows Server 2022 x64
build 20348, WebView2 131.0.2903.86, MSVC with a static Loader. The local GNU
installer passed on Windows 11 build 26100 with a dynamically imported Loader,
including the missing-DLL negative control. Both verified 2,166 Core files,
the embedded manifest, dependency lock, SDK DLL and Runtime bootstrapper.
Host started with only System32 on PATH, without developer Python variables.
These pre-version checks do not establish the final beta1 package identity.

| Installation scenario | Independent Windows 10 x64 | Independent Windows 11 x64 |
| --- | --- | --- |
| First installation with existing Runtime, launch and exit | Pending | Pending |
| In-place upgrade from alpha2 | Pending | Pending |
| No Runtime, connected installation | Pending | Pending |
| No Runtime, offline failure handling | Pending | Pending |
| Uninstall | Pending | Pending |

No isolated Windows 10/11 installation environment was available for that
matrix. Server runner startup and local extracted-payload startup are recorded
separately and do not replace it. The OpenNexus Host and installer are unsigned;
the SDK DLL and embedded Runtime bootstrapper have verified Microsoft
signatures. The bootstrapper needs network access to install a missing Runtime.
See [Windows package verification](windows-package.md) for commands and scope.

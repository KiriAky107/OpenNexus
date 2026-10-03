# Editable code highlighting

CodeMirror owns text, selection, composition and undo. Its input transactions map the current visible decorations and schedule highlighting after 30 ms without another edit. They do not tokenize or flatten the complete document. The initial highlight is also deferred. Plain text stays available while colors are loading or if highlighting fails.

One module Worker loads Shiki grammars and the GitHub themes on demand. It tokenizes the entire source so off-screen comments, strings and embedded language states remain correct. It returns transferable UTF-16 range triples and a deduplicated style palette. The main thread builds decorations for the visible ranges using binary search; scrolling reuses the same result without another tokenization. Markdown preview HTML keeps its existing bounded cache and shares the DOM-independent grammar loader within its own realm.

The broker sends one job at a time. Each editor has at most one pending snapshot, replaced by its latest edit; the global pending queue is limited to 32 jobs and 4,000,000 UTF-16 characters. Excess old jobs fall back to plain text. The cache holds at most 32 results and 1,000,000 estimated bytes, excludes sources longer than 16,000 characters, and includes language and theme in each key. A single job supports up to 1,000,000 characters and 250,000 spans; larger inputs remain fully editable without highlighting. A 15-second deadline terminates an unresponsive Worker, with a five-second retry backoff. Idle Workers and their cache are released after 60 seconds.

Replies are bound to an editor instance, document object and revision. New edits, language/theme reconfiguration, and destruction cancel pending callbacks. A stale reply cannot add decorations to a newer document. Highlight effects do not enter undo history. Tests cover delayed replies, cancellation, queue/cache limits, worker failure, Unicode/CRLF offsets, off-screen multiline grammar, selected text and undo/redo, plus all bundled language grammars with both themes.

## Reproducible measurements

After `pnpm build`, run `node scripts/highlight-benchmark.mjs 1000 5000`. The fixture repeats the same TypeScript export line used for native WebView measurements. The script compares the beta1 full-document tokenization/decoration computation against the built production Worker's message transport and computation for initial loading, one-character edits, 200-line paste, undo/redo, theme and language changes. It uses three samples per case and reports medians, span counts and transferred range bytes. Worker round-trip time includes background computation; it is not input latency.

Native WebView validation records real CodeMirror transaction time, time to the next frame and main-thread tasks over 50 ms with the same input before and after this change. Browser measurements belong in the beta2 acceptance record alongside the actual Host build and WebView version.

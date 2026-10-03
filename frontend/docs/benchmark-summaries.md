# Benchmark summaries and comparisons

Run lists and individual status requests read `summary_json`, with frozen
`dataset_cases` omitted. Migration v24 derives summaries from existing run
snapshots without changing their full evidence. Full configuration, cases and
runtime identities remain available through the report endpoint and JSON export.
Terminal list entries never load or validate `report_json`, and historical reports
are not added to the live task cache.

Only orphaned queued/running records are recovered after restart. Their partial
case events produce an interrupted report, with the transition and terminal event
in one transaction. Recovery happens before state filtering; concurrent readers
cannot generate duplicate terminal events. Lists preserve vault scope, filters,
stable ordering and pagination, including legacy process-local entries.
Database reads/recovery run in worker threads with the request context propagated;
live asyncio task queues and cancellation flags stay on their owning event loop.

The view fetches the first history page once. Every 1.5 seconds it requests only
the summaries of visible-history active runs, including older loaded entries.
It stops when those runs finish or the document is hidden; returning to the page
refreshes the first history page. Manual refresh discovers runs created elsewhere
while idle. Requests do not overlap within a vault, and late previous-vault or
unmounted responses cannot replace current state.

Comparisons require different complete runs from the same vault, evaluation type,
dataset ID and content hash; the returned reports are checked against these
identities again. Quality changes, time/cost and evidence changes are shown
separately. Agent checks, tool selection/argument accuracy and invalid calls count
as quality, including partial regressions when both overall results failed.
Accuracy uses `max(actual_calls, expected_calls)`, matching aggregate scoring;
invalid-call rate uses actual calls. Higher quality is better except invalid-call
count/rate. Time/cost are displayed independently and do not enter the quality
regression filter. Missing/non-finite values and zero denominators remain unknown.

Known runtime IDs are excluded from business/configuration changes. Object key
order also does not change a result. Retrieval targets, errors and other evidence
remain comparable, and the complete raw pair retains identities for audit.

The repeatable synthetic test is `backend/tests/test_benchmark_summaries.py`:
15 reports with 3000 valid RAG results each. On the local Windows 11 development
machine, the old equivalent terminal list decoded 15 reports (13,409,475 bytes)
in 431.821ms; the new list read zero reports and 5160 summary bytes in 20.307ms.
These are single-pass timings on identical fixture data, not a performance
guarantee. Structural assertions enforce zero full-report reads independently
of timing. Additional tests cover v23 migration, restart recovery, concurrency,
filters/pagination, identity-only changes, partial checks and unavailable values.

## Incremental execution persistence (0.5.9-beta2)

Migration v25 moves frozen configuration, including the input dataset, into
`benchmark_inputs`. Large inputs therefore do not share the row updated after
each evaluated case. `run_json` retains a small initial snapshot; `summary_json`
contains current progress and state. Reports omit the duplicate input dataset
on disk and restore it on reads and exports. Existing beta1 runs, complete
reports and interrupted evidence migrate in the same transaction without
changing their public representation.

RAG, single-agent and collaboration runs await one worker-thread write at each
case boundary. There is at most one pending persistence operation per run
(the existing active-run limit is 100); evaluation cannot outrun the writer.
Worker calls preserve the originating Vault context. Each case event and its
progress update commit together; the terminal report, status and terminal event
also share a transaction. Polling and SSE expose updates only after commit.
Identical event retries are harmless, while conflicting or out-of-order
sequences are rejected before progress changes. Cancellation drains the pending
write before recording the terminal event. A failed writer closes subscribers
without inventing an uncommitted event; remaining durable evidence can be
recovered by the interrupted-run path.

`backend/tests/test_benchmark_incremental.py` covers these transitions, actual
AgentRuntime and collaboration execution, Vault isolation, beta1 migration,
rollback, failed writes, cancellation during writes and linear SQL payload size.
The scale fixture uses 1,024-character queries and an empty retrieval response,
with the real service, RAG runner and SQLite. Its 200/500-case typed datasets
deliberately bypass the existing 100-case import limit for scale testing.

On 2026-10-04, an isolated same-machine comparison loaded the published
`v0.5.9-beta1` service/storage source and executed the same fixture against both
versions. These numbers count cumulative UTF-8 **SQL string parameters**, not
physical disk writes:

| Cases | beta1 parameter bytes | beta2 parameter bytes | beta1 serialized input cases | beta2 serialized input cases |
| --- | ---: | ---: | ---: | ---: |
| 100 | 12,946,805 | 315,388 | 10,400 | 100 |
| 200 | 49,655,763 | 620,997 | 40,800 | 200 |
| 500 | 302,471,323 | 1,542,466 | 252,000 | 500 |

The paired runs issued 206/406/1,006 write statements respectively: the change
reduces payload size and moves writes off the loop rather than batching away
case durability. The new per-run pending-write peak was one, with zero writes
executed on the event-loop thread. A 1ms asyncio heartbeat measured p95 excess
delay of 23.377/21.360/30.172ms for beta1 versus 4.507/6.283/4.396ms for beta2.
These are single instrumented passes affected by scheduling, not a UI latency
guarantee. Deterministic assertions check the queue bound, commit ordering and
linear payload growth separately from timing.

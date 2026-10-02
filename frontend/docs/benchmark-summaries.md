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

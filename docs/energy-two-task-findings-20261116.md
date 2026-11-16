# November 16 scheduled daily-report outcome comparison

The operator supplied `log(28).txt` from the exact-two-chat read-only inspector.
Its execution-event coverage for both selected runs is complete; no raw article,
tool command or provider credential is present in the exported diagnostic.
Original operator output and a factual comparison are archived in
`release/energy-two-task-comparison-20261116-r1/`.

| Chat | Queue wait | Agent runtime | Business outcome | Native Work configuration |
| --- | --- | --- | --- | --- |
| `9214a4fc-4cff-4599-9855-ea0af1d1ee27` | 59m33s | 304.139s | No accepted build or successful file-publication receipt | Not configured; no outbox entry |
| `945b81c9-225f-4a12-a033-19d13da92903` | 52m32s | 315.004s | Accepted PNG/HTML/JSON/TXT, followed by successful `present_files` | Configured; outbox `sent` |

Both prepares use Skill 2.1.6, publication date 2026-11-16 and target data date
2026-11-15. They report the same candidate counts (not proof of identical article
content): zero market candidates, five company candidates, 14 original-page
articles, zero available financial quotes and eight dated freight references.
Both therefore enter `NEWS_SUPPLEMENT_REQUIRED`. The successful run later reaches
`SUMMARY_BATCH_READY` and `REPORT_ACCEPTED`; PNG rendering takes 3.135 seconds.
The missing quote values do not block that accepted four-file generation.

The other run has five explicit failed import receipts: original body unavailable
at event 34, invalid source URL at 46, and invalid news time at 52, 56 and 60.
Six import-news command proposals are counted, but the no-error/no-status receipt
at 36 is not evidence of a successful import. There is no accepted build or
successful `present_files` receipt in its complete selected-kind timeline.

The deployed `import_news` checks HTTPS URL shape, source label, parseable time
inside the seven-day cutoff, project-local original evidence and nonempty body.
`NEWS_TIME_INVALID` alone does not distinguish missing/unparseable time from an
out-of-window time. The diagnostic does not export the original submitted field
values, so this record does not assert a particular malformed date value or
relax evidence requirements. The differing later paths expose the fragility of
model-authored supplement metadata; they do not establish that valid market news
was unavailable globally.

There are two additional, independent product issues:

1. Both durable runs and scheduled occurrences are recorded as `success`, despite
   one having no completed report. The observed scheduler service maps a normal
   agent-run completion to success; this is not a business-delivery acceptance
   check. Future work should consume an explicit Skill-owned outcome/receipt
   rather than duplicate news schemas or infer business success from prose.
2. The unsuccessful task has no native Work connection/binding configured. The
   successful task has both, and its stored preview retains the reader link;
   current formatting yields the expected link-only notification and outbox
   status is `sent`. A webhook mentioned in a prompt is not a native connection
   binding. A failed/no-report task's notification is not retried by this analysis.

As of this observation Gateway remains generation 418, with 70 ready/updated
replicas and fresh config `max_concurrent_runs=3`, `multi_instance=false`,
`recursion_limit=10000`. This is not evidence that the prepared 32-concurrency
release was activated. Raising concurrency addresses the lengthy queue wait;
it does not repair invalid supplemental evidence or absent notification binding.

This turn archives and analyzes the operator's evidence only. It does not modify
the Skill, schedule targets, task history, Gateway configuration, or Work outbox,
and does not replay either task or send a message.

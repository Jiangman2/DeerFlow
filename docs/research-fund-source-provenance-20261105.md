# November 4/5 financial Skill source provenance

`release/research-fund-source-20261105/SOURCE.json` seals the already executed November 4/5 financial Skill updates independently of the enterprise application releases. It binds the current 30 Skill files to the November 5 candidate manifest and operator report, preserves all 39 existing archive files byte-for-byte, and registers the 10 related repository tests, template reviewer, and delivered operator tools.

The deployed Skill tree is `6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951`. Operator evidence confirms the file replacement and five Gateway node views; real user report-generation acceptance remains pending. These checks make no cluster, database, provider, or production calls. Do not rerun the archived update or cleanup commands.

The October 27 lock, inventories, and receipts are unchanged. The new verifier first validates the current source and evidence, then reads the original 29-file archive embedded in November 4's sealed `old-bundle.json` to verify the October 27 records. Archive members are read in memory and never extracted or executed. The original November 4 `INCOMPLETE` update receipt remains intact; its successful cleanup is a separate receipt. The November 5 reconstruction record explicitly distinguishes the exact recovered operator report from a direct server download.

Run with Python 3.12 or newer:

```sh
python -B deploy/enterprise/verify-research-fund-production-source.py --ref HEAD
python -B deploy/enterprise/test_research_fund_source_20261105.py
python -B release/research-fund-feedback-20261105/nov05_release_template_test.py
```

Before committing, `--worktree` checks local bytes instead of Git objects. The default remains committed `HEAD`. Skill Review CI runs the committed check, rejection tests, 18 synthetic release-template checks, and all nine repository financial test files plus the backend financial Skill tests. Original operator tools are evidence only and are never invoked by this workflow.

The exact byte-preservation attributes cover the two feedback archives, the five delivered operator tools, and `scripts/review_research_fund_templates.py`. The reviewer contains original CRLF bytes sealed by the test adaptation record; normalizing that file would invalidate its recorded hash. Other source paths retain the existing repository attributes.

The PR base is enterprise commit `459b44eae00e671466c90fc20462dee018b6a0c0`. Its existing October 29, November 1, and November 2 verifiers reject `frontend/src/core/settings/store.ts` with `NOV02_PROTECTED_SOURCE_CHANGED` before any financial changes. The same October 29 check passes at its parent `d63a01c9b`. That separate frontend provenance issue is outside this financial Skill registration. No frontend source, historical application inventory, or historical acceptance claim is changed here.

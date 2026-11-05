# November 5 financial Skill successor source chain

This registration extends the earlier November 4/5 archive with two already
released shared-Skill updates. The enterprise PR contains the current 30-file
package, not a new production rollout.

| Stage | Skill tree SHA256 | Operator-reported status |
| --- | --- | --- |
| Earlier November 5 | `6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951` | Accepted |
| Round3 feedback | `0d935a5e5c45e8bf916b6b52ee310826d68acb22fffb0cbc70ab5d3304840a4c` | Accepted on five nodes; maintenance Pod removed |
| Project type text | `28944ec28d4644acb09cd10eb8e73dc546a4250d0c21172d325b23cb713d7d43` | Accepted on five nodes; maintenance Pod removed |

Round3 aggregates the external-testing child reasons, computes the designated
blank summary cells, removes the company report's acceptance-date prerequisite,
and removes the indirect-expense explanation sentence while retaining its data.
The final update preserves arbitrary nonblank Appendix 1 project-type text on
the cover. Existing case files keep their previous interpretation until reimport.

`SOURCE.json` seals the two release archives, the supporting tests and the
previous versions of two supporting files. The verifier validates the current
package against the archived updater payloads before projecting the earlier
source view into its unchanged source registration. The October 27 inventory,
the original failed-cleanup receipt, and earlier accepted releases stay intact.
The updater payloads are parsed as literal data; they are never imported or run.

Verification for this PR:

```sh
python -B deploy/enterprise/verify-research-fund-production-source.py --ref HEAD
python -B deploy/enterprise/test_research_fund_source_20261105.py
python -B deploy/enterprise/test_research_fund_late_source_20261105.py
```

Local checks on the final source passed: 334 financial tests (327 Skill tests
plus seven backend Skill tests), 22 earlier source rejection tests, 15 successor
rejection tests, and the 29 Round3 / 18 earlier release-template tests. GitHub CI
also verifies the committed merge tree and scans the changed public Skill.

The archived terminal JSON captures are operator-supplied evidence. Their local
byte hashes differ from the server REPORT.json where terminal links were
formatted as Markdown; server report hashes are retained as reported values,
not independently downloaded-file claims. Actual financial-report user
acceptance remains pending. No production deployment, database mutation, model
request, or business validation is performed by this source verifier.

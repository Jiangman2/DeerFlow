# 2.0.3 news-handoff candidate — not deployed

The candidate at `portable-skills/candidates/energy-market-daily-2.0.3` starts
from the exact 106 files in the accepted 2.0.2 public-code ZIP. The current
`portable-skills/energy-market-daily` directory matched that ZIP byte for byte
before preparation and remains unchanged. The original archive is not rewritten.

Only `scripts/daily.py`, `SKILL.md` and the candidate's release manifest differ.
`import-news` and `select` retain their existing receipt fields and add
`research_file`, `news_file`, `publication_date`, `news_as_of`, and the correctly
quoted `build` `next_command`. News selection, validation, data acquisition,
calculations and rendering remain unchanged. The candidate version is 2.0.3 and
continues to accept prepared projects from 2.0.0, 2.0.1 and 2.0.2.

The instructions now use the already-generated summary batch after a successful
news import. Another `select` is needed only to change article IDs. No scheduler
budget, webhook, production configuration or historical result is changed.

`test_candidate_receipts.py` exercises the real CLI against local approved news
fixtures. Its three tests cover the old receipt fields and identical automatic
batch, metadata/file consistency, literal workspace quoting, explicit ID order,
and rejection of duplicate IDs. They run neither `build` nor any provider call.
Windows: 3 passed. Linux in a network-disabled, read-only source container:
3 passed. Temporary fixture projects were removed normally.

`CANDIDATE.diff` is the complete three-file diff. `CANDIDATE.json` records the
baseline identity, candidate inventory, archive hash, tree-hash definition and
test results. The new candidate ZIP is a review artifact, not an activation
package or proof of production acceptance.

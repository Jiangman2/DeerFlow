# Financial report numbering and cover label

The group-company report now numbers the outer entries in “科技项目（课题）资金结余及原因” as `1.` / `2.`. The numbered reasons inside an entry keep their existing parentheses. Each of the three supported report profiles adds a small, black, 9 pt “AI自动生成” label at the bottom right of its cover.

This is a successor to the accepted November 5 project-type release. Its production before-tree is `28944ec28d4644acb09cd10eb8e73dc546a4250d0c21172d325b23cb713d7d43`. The work starts from enterprise commit `2d6b9a7ed95c6a9a01114110a38585576c467451`. Historical source inventories, original reference documents and prior operator receipts are unchanged.

## Scope

- Five existing Skill files change: `SKILL.md`, `references/group-company.md`, `references/layout-contract.md`, `scripts/group_build.py` and `scripts/template_builder.py`.
- One Skill module is added: `scripts/report_display.py`.
- Financial calculations, validation, imported project-type text and tax rules retain their current implementation.
- The cover has its own first-page footer. Original footer parts and page fields remain intact; later sections do not repeat the label. The MIIT cover retains its page number, shifted slightly upward by the separate label line.
- Fidelity inspection compares the exact expected new footer, relationship, content type and designated numbering change, and still rejects missing, extra or duplicate package parts.

## Validation

The full financial regression suite passed 353 tests in the cached offline Linux test image. The focused display/template suite passed 67 tests, and the Skill frontmatter validator passed. These overlapping suites are not additive test counts.

Three synthetic final reports were exported with an isolated, invisible Word 16 instance opened read-only, with macros disabled, no recent-file entry and unchanged source hashes. The configured Windows dependency bundle contains no LibreOffice, so Word export plus bundled Poppler rasterization was used for visual QA. See `word-render-receipts.json`, `baseline-word-render-receipts.json` and `visual-qa.json`.

Rendered page counts remain 16 / 9 / 8. All non-cover pages are pixel-identical to the same-input HEAD baseline, except the intended group-company balance-list number glyphs. PDF text confirms the AI label occurs only on page 1 in every profile. Key cover and balance-list page images are synthetic QA evidence, not customer reports.

The readonly preflight passed 45 Windows tests (one Linux-specific check skipped), 46 Linux tests, and a Python 3.7 wrong-host entry check with no production reads or mutations.

The complete candidate passed one offline stage, qualification and publication cycle in the cached Linux AMD64 test image: 304 collected test instances and 9 CLI checks passed, with before/after manifests and backup verified. The update-template boundary suite passed 16 tests on Windows and 16 on Linux; the unbound Python 3.7 entry performed zero commands. See "offline-update-qualification.json" and "local-install-qualification.json". This local qualification is supplemented by the production cached ARM64 runtime acceptance below.

## Production status

**Released; runtime accepted, user report acceptance pending.** The operator ran the bound update tool once on November 6. Operation `4afbe409ad1f49c2a79f28457e91df51` returned `RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED`, exit code 0. The shared Skill changed from `28944ec28d4644acb09cd10eb8e73dc546a4250d0c21172d325b23cb713d7d43` to the qualified candidate `883b98f54f2756c218e98bb535d4d6265fb7bd1fffc2049a54bbac7746ea39dc`.

The production maintenance runtime passed 304 regression instances and nine profile CLI checks. All five Gateway node views matched the new tree, with the same sampled Pod UIDs before and after. The previous Skill backup was verified and the maintenance Pod was confirmed absent. No workload patch, database access or provider request was made. Gateway, Runner and Provisioner specifications were preserved.

The accepted baseline remains `operator-preflight-converged-20261106.json` (Gateway 409 fully converged, Frontend 242, Provisioner 86 and Runner 58 stable). The earlier rolling receipt and all preparation records remain unchanged as historical evidence. `runtime-acceptance-20261106.json` records the exact original pasted output and the single transport normalization used to reconstruct the 7,326-byte server report; its SHA256 matches the operator-reported `903cd6eb15fde2a10bc9e5123a231db6b1ee8f21108b791aca73a8cfe5b8a777`.

The operator should generate a new report in a new `/research-fund-settlement` conversation to confirm the outer `1.` / `2.` numbering and the small cover-only AI label. Existing reports are not rewritten. Business acceptance is still pending; do not rerun the update or cleanup tools.

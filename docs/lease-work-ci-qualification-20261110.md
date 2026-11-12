# Lease / Work source CI qualification

PR #45 changes the attachment and conversion runtime from source tree
`06933fb2211346321f47efa5d1ac07f706e9944e`. The existing Work provenance
projection still expected the previous `feishu.py`, so backend lint's provenance
step, November 2 source provenance, and Gateway 406 observation provenance
rejected the candidate after the ordinary lint checks had passed.

`lease_work_source_20261110.py` now verifies the exact 15-file candidate delta
and four immutable parent snapshots before exposing its parent to the existing
Work verifier. Its sealed manifest binds the tested tree and parent commit;
existing historical inventories and their hashes remain unchanged. Newly added
runtime files are included in the qualified inventory. Ref-based archive readers
include the new evidence, tests, environment plan, and guide. Regression tests
reject changes to every candidate file, every parent snapshot, or the manifest,
and reject new runtime files without their manifest.

The full unit suite also exposed five XLSX tests that assumed conversion ran
in the Gateway process. Real conversion tests now require the compatibility
shim to remain absent from the parent while validating both standard and
`defaultColWidthPt` workbook content from the child. The dependency-failure
test checks the converter error directly and injects a failure at the isolated
worker boundary, where the public API must withhold Markdown publication.
Existing real child failure, timeout, cancellation, and cleanup tests remain.

This follow-up changes tests and source qualification only. It does not change
the candidate's runtime code, conversion limits, dependencies, or Work environment
plan. Source qualification and CI success do not claim live business acceptance.

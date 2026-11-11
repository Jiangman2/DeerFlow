# New-chat default model source registration

Enterprise commit `459b44eae00e671466c90fc20462dee018b6a0c0` changes six frontend
runtime files, adds two regression tests, and updates the module guide. It makes
ordinary new chats initialize DeepSeek V4.1 once while preserving explicit choices
within the current chat and the existing/custom-agent policies.

The November 2 historical source verifier still expected the old settings store
bytes, causing both `lint-backend` and `preparation-evidence-and-synthetic-tests`
to fail with `NOV02_PROTECTED_SOURCE_CHANGED`. This registration preserves that
historical constraint and validates the newer successor first.

`SOURCE.json` pins exactly the nine changed paths to the colleague's commit and
its parent `d63a01c9b293bad64d99055918ac3d16ffb973eb`. Seven existing parent files
are copied from Git into `baseline/`; the two added tests have no parent file.
The verifier checks the sealed registration, every current hash and every parent
hash before projecting those parent bytes into older source checks. All three
Git-reference verifier snapshots include the new archive and module guide.

The rejection suite covers missing seals, missing/changed sources, parent edits,
scope expansion, unregistered runtime changes and false acceptance claims. It
also compares all registered hashes to the actual fixed Git commits. Existing
financial, frontend and backend tests remain enabled.

This change edits no application files and runs no production commands. It
registers committed source only; it does not establish a fresh production image,
model-call or financial-report acceptance. Previous inventories, receipts and
deployed-source snapshots remain unchanged.

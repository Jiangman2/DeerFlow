These are reviewable, unexecuted source baselines for the PPT patch-scope tests.

`manifest.json` pins each source commit/path, Git blob ID, SHA256, and byte count.
The three `.py.txt` files are exact LF source bytes captured from
`0044d221d4338ddf455f57c46435920d896a5824`; the tests parse the before/after
sources with the same running Python interpreter. They do not pin an AST dump
whose representation can differ across Python versions.

The manifest also pins the old vendor and public Skill tree IDs. The tests read
the current index once, restore only the three originally authorized vendor
entries in memory, and reproduce Git's recursive tree hashes (including modes
and bytewise directory sorting). Public Skills must reproduce their exact
reviewed tree without any substitution. Unstaged and untracked changes remain
separately checked, so an unchanged index cannot conceal a working-tree change.
Neither the index nor the object database is written by these tests.

The vendor baseline commit was private local release history and is not
reachable from the PR; increasing checkout fetch depth alone cannot retrieve
it. The public baseline is an ancestor but is absent from a depth-one checkout.
Shipping these small pinned baselines makes both checks independent of either
historical object's availability. The original allowed files/functions,
patched hashes, base hashes, and upstream identity checks remain in force.
The former public-baseline ancestry assertion is replaced by exact current-tree
content/mode equality. A depth-one checkout cannot prove the missing ancestry
chain; that lineage is recorded separately during provenance review in the full
repository. Do not interpret the portable tree check as an ancestry proof.

Do not regenerate the manifest to make an unexpected source change pass. Any
baseline update requires its own provenance and scope review. These fixture
files are data, not additional product modules or release payload.

# Procurement review source qualification

This records the scenario-two procurement implementation and its schema-compatibility
updates against commit `c0c6dfa547a1963677db974a98f3dbc47b9c130e`.

The verifier pins this manifest, checks every current file and every retained parent
snapshot, then exposes the exact parent to the existing historical release verifiers.
New procurement paths are hidden only in that historical view. Existing historical
manifests and their hashes remain unchanged; this record does not prove deployment
or live business acceptance.

The runner overlay includes the procurement ORM and migration so it can recognize
shared schema 0023 without replacing its dedicated agent. This recipe change does
not claim that a runner image has been built or deployed.

Tests reject drift in each current file, parent snapshot and manifest, compare the
snapshots with Git, and check idempotent historical projection. Bootstrap regressions
cover empty, legacy, concurrent and already-versioned databases.

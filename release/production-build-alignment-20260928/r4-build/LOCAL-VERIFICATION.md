# R4 local verification and release gates

Local source tests: `backend/tests/test_artifacts_router.py` must pass. The
package composer must reproduce both frozen production source hashes, replace
only the selected resolver, and preserve both bounded preview port windows.

The build package's `--check-only` confirms the live Gateway and Runner bases
and source hashes, but does not build or change production. Build-only verifies
the two OCI candidates without importing them. Before rollout, require
independent canaries on the scheduled Gateway and Runner nodes, old-image
rollback availability, and fresh deployment and active-work checks.

Browser acceptance: a new explicit `/ppt-master` chat opens an authenticated
6060 preview while its Sandbox is running; a pre-existing PPT thread with a
missing `runtime_profile` also routes if its latest owner-scoped run is the PPT
Runner. An ordinary or foreign-owner thread remains rejected for 6060. Verify
confirmation mutations retain CSRF and owner authorization. A disappeared
Sandbox should report unavailable, not a false port error. Never use a
metadata-only or client-supplied marker as PPT preview authority.

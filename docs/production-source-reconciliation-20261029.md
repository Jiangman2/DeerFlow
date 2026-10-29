# Production source reconciliation: Gateway 389 / Frontend 231

This reconciliation records code **already deployed**, not a new rollout. The
accepted source lock is `deploy/enterprise/production-source-lock-20261029.json`.
Run `python deploy/enterprise/verify-production-baseline-20261029.py --ref HEAD`
with Python 3.12 or newer to verify committed Git objects.

| Component | Accepted generation | Image manifest |
| --- | --- | --- |
| Gateway | 389; 70 accepted Pods | `sha256:a879d9a0688099417a10d301fb41fa78664cb32d62b17a15ec1f435b943a19d4` |
| Frontend | 231; 2 accepted Pods | `sha256:99a1da37496816a6abb0524587069cdf59dcec1bea4d5cc94c58f57c7817bbb2` |
| PPT Runner | 55; unchanged independent source snapshot | `sha256:0b0cdbbbf95794ab0ce6f96e09f6a449152d05761c23b47dcda3d5c9cf2a3140` |

## Scope

Frontend 231 includes the cumulative Frontend 230 visual and mobile refinements:
the original ocean-platform background across the welcome page and sidebar,
consistent module headings and gutters, smaller secondary dialogs, responsive
upload/settings/table layouts, employee avatars and the hover contact card.
It also includes adaptive employee pagination, account identity/token summary,
employee click analytics and feedback actor detail inside the administrator
dashboard, and the competition display at `/competition`.

Gateway 389 adds employee analytics, administrator-managed public competition
selection, per-account token totals and the data-lake/local-data task boundary.
The display publishes aggregate counts only; administrator maintenance retains
authentication and CSRF, and machine usage queries retain their own access
boundary. Anonymous viewers receive 404 while the display is unpublished.
Shared selection writes use version checks and a cross-process file lock.

The V4.1 adapter is present in the deployed image, but **V4.1 is not enabled**.
The production model configuration and default remain unchanged after the
upstream returned HTTP 402. The model YAML in `deploy/enterprise` is a synthetic
qualification fixture, not the active production configuration. The paused Work
update, database migrations and Runner changes are excluded.

The Tavily Secret replacement is recorded only through its redacted receipt.
No old or new key is committed. All 70 Gateway environments passed the binding
check; actual Tavily search business acceptance is still pending.

## Evidence and reproducibility limits

`release/combined-features-20261028-r1/SOURCE.json` seals 967 frontend inputs and
588 Gateway Python files. Sources were restored from that sealed snapshot, not
the dirty development checkout. The 11-file Gateway delta was appended to the
accepted 69-layer Gateway image; Frontend was built with the pinned ARM64 Node
base recorded in the archived build script. No dependency lock was upgraded.

The prior 13 Python layout/import-order normalizations are retained, with their
exact production originals and AST checks. Three frontend build inputs had CRLF
line endings; their original bytes are archived under `source-line-endings/`,
and the verifier permits only their exact LF representation in Git. This does
not permit runtime logic changes. Historical locks remain immutable.

Two unit tests received Prettier-only layout corrections. Their original sealed
inputs are preserved in `qualification/source/`, and the source lock pins the
corrected test hashes separately; these exceptions do not cover runtime files.

Build, preload, validation, partial activation, diagnosis and final resume
receipts are retained in the release directory. Earlier `NOT_DEPLOYED` statuses
describe those earlier stages; the final runtime receipt is authoritative for
deployment completion. The final server report remains on datax-01 at:

`/var/lib/portal-combined-features-resume-20261029-r1/REPORT.json`

Its SHA256 is
`a5bafd66bedbb8a87d1a13dabfa7589383e700969e555e69ccc10965d49b4772`
(157,390 bytes). The repository contains the operator-pasted summary and its
source attachment digest, not a fabricated copy of the full server report.

Two installed Gateway checks each verified all 588 files and passed 251 tests
using synthetic data. The archived qualification bundle preserves those exact
test inputs. The cross-node storage test passed read/write, stale-version and
exclusive-lock checks and removed its canary. Frontend image checks verified
13 compiled assets on each frontend node. The final runtime receipt accepted
70 Gateway and 2 Frontend Pods and the seven Service-level HTTP checks.

This is source-to-existing-image reconciliation. It does not claim that a fresh
build today will reproduce an identical image digest, nor that archived
operator-bound commands may be rerun. The historical tools are audit material.

## Preserved components and outstanding acceptance

Runner 55 retains its separate archived source tree. WeKnora's native GLM
1999-file source lock and program receipt remain unchanged and must still pass
`python deploy/enterprise/verify-weknora-native-glm-source.py --ref HEAD`.
WeKnora Frontend 39 retains the exact archived member-UI ConfigMap payload and
the colleague's base image. Full provenance of that base image remains unproven.
The October 26 component observation is historical, not a fresh cluster scan.
The combined rollout's preservation receipt establishes its unchanged scope.

External HTTPS/browser acceptance, authenticated dashboard checks, publication
of the competition roster and actual Tavily search remain separate business
acceptance. The October 29 read-only image-generation report found repeated
`generate_image` timeouts dating back to October 27, with matching configuration
and successful TCP connections in one sampled Pod on each of five Gateway nodes.
The operator subsequently confirmed that the problem was on the model-service
side, had been resolved, and required no further investigation. That resolution
is operator-confirmed, not a new inference test performed by this reconciliation.
No image-generation code change is included in this production baseline.

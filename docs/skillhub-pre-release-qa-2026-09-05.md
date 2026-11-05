# Skill Center pre-release QA — 2026-09-05

## Decision

The phase-1 Skill Center integration is functionally ready for a staged server
deployment after production identity, secrets, storage, and image-mirror values
are supplied. It is not a frontend-only update: the release contains the
DeerFlow frontend shell, Gateway runtime-install adapter, Nginx routes, and a
separately deployed upstream iFLYTEK SkillHub v0.2.18 stack.

## Verified locally

| Area                     | Result         | Evidence                                                                                                                                                        |
| ------------------------ | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gateway skill APIs       | Pass           | Install/list/toggle/uninstall, upgrade, idempotency, conflict protection, validation, per-user isolation, feature-gate, and custom-skill authorization tests    |
| Frontend quality gates   | Pass           | Full ESLint and TypeScript check                                                                                                                                |
| Skill Center unit tests  | Pass           | 9 tests covering routes, roles, identity parsing, detail/download paths, My Skills merge, and visible-text sanitization                                         |
| Native marketplace       | Pass           | Search, sorting/filter surface, detail Overview/Files/Versions, stars, subscriptions, and 17 seeded skills rendered through the same-origin workbench           |
| Administrator navigation | Pass           | Publish, governance/review, promotions, reports, users, audit log, labels, and namespaces opened successfully                                                   |
| Runtime lifecycle        | Pass           | A real marketplace package was installed, enabled, disabled, re-enabled from Settings, observed in My Skills, and removed; temporary test data was cleaned up   |
| State synchronization    | Pass after fix | `/api/skills` is now authoritative for live enabled state; SkillHub registry metadata supplies provenance/version without overwriting a fresher Settings change |
| Mobile                   | Pass           | 390×844 viewport, no page-level horizontal overflow, single-column cards, scrollable tab rail, and sidebar escape hatch                                         |
| Reverse proxy            | Pass           | Docker Nginx configs parse; SkillHub notification SSE uses an exact unbuffered route with a 660-second read timeout                                             |
| Helm packaging           | Pass           | Official v0.2.18 dependencies resolved; `helm lint` and `helm template` pass with the repository values template                                                |
| Session bridge           | Pass           | Gateway assertion tests pass; customized official server compiles all eight Maven modules and its HMAC adapter tests pass 2/2                                  |

The standalone Playwright suite was also invoked against the already-running
auth-enabled shared development server. Its four isolated-context cases were
redirected to DeerFlow login because that process was not started with the
suite's `DEER_FLOW_AUTH_DISABLED=1` test mode. This is a harness/environment
limitation, not a rendered-page failure; the same desktop, role, My Skills, and
mobile paths were exercised in the authenticated in-app browser.

## Data and permission boundaries

- **My Skills** is always scoped to the authenticated account's
  `users/<user_id>/skills/custom/` inventory.
- Registry-installed rows are the same physical personal skills, enriched with
  namespace and version metadata from `_registry_installations.json`.
- Historical shared packages in the deployment root `skills/custom/` are
  `legacy` compatibility inputs and do not appear in My Skills.
- An administrator's My Skills page shows only that administrator account's
  personal inventory. Phase 1 does not add a cross-user filesystem browser.
- Ordinary SkillHub consumers see discovery, My Skills, favorites,
  subscriptions, detail, and personal install/enable/disable/uninstall flows.
- Operator navigation is shown from native SkillHub roles, while SkillHub's
  server-side RBAC remains the enforcement boundary.
- Existing DeerFlow self-created custom-skill enable/disable remains governed
  by the existing DeerFlow administrator policy. Changing that policy would be
  a separate authorization decision, not part of this release.

## Required production inputs

1. Import the reviewed ARM64 release set on all three nodes: customized server,
   upstream web/scanner, PostgreSQL and exporter, Redis and exporter, and the
   BusyBox dependency waiter. The release package uses local immutable names and
   `imagePullPolicy: Never`.
2. Create a protected values override with the real HTTPS origin, database and
   Redis passwords, bootstrap administrator password, cookie secret, storage
   class/access mode, and mirrored image locations. Do not commit it.
3. Store one random, at-least-32-byte bridge secret in the DeerFlow runtime
   Secret and the SkillHub server Secret. Verify the exact passive-bootstrap
   proxy route; browser-supplied identity headers must be overwritten.
4. Assign native SkillHub operator roles to the required bridged enterprise
   identities and retain a rotated break-glass local administrator.
5. Back up the current DeerFlow deployment manifests/configuration and preserve
   all existing global and per-user skill trees before rollout.

## Staged rollout order

1. Deploy SkillHub PostgreSQL, Redis, scanner, server, and web as private
   ClusterIP services; keep public Ingress disabled.
2. Verify SkillHub health, migrations, scanner readiness, and native admin login
   from inside the cluster while `DEER_FLOW_SKILLHUB_ENABLED=false` keeps the
   workspace entry and runtime endpoints closed.
3. Roll out the DeerFlow Gateway, frontend, and shared Nginx configuration while
   the feature remains closed.
4. Enable the feature, restart Gateway and frontend to load the Secret, and
   verify passive session bootstrap with one ordinary user.
5. Assign/verify one native administrator role, then complete desktop/mobile and
   install/upgrade/disable/uninstall canaries before broad use.

## Post-deployment acceptance

- An already-authenticated user enters Skill Center at discovery without seeing
  the standalone SkillHub landing page, logo shell, or footer.
- Ordinary and administrator navigation differs according to native roles, and
  direct unauthorized native API calls remain denied.
- A canary skill can be installed to exactly one user's personal custom tree,
  used by a new conversation/default expert, disabled from either My Skills or
  Settings with synchronized state, upgraded, and uninstalled without changing
  another account.
- Existing per-user custom skills appear in My Skills; shared root
  `skills/custom/` entries do not.
- Search, detail, Files, Versions, favorites, subscriptions, notifications,
  publishing/review, and audit pages work through the shared HTTPS origin.
- Rollback restores the previous frontend/Gateway/Nginx images and leaves
  SkillHub data volumes intact for diagnosis rather than deleting them.

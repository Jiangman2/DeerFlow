# iFLYTEK SkillHub integration

DeerFlow exposes the native iFLYTEK SkillHub service under
`/platform/skillhub/`, but presents it as the DeerFlow **Skill Center** at
`/workspace/skills`. DeerFlow owns the visible page header, role-aware secondary
navigation, loading state, embedded color tokens, and removal of SkillHub's
standalone header/footer. The embedded content starts at skill discovery rather
than SkillHub's public home or account dashboard. SkillHub continues to own the
registry, publishing, review, versioning, governance, authentication,
authorization, and audit behavior.

## Scope

Phase 1 intentionally does not rebuild SkillHub screens or introduce a second
skill registry in DeerFlow. It provides:

- a desktop and mobile workspace entry directly below Knowledge;
- a DeerFlow-owned desktop/mobile shell around the native SkillHub content;
- a discovery-first landing page, personal collection/subscription tabs, and a
  role-aware administrator menu;
- a presentation-only embedded theme that hides the standalone SkillHub chrome
  without forking its application behavior;
- one stable same-origin reverse-proxy path for UI, assets, APIs, OAuth/OIDC
  callbacks, downloads, and CLI device authorization;
- a DeerFlow-owned install bar on native skill detail pages that installs,
  upgrades, enables, disables, or uninstalls a package for the current user;
- an upstream Helm values template for a private SkillHub deployment.

SkillHub remains the registry and governance system. DeerFlow owns the runtime
adapter because only DeerFlow can safely resolve the authenticated user's
private skill directory, apply its local security policy, refresh its prompt
index, and mount the resulting tree into that user's Agent sandbox.

## Request flow

```text
Browser /workspace/skills
  -> DeerFlow Skill Center shell
  -> iframe /platform/skillhub/search?q=&sort=relevance&page=0&starredOnly=false
  -> DeerFlow Nginx
  -> one-time passive session bootstrap signed by DeerFlow Gateway
  -> skillhub-web:80
  -> SkillHub Server / PostgreSQL / Redis / Scanner
```

The exact `/platform/skillhub/` Nginx location takes precedence over the
existing generic `/platform/` WeKnora location. SkillHub must be configured with
that same base path so the outer proxy never has to rewrite application URLs.
The exact `/platform/skillhub/api/web/notifications/sse` route disables proxy
buffering and keeps a 660-second read timeout so native notification events are
not delayed by the otherwise buffered page/asset proxy.

The reviewed upstream web image denies iframe embedding by default. The shared
Nginx removes that denial only for `/platform/skillhub/`, replaces it with
same-origin `frame-ancestors 'self'`, and rewrites the matching HTML meta policy.
No other page has its framing policy relaxed.

An authenticated browser establishes its native SkillHub session through the
exact `/platform/skillhub/api/v1/auth/session/bootstrap` route. Nginx first calls
Gateway's internal `/internal/skillhub/session/assertion` endpoint with the
existing HttpOnly session cookie. Gateway returns a role-free, 30-second HMAC
assertion; Nginx overwrites any browser-supplied assertion headers before sending
the request to SkillHub. The custom v0.2.18 server adapter verifies the HMAC,
issuer, audience, timestamps, nonce, and identity fields, then creates or binds a
native SkillHub user. SkillHub reads roles only from its own database, so a
DeerFlow administrator is not promoted to a SkillHub administrator.

An install from a native detail page follows a separate, explicit boundary:

```text
Browser with native SkillHub session
  -> authenticated SkillHub version download
  -> multipart POST /api/skillhub/runtime/skills
  -> DeerFlow archive limits + safe extraction + static scan + LLM scan
  -> regular-file path/size/SHA-256 manifest verification
  -> atomic install under users/<user_id>/skills/custom/<slug>/
  -> per-user provenance registry + enabled state
  -> invalidate only that user's skill prompt cache
  -> sandbox mounts the same tree at /mnt/skills/custom/<slug>/
  -> sandbox init opens every mounted file before runtime startup
  -> next Agent run discovers the enabled skill
```

The browser performs the authenticated registry download so SkillHub keeps its
native session and download authorization. The package is never accepted by
DeerFlow based on a client-supplied path or URL: the Gateway receives bytes plus
the normalized namespace, slug, and version, verifies that `SKILL.md` has the
same name as the registry slug, computes SHA-256, and applies DeerFlow's own
checks before committing it. Deployments whose SkillHub object storage redirects
downloads to another origin must either enable CORS for the DeerFlow origin or
configure SkillHub/Nginx to stream downloads through the same-origin prefix.

## Runtime behavior and expert scope

Installing a skill makes it available to the current user; it does not create a
new digital employee and does not silently broaden an employee's explicit whitelist.

| Agent configuration                                       | Behavior on the next user message                                                       |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Default Agent or digital employee with `skills` omitted/`null` | Discovers every enabled public and current-user custom skill, including the new install |
| Role expert with an explicit `skills` list                | Discovers only skills in that whitelist; add the new slug deliberately                  |
| Role expert with `skills: []`                             | Discovers no skills                                                                     |

The next user message may be in a new conversation or an existing conversation;
an already-running turn is not mutated. With deferred discovery enabled,
DeerFlow initially adds the skill to the compact skill index and reads the full
`SKILL.md` only when the task selects or explicitly activates it.

The DeerFlow-owned **My Skills** page is account-scoped. It lists only the
authenticated user's `users/<user_id>/skills/custom/` inventory and enriches
registry-installed rows with SkillHub namespace/version provenance. Historical
shared packages under the deployment root `skills/custom/` are `legacy`
compatibility inputs, not personal assets, and are intentionally excluded from
My Skills for ordinary users and administrators alike. An administrator's My
Skills page therefore remains the administrator account's own library; native
SkillHub operator pages do not expose another user's private custom directory.

## Runtime API and safety

All four endpoints require the normal DeerFlow authenticated user and resolve a
`UserScopedSkillStorage` from that server-side identity:

| Endpoint                                                 | Purpose                                                          |
| -------------------------------------------------------- | ---------------------------------------------------------------- |
| `GET /api/skillhub/runtime/skills`                       | List the current user's registry-managed installs                |
| `POST /api/skillhub/runtime/skills`                      | Install a new version or atomically upgrade an owned install     |
| `PATCH /api/skillhub/runtime/skills/{namespace}/{slug}`  | Enable or disable the install for this user                      |
| `DELETE /api/skillhub/runtime/skills/{namespace}/{slug}` | Remove only the registry-owned tree and its enabled-state record |

The adapter rejects invalid identity/version syntax, empty or oversized uploads,
unsafe archive paths, links, archive bombs, unsupported executable content,
name mismatches, deterministic SkillScan blockers, and LLM security-scan
blockers. A missing or unavailable LLM scanner fails closed. The default upload
limit is 64 MiB; `DEERFLOW_SKILLHUB_PACKAGE_MAX_BYTES` may set a 1–512 MiB
deployment-specific limit. Installation refuses to overwrite an existing
custom skill unless the per-user provenance registry proves that the same
SkillHub namespace owns it. Upgrades are staged and scanned before the live tree
is swapped, and the previous tree is restored if the swap fails.

Accepted archives are never partially installed. Symbolic links are rejected
instead of skipped, and DeerFlow compares the relative path, byte size, and
SHA-256 of every regular file before the staged tree can replace the live tree.
In Kubernetes PVC deployments, the Provisioner mounts shared public skills from
`SKILLS_PVC_NAME/public`, the current user's registry/custom skills from
`USERDATA_PVC_NAME/deer-flow/users/<user_id>/skills/custom`, and optional legacy
skills from `SKILLS_PVC_NAME/custom`. It creates a new user's custom and thread
subpaths before mounting them, then opens every regular file through the final
`/mnt/skills/public`, `/mnt/skills/custom`, and optional
`/mnt/skills/legacy` paths. A missing or unreadable mount therefore prevents
the sandbox Pod from becoming ready instead of failing later inside a Skill.

This is a file-delivery and platform-runtime guarantee, not a promise that an
arbitrary uploaded program is self-sufficient. Skill authors must still declare
`required-secrets`, use tools permitted by the selected Agent, and target
interpreters/libraries/services present in the released sandbox image. Native
binaries remain rejected. Before publishing, run the Skill in the release
sandbox image with its production tool policy, network policy, and declared
secrets; a Skill that needs an undeclared package or unreachable external
service is not DeerFlow-compatible even when its files are mounted correctly.

These checks reduce supply-chain risk but do not make arbitrary third-party code
trustworthy. Enterprise operators should keep SkillHub review/publishing policy,
scanner services, package provenance, and DeerFlow tool permissions enabled;
an approved skill can still request powerful tools that the selected Agent is
authorized to use.

## Kubernetes install

The repository does not fork or vendor SkillHub. Deploy the reviewed upstream
release into the existing `deer-flow` namespace so the native web Service has
the stable name `skillhub-web` expected by Nginx.

1. Check out the reviewed upstream release:

   ```bash
   git clone --branch v0.2.18 --depth 1 https://github.com/iflytek/skillhub.git
   helm dependency build ./skillhub/charts/skillhub
   ```

2. Copy `k8s/skillhub/values-deer-flow.example.yaml` to a protected location.
   Replace the public hostname and every `REPLACE_WITH_...` value. Do not commit
   the completed file.

3. Install the native chart:

   ```bash
   helm -n deer-flow upgrade --install skillhub ./skillhub/charts/skillhub \
     -f /protected/path/values-deer-flow.yaml
   ```

4. Apply or restart DeerFlow Nginx after its ConfigMap is updated, then verify:

   ```bash
   kubectl -n deer-flow get deploy,svc -l app.kubernetes.io/instance=skillhub
   curl -I https://deerflow.example.com/platform/skillhub/
   ```

For the enterprise offline ARM64 cluster, import all eight runtime images on
every eligible node before install: the customized server, upstream web and
scanner, PostgreSQL, PostgreSQL exporter, Redis, Redis exporter, and BusyBox
dependency waiter. The supplied enterprise release aliases all eight under
`deerflow.local`, uses immutable release tags, and sets `imagePullPolicy: Never`.
Do not enable a second public Ingress: the shared DeerFlow Nginx remains the
only browser entry point.

## Authentication and administrators

The example starts with SkillHub's native bootstrap administrator so publishing,
review, upgrade, and withdrawal can be evaluated without granting roles from a
DeerFlow session. Keep it as a break-glass account, rotate its generated password
after the first login, and grant only the native SkillHub roles required by each
operator.

The workspace entry targets SkillHub search, not the public SkillHub landing
page or the account-oriented native dashboard. Enterprise users reuse their
existing DeerFlow login through the private HMAC session bridge described above;
there is no second password prompt and no dependency on a standard OIDC issuer.
SkillHub still creates and owns its own session, identity binding, and RBAC
records.

The DeerFlow shell uses SkillHub's `/api/v1/auth/me` response and the current
user's native namespace memberships only to decide which navigation entries to
present. It does not grant permissions. SkillHub's native server-side RBAC
remains the authorization boundary. The integrated navigation mirrors the
native destinations as follows:

| SkillHub identity or membership | Skill Center presentation |
| --- | --- |
| Every authenticated user | Discovery, native dashboard, authored/published skills, My Namespaces, review progress, stars, subscriptions, notifications, API tokens, profile, notification preferences, and password settings when native `canChangePassword` is true |
| `SKILL_ADMIN` | Publish ZIP/folder, global review center, governance, promotions, and reports |
| `USER_ADMIN` | Global review center and user management |
| `AUDITOR` | Audit log |
| `NAMESPACE_ADMIN` | Governance |
| Active team namespace `OWNER` or `ADMIN` | Review center for the namespaces that native SkillHub authorizes |
| `SUPER_ADMIN` | All operator entries plus label management and global namespace governance |

`/dashboard/namespaces` and `/admin/namespaces` are intentionally separate.
The first is the native personal/team namespace workbench (including the Create
Namespace button for roles that SkillHub authorizes); the second is the
super-administrator governance page for freeze, archive, restore, ownership,
and member operations. Combining those routes makes native creation appear to
be missing.

Contextual native routes—skill detail/version comparison, namespace member and
review detail, and individual review detail—remain reachable from their native
parent pages and are not duplicated as top-level shell links. Login, register,
password-reset, and logout surfaces are replaced by the enterprise SSO session
boundary; CLI authorization remains an on-demand protocol route. These are the
only deliberate shell substitutions, not disabled registry capabilities.

The phase-1 values disable automatic personal namespace provisioning, so an
ordinary enterprise user is a skill consumer by default. Deliberately adding a
user to a namespace through native SkillHub administration can still make that
user a publisher without any DeerFlow code change.

Set `DEER_FLOW_SKILLHUB_ENABLED=true` and the same random, at-least-32-byte
`SKILLHUB_SESSION_BRIDGE_SECRET` in the DeerFlow runtime Secret and SkillHub
server configuration. The bootstrap route must remain exact and ahead of the
generic SkillHub proxy. Direct requests to Gateway's internal assertion endpoint
still require a valid DeerFlow session; direct requests to SkillHub without a
valid signed assertion do not create a session. Keep the bootstrap administrator
until one bridged enterprise identity has been assigned and verified with the
required native SkillHub role.

## Acceptance checklist

- Knowledge, Skill Center, Digital Employees, and Scheduled Tasks remain separate
  workspace destinations.
- Skill Center loads native search on the DeerFlow origin on both desktop and
  mobile; it never starts on the public landing page or account dashboard.
- Users see DeerFlow's header and secondary navigation; SkillHub's standalone
  logo/header, dashboard-return control, decorative branding, and footer are
  absent from the embedded presentation.
- The DeerFlow-owned **My Skills** tab lists the authenticated user's complete
  `custom` inventory, including both self-created and SkillHub-installed skills.
  SkillHub entries show namespace and version and expose their per-user
  enable/disable, detail, and uninstall actions; creating a skill continues to
  use the existing conversational skill builder.
- Every authenticated user can reach all native personal-workbench pages.
  Operator entries are shown only for the matching native platform role or
  namespace membership, and the native API independently enforces the same
  permission.
- **My Namespaces** opens `/dashboard/namespaces`; **Namespace Governance**
  opens `/admin/namespaces`. Super administrators can reach both instead of
  losing the native creation workbench behind the governance screen.
- Embedded dialogs and native select lists remain vertically scrollable at
  desktop zoom and on short mobile viewports; the role selector must never be
  clipped by the containing dialog.
- SkillHub deep links, refreshes, API requests, downloads, and login callbacks
  stay under the same prefix.
- Publishing, review queues, version lifecycle, and administrator content are
  upstream SkillHub screens presented inside the DeerFlow shell, not DeerFlow
  replicas.
- Installing an approved detail-page version creates only
  `users/<user_id>/skills/custom/<slug>/`, records namespace/version/SHA-256,
  and never overwrites an unmanaged custom skill.
- The installed tree contains every accepted regular archive file; a package
  containing a symbolic link is rejected rather than reported as a partial
  success.
- In PVC mode, a newly created Sandbox mounts that exact user's tree at
  `/mnt/skills/custom/<slug>/`; its init checks can open `SKILL.md`, scripts,
  references, templates, and assets before the runtime becomes ready.
- A canary Skill script executed through
  `/mnt/skills/custom/<slug>/scripts/...` can read a bundled reference and
  exits successfully in the released sandbox image.
- The installed skill appears in `GET /api/skills` for the same user, its
  enable/disable switch changes prompt discovery on the next run, and another
  user cannot see or mutate it.
- If either deterministic or LLM security review blocks or is unavailable, the
  install fails without replacing the live skill tree.
- A SkillHub outage returns an isolated error for the embedded workbench and
  does not prevent DeerFlow Nginx, chat, knowledge, or digital employees from
  starting.

After deploying a Provisioner image containing this mount contract, delete or
expire existing Sandbox Pods so subsequent turns recreate them with the new
volume mounts. Existing Pods retain the Pod specification with which they were
created; updating the Provisioner does not mutate them in place.

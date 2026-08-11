# ppt-master Host Adaptation Audit

Status: superseded for new tasks by the isolated runner, 2026-08-10

New explicit `/ppt-master` tasks no longer use the host adaptation gates audited
below. They run in the dedicated process documented in
[`ppt-master-isolated-runner.md`](ppt-master-isolated-runner.md), where the
upstream package is authoritative. The matrix remains as migration evidence for
legacy threads only and must not be extended with new PPT-specific middleware.

This document separates three different responsibilities:

1. `ppt-master` owns route semantics, stage order, schemas, validators, and delivery rules.
2. DeerFlow owns durable execution across model/tool nodes, run boundaries, context compaction, remote Sandboxes, and browser proxying.
3. A host contract must make the Skill's declared runtime prerequisites observable without copying the Skill's workflow into DeerFlow.

The official package works best in a continuous local session where the same model keeps the complete authority context, shell exit status is direct, the Skill tree is writable, and loopback preview belongs to the same machine. DeerFlow intentionally changes all of those assumptions. The adaptation layer therefore has to externalize the assumptions that the upstream host satisfies implicitly.

## Verified real-run evidence

Thread `ef11b110-6b95-4008-8f1e-9d59340259f8` established the following order from persisted tool receipts:

1. The selected Generate route and the current planning authorities were read.
2. `design_spec_reference.md` was read before `design_spec.md` authoring.
3. `spec_lock_reference.md` was read before `spec_lock.md` repair.
4. The official `project_manager.py validate` failed, the lock was repaired, and the same official validator then passed.
5. Five AI image resources were generated and persisted in the project.
6. The first-page SVG checker rejected an external PNG written through `<use>`. The gate remained blocking, the SVG was repaired to `<image>`, and the same checker was rerun.

The last failure exposed a separate closure problem: `executor-base.md` had been read, but its image-triggered references (`executor-image.md`, `image-layout-spec.md`, `image-layout-patterns.md`, and `svg-image-embedding.md`) and several always-on execution references had not been loaded before P01 was authored.

## Coverage matrix

| Official requirement | Current host coverage | Status | Required action |
|---|---|---|---|
| Complete `SKILL.md` activation and content hash | Activation is persisted and restored | Enforced | Keep generic |
| `attribution_guard.py` before routing | The `ppt-master` adapter requires and receipts the official integrity command before routing; an invalid model batch is reduced to this one deterministic owner action | Implemented in v46 | Replace with a generic Skill-declared bootstrap-owner contract if upstream publishes one |
| Read `routing.md`, then exactly one top-level route | v46 serializes integrity → routing → one model-selected route and strips redundant/later companions instead of deleting a valid route read | Implemented in v46 | Retain the route until a new explicit activation |
| Persist route, stage authorities, and project contracts across compaction | Separate retention classes exist; stage window raised from 4 to 24 | Enforced in next image | Verify with a full conditional branch and compaction |
| Load every deterministic conditional reference before its governed action | The `ppt-master` adapter projects locked mode/style and capability facts, then closes the official SVG/image/chart/structure/web/notes/animation/audio set before the governed action | Implemented in next image | Verify with a fresh default Generate task and forced run boundary |
| Read artifact reference before authoring the artifact | Direct prose-declared artifact/reference edges are discovered and enforced | Enforced | Extend only through declarations owned by the Skill |
| Project root comes from an owning project receipt | Successful init/project-contract receipt is required | Enforced | Keep generic |
| Confirmation files are owned by the user/Skill server | Managed artifacts cannot be fabricated; surface and timeout are checkpointed | Enforced | Retain official server validation |
| Ready Stage 1/2/3 page versus unfinished-plan guard | A successful recommendation write while the owner page is active becomes a resumable user-wait handoff; it cannot be converted into a generic unfinished-plan error | Enforced in next image | Resume from the persisted page result and verify all three stages |
| Stage 1/2/3 ordering and current-stage confirmation | Confirm UI owns field validation; host persists surface and milestones | Partial | Add a generic owner-stage receipt contract so skipped stages are mechanically visible without copying schemas |
| `design_spec.md` and `spec_lock.md` schema/catalog validation | Official `project_manager.py validate` is used and replayed safely after repair | Enforced for declared validator | Preserve real exit status |
| Semantic fidelity from confirmation → Design Spec → lock | Confirmed Deck/Layout handoff is reconciled before contract writes and Executor entry; named prototypes cannot be downgraded to `style`/flat and structured mappings must be complete | Enforced for template reuse in v41 | Keep broader non-template semantic fidelity owned by a future Skill compiler rather than recreating all schemas in DeerFlow |
| Successful validator remains fresh after later planning-contract edits | Failed receipt freshness is tracked; successful validation provenance is not yet bound to later contract mutation | Missing | Add generic successful-validator provenance for authority-declared contract artifacts |
| Step 5 resource acquisition reaches a terminal state before Executor | Old host did not compare persisted `Pending`/`Failed` rows with the Executor transition | Implemented in next image | Require complete Design Spec read after partial edits and block SVG entry until rows are terminal |
| Skill Python owner exit status | Terminal `head`/`tail` could mask failure or cause SIGPIPE | Fixed in next image | Strip only the terminal display truncator; leave ordinary pipelines unchanged |
| First-page and final SVG gates converge | Failed owners remain blocking; their complete issue batch permits one consolidated project SVG repair pass, while stale report reads/advancement stay blocked and exact validator replay remains mandatory | Enforced in next image | Continue real-run verification through final gate |
| Template-install recovery after premature completion attempt | Failed gate permits the official read-only workspace validator, then one atomic project install, while retaining the original `--complete-template-phase` owner | Implemented in v42 | Resume the fresh task and verify install → owner retry → Stage 1 |
| Single-workspace handoff cannot accept a partial install | Template completion and Stage 1 compare every portable `templates`/`images`/`icons` file with the selected workspace | Implemented in v43 | Verify all 中国海油 prototypes and `image9.png` exist before Stage 1 |
| Managed confirmation violation can recover through its official owner | A forbidden direct receipt write remains blocked; if the official wait/completion command then reports a missing project precondition, recovery ownership transfers to that command so bounded project repair and same-owner retry can proceed | Implemented in v44 | Resume the partial-install regression and verify full workspace install before Stage 1 |
| Partial template repair cannot masquerade as a completed install | Contract/SVG entry checks the full portable mapping; every template-copy command reports remaining files, and only a complete copy records durable project mutation | Implemented in v45 | Verify all prototypes plus `image9.png`, then retry the same owner |
| Confirm UI cannot silently downgrade to chat after a project error | Non-timeout owner failures keep the page surface locked; shutdown/chat fallback requires a real bounded-wait timeout or explicit user intent | Implemented in v45 | Complete Stage 1/2/final with actual browser clicks |
| Valid bootstrap/route reads cannot be lost because the model batched another tool | v46 executes the one currently legal official prerequisite through the normal tool node; only genuinely missing/ambiguous route choice consumes a bounded sequence retry | Implemented in v46 | Verify a fresh task that repeats `attribution_guard.py` beside `generate-pptx.md` reaches project initialization |
| One model turn cannot consume an unbounded wall-clock window | Active `ppt-master` delivery turns have a 600-second total deadline; empty/hidden streamed chunks cannot extend it, and timeout reports the persisted stage plus exact resume pointer | Implemented in v39 | Force a short test deadline, then resume the preserved project |
| Conditional authority closure converges across repeated same-run compaction | Loaded `ppt-master` authority is bound to the current run; summary rewrites cannot repeatedly invalidate the full closure, while a new run or owning-script failure still requires exact re-reads | Implemented in v41 | Resume the persisted project and verify first-page repair follows the existing closure without another bulk reference load |
| Structured template SVGs actually derive from mapped prototypes | Pre/post authoring checks compare Master/Layout root identity, inherited layout/master atoms and referenced definitions, and slot identity/bounds against `page_layouts` | Implemented in v41 | Verify P01 retains the 中国海油 background image and package structure in a fresh frontend run |
| Fill Native PPTX plan confirmation/readback | Route scripts own it; host lacks route-specific completion evidence | Partial | Express required owner receipts in the generic runtime contract |
| Enhance Native PPTX plan confirmation/readback | Route scripts own it; host lacks route-specific completion evidence | Partial | Same generic owner-receipt mechanism |
| Create Template library registration | Upstream assumes a writable Skill library; public Skills are read-only in DeerFlow | Missing handoff | Persist user/project workspace, then require an explicit administrator publish/register operation |
| Live preview loopback URL | Frontend rewrites it to the authenticated thread proxy | Enforced | Keep owner alive through export and test every stage page |
| Final PPTX capabilities and publication | `present_files` runs the Skill delivery checker plus requested-capability/package inspection | Enforced for Generate delivery | Extend route-owned final acceptance evidence to Fill/Enhance/Create Template outputs |
| Project intermediates survive Sandbox replacement | Project root is under persistent user workspace storage | Enforced by deployment | Continue testing Pod replacement, not only process restart |
| Loop hard stop is terminal | Hard stop cannot be deleted and auto-resumed | Enforced | Diagnose missing closure/progress instead of raising limits blindly |

## Scope of the current fix

The package already contains `scripts/prompt_audit_manifest.json`, whose load sets describe the official route and conditional reference combinations. It is deliberately marked `audit_only: true` and `runtime_consumed: false`; using it silently as runtime policy would change its contract and couple DeerFlow to a maintainer-only artifact.

The current release deliberately implements this closure only for the vendored
`ppt-master` integration. It does not introduce a platform-wide conditional
authority protocol or alter other Skills. The adapter projects the official
Design Spec/lock facts already used by presentation delivery, requires the
documented Executor reference groups before their governed action, prevents
Step 6 entry while Step 5 resources remain non-terminal, and makes a confirmed
Deck/Layout handoff mechanically consistent before any structured page can be
authored.

A future platform-wide solution, outside the scope of this validation, would be
a versioned, Skill-owned runtime contract with generic concepts such as:

- bootstrap owner operations;
- exclusive top-level route selection;
- deterministic trigger facts derived by the Skill's own validator/compiler;
- required authority documents for each active trigger;
- owning commands and successful receipts that close a gate;
- user/tool-owned confirmation artifacts;
- final acceptance owner and artifact types.

DeerFlow could consume those declarations, persist their resolved closure and
receipts, and block governed actions when the closure is incomplete. That broader
protocol is deferred; it is not required to ship the current `ppt-master`
adapter.

Within the `ppt-master` scope, the new closure no longer depends on the model
remembering those Executor references. Outside that scope, DeerFlow still only
guarantees authorities it already knows were read and restored.

## Acceptance target

The adaptation is complete only when a fresh frontend task can, without manual backend intervention:

1. select and retain one official route;
2. resolve and read the complete active authority closure;
3. persist every owner-produced intermediate and user confirmation;
4. stop at a failed official gate, perform its bounded durable repair model (including a consolidated SVG batch where the official checker reports all issues), and rerun the same owner;
5. complete every requested capability, SVGs, charts, notes, animations, export, delivery check, preview, and `present_files`, without silently adding or dropping conditional image/audio requirements;
6. survive at least one run boundary or forced context compaction without changing route, skipping an authority, or repeating a closed stage;
7. produce no false success, `[FORCED STOP]`, masked owner failure, or browser-inaccessible loopback link.

The next cost-controlled real-task profile explicitly sets AI image generation
and narration/audio to `no`. It still requires the registered template, varied
layouts, icons, native charts, Chinese speaker notes, transitions, and object
animations. Image and audio branches remain covered by their existing focused
tests and prior real-run evidence rather than invoking paid providers again.

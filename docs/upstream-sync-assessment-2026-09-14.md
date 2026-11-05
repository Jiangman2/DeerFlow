# DeerFlow upstream sync assessment (2026-09-14)

## Compared revisions

- Enterprise baseline: `af6c9e7642393cbbc0afc5f861c0b8e1656ef2e7`
- Official `upstream/main`: `f770ecc0b83968ce0a04158a580d872dc34d698a`
- Official commits ahead of the baseline: 632

## Integrated in this pass

The recent-conversation work uses the official implementation and metadata
contracts, adapted only where this enterprise branch has additional demo-chat
and routing behavior:

- `68c0ffdac` — pin/unpin recent chats
- `78f7a5f7c` — pinned ordering on the Chats page
- `98b8e4657` — archive and restore conversations
- `c3adc51ec` — confirmation and retry-safe deletion from the sidebar

The backend keeps pin/archive organization updates from changing
`updated_at`, filters archived membership before pagination, and orders pinned
records before ordinary records. Frontend cache updates and query invalidation
follow the corresponding official contracts.

## Local product additions

No equivalent was present on the inspected official main branch for these
requested UI behaviors, so they remain small enterprise extensions:

- Digital Employee Square and My Digital Employees pagination
- compact Popular Tags and avatar pickers with expand/collapse
- viewport-bounded new-employee form with internal scrolling
- hidden dedicated model/inference controls in the employee editor
- three-row preview and remainder toggle for workspace changed files

## Full-main merge audit

A no-commit merge was executed in an isolated temporary worktree. It produced
195 unresolved paths (95 backend, 85 frontend, and 15 elsewhere), so merging
all 632 commits directly into the active dirty enterprise worktree is not a
safe unattended operation.

The largest near-term integration boundary is official Projects: this branch
already owns enterprise migration `0019_digital_employee_agents.py`, while the
official Projects series introduces its own migrations and broad thread/query
changes. That work should be integrated as a dedicated, reviewable stream with
migration renumbering, service-level regression tests, and deployment rollback
coverage.

## Ongoing rule

For future updates, prefer an official patch and its tests whenever it exists.
Only implement a local extension when current official main has no matching
behavior. Preserve enterprise security, SSO, storage, routing, and migration
boundaries when an upstream patch conflicts with them.

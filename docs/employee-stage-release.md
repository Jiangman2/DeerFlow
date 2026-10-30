# Employee-scoped stages and model configuration

Build and roll only Gateway for the code delta. Source base:
199426d234acc71845e85c18b5ea17dee2c05a70. Keep the existing enterprise TLS fix.

`staged_execution.enabled: true` plus `team_agents: [制度文生图智能体]` enables
stage orchestration for the actual shared team employee, for browser and OA runs.
Personal file/DB agents that shadow the name are excluded. Thread allowlists remain
supported. Runtime context cannot grant stage access. Keep Skill ownership and
all existing stage budgets, receipt validation, continuation and terminal rules.
An agent change resets a previous target's stage plan; stages remain per-thread.
Simple questions can remain direct. This does not force every OA document into
stages or replace OA business/output validation. Keep OA owner and callback settings.

Rename only the V4.1 display_name to DeepSeek-V4.1-Flash. Preserve internal name
deepseek-v4.1-flash-1017, upstream model ID, credentials and URL. Put its entry first
in models for global fallback selection. Explicit saved choices and agent model
overrides retain precedence. The target employee currently has no model override.

Work (Feishu) /connect instructions have no punctuation after the connection code.
Other channel instruction wording is preserved. The existing multi-channel HTTP
test verifies each channel's exact expected wording.

The existing GET /api/competition/agent-usage endpoint reports employee clicks,
not LLM request counts. Configure COMPETITION_USAGE_API_KEY and
COMPETITION_USAGE_ALL_PUBLISHED_ENABLED=true in the actual Gateway environment.
All currently published team/external employees are queryable by exact display
name; new publications become available automatically. Private and withdrawn
employees remain excluded. Duplicate names return 409 instead of guessing.
This mode supersedes COMPETITION_USAGE_AGENTS_JSON; keeping the flag off preserves
the previous explicit alias roster mode. Each request rechecks authentication and
publication before consulting the identity/publication-bound count cache.
This does not publish an unauthenticated board or change the admin display list.
Reuse a valid existing key; otherwise generate it privately during configuration.
Never put a real key in source, browser code, terminal output or a release receipt.

The new schema field requires the new Gateway image. Switch image and candidate
ConfigMap in the same Pod-template change. Rolling back to the previous image
requires restoring its old ConfigMap as well; it cannot parse team_agents.
No database migration is needed. Revalidate deployed images, runtime config,
Work connection instructions, usage authorization, and real browser/OA runs.
Local synthetic tests and draft generation do not establish production acceptance.
The exact real-OA fixture test may be skipped only when its two fixture paths are
absent. Record its test name and exact reason as NOT_TESTED_REAL_FIXTURES_REQUIRED;
never count it as passed. Every other skip, failure or error blocks preparation.

/**
 * LangGraph super-step budget for interactive lead-agent runs.
 *
 * Super-steps include middleware and tool nodes, so a long, progressing Skill
 * workflow can legitimately use many more steps than its LLM-call count. The
 * Gateway clamps this value. Loop detection and token budgets are separate
 * operator-configured controls; increasing this value does not enable them.
 */
export const LEAD_AGENT_RECURSION_LIMIT = 10_000;

/**
 * LangGraph super-step budget for interactive lead-agent runs.
 *
 * Super-steps include middleware and tool nodes, so a long, progressing Skill
 * workflow can legitimately use many more steps than its LLM-call count. The
 * Gateway clamps this value, while loop detection and token budgets remain
 * independently bounded.
 */
export const LEAD_AGENT_RECURSION_LIMIT = 2000;

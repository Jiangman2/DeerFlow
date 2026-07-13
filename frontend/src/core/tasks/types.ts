import type { AIMessage } from "@langchain/langgraph-sdk";

import type { TokenUsage } from "../messages/usage";

import type { SubtaskStep } from "./steps";

export interface Subtask {
  id: string;
  status: "in_progress" | "completed" | "failed";
  subagent_type: string;
  description: string;
  /** Effective DeerFlow model selected for this subagent run. */
  modelName?: string;
  /** Latest cumulative token snapshot reported while the subagent runs. */
  usage?: TokenUsage;
  latestMessage?: AIMessage;
  /**
   * Full ordered step history (assistant turns + tool outputs) of the subagent.
   * Accumulated live from task events and backfilled on expand for historical runs.
   */
  steps?: SubtaskStep[];
  prompt: string;
  result?: string;
  error?: string;
  /** Optional guardrail stop reason carried by newer backends. */
  stopReason?: string;
}

export interface AgentModelSettings {
  temperature?: number | null;
  max_tokens?: number | null;
}

export type ReasoningEffort = "low" | "medium" | "high";

export interface Agent {
  name: string;
  display_name?: string | null;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  skills: string[] | null;
  model_settings?: AgentModelSettings | null;
  thinking_enabled?: boolean | null;
  reasoning_effort?: ReasoningEffort | null;
  category_id?: string | null;
  tags?: string[];
  avatar_id?: string | null;
  avatar_url?: string | null;
  soul?: string | null;
  source?: "personal" | "team" | "legacy" | null;
  published_by_user_id?: string | null;
  published_by_current_user?: boolean;
  external_url?: string | null;
  is_hot?: boolean;
  hot_version?: number;
  hot_identity?: string;
  creator_name?: string | null;
}

export interface CreateAgentRequest {
  name: string;
  display_name?: string | null;
  description?: string;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  model_settings?: AgentModelSettings | null;
  thinking_enabled?: boolean | null;
  reasoning_effort?: ReasoningEffort | null;
  category_id?: string | null;
  tags?: string[];
  avatar_id?: string | null;
  soul?: string;
}

export interface UpdateAgentRequest {
  display_name?: string | null;
  description?: string | null;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  model_settings?: AgentModelSettings | null;
  thinking_enabled?: boolean | null;
  reasoning_effort?: ReasoningEffort | null;
  category_id?: string | null;
  tags?: string[] | null;
  avatar_id?: string | null;
  soul?: string | null;
}

export interface DigitalEmployeeCategory {
  id: string;
  name: string;
  description: string;
  sort_order: number;
  domain_group: "unified" | "distributed";
}

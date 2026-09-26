export interface AgentModelSettings {
  temperature?: number | null;
  max_tokens?: number | null;
}

export type ReasoningEffort = "low" | "medium" | "high";
export type AgentSource = "personal" | "team" | "legacy";

export interface AgentKnowledgeItem {
  type: "kb" | "file";
  id: string;
  name: string;
  kb_id?: string;
  kb_name?: string;
  tenant_id?: string;
}

export interface AgentKnowledgeScope {
  version: 1;
  mode: "inherit" | "disabled" | "selected";
  items: AgentKnowledgeItem[];
}

export interface Agent {
  name: string;
  display_name?: string | null;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  skills: string[] | null;
  knowledge_scope?: AgentKnowledgeScope | null;
  model_settings?: AgentModelSettings | null;
  thinking_enabled?: boolean | null;
  reasoning_effort?: ReasoningEffort | null;
  category_id?: string | null;
  tags?: string[];
  avatar_id?: string | null;
  avatar_url?: string | null;
  soul?: string | null;
  draft_revision?: string | null;
  source?: AgentSource | null;
  published_by_user_id?: string | null;
  published_by_current_user?: boolean;
  publication_id?: string | null;
  publication_revision?: string | null;
  publication_state?: "published" | "withdrawn" | null;
  external_url?: string | null;
}

export interface CreateAgentRequest {
  name: string;
  display_name?: string | null;
  description?: string;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  knowledge_scope?: AgentKnowledgeScope | null;
  model_settings?: AgentModelSettings | null;
  thinking_enabled?: boolean | null;
  reasoning_effort?: ReasoningEffort | null;
  category_id?: string | null;
  tags?: string[];
  avatar_id?: string | null;
  soul?: string;
}

export interface UpdateAgentRequest {
  expected_draft_revision?: string;
  display_name?: string | null;
  description?: string | null;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  knowledge_scope?: AgentKnowledgeScope | null;
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

export interface Agent {
  name: string;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  skills: string[] | null;
  soul?: string | null;
  source?: "personal" | "team" | "legacy" | null;
  published_by_user_id?: string | null;
  published_by_current_user?: boolean;
  external_url?: string | null;
}

export interface CreateAgentRequest {
  name: string;
  description?: string;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  soul?: string;
}

export interface UpdateAgentRequest {
  description?: string | null;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  soul?: string | null;
}

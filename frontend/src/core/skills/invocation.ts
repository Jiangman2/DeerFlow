import { parseAgentSource } from "@/core/agents/routing";
import type { AgentSource } from "@/core/agents/types";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { SkillRequestError } from "./api";
import type { Skill } from "./type";

export type InvocationSkill = Pick<Skill, "name" | "description" | "enabled">;

export interface EmployeeSkillScope {
  name: string;
  source?: AgentSource;
  threadId?: string;
}

// Discovery is metadata-only. Source delivery and invocation authorization
// remain server boundaries, never a fallback to the viewer's installed Skills.
export async function loadEmployeeInvocationSkills(
  scope: EmployeeSkillScope,
  signal?: AbortSignal,
): Promise<InvocationSkill[]> {
  const query = new URLSearchParams();
  const source = parseAgentSource(scope.source);
  if (source) query.set("source", source);
  if (scope.threadId) query.set("thread_id", scope.threadId);
  const response = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(scope.name)}/invocation-skills${query.size ? `?${query}` : ""}`,
    { signal },
  );
  if (!response.ok) {
    throw new SkillRequestError(response.status, "Employee skills unavailable");
  }
  const data: unknown = await response.json();
  if (
    !data ||
    typeof data !== "object" ||
    !("skills" in data) ||
    !Array.isArray(data.skills)
  ) {
    throw new Error("Invalid employee skill catalog");
  }
  return data.skills.map((item: unknown) => {
    if (
      !item ||
      typeof item !== "object" ||
      !("name" in item) ||
      typeof item.name !== "string" ||
      !("description" in item) ||
      typeof item.description !== "string" ||
      !("enabled" in item) ||
      typeof item.enabled !== "boolean"
    ) {
      throw new Error("Invalid employee skill catalog entry");
    }
    return {
      name: item.name,
      description: item.description,
      enabled: item.enabled,
    };
  });
}

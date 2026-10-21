import type { Agent } from "./types";

export interface EmployeeMetadata {
  name: string;
  source: string;
  is_hot: boolean;
  version: number;
  identity_token: string;
  creator_name: string | null;
}

export function employeeSource(agent: Agent) {
  return agent.external_url ? "external" : (agent.source ?? "personal");
}

export function applyEmployeeMetadata(
  agents: Agent[],
  entries: EmployeeMetadata[],
) {
  const metadata = new Map(
    entries.map((entry) => [`${entry.source}:${entry.name}`, entry]),
  );
  return agents.map((agent) => {
    const entry = metadata.get(`${employeeSource(agent)}:${agent.name}`);
    return {
      ...agent,
      is_hot: entry?.is_hot === true,
      hot_version: entry?.version ?? 0,
      hot_identity: entry?.identity_token,
      creator_name: entry?.creator_name ?? null,
    };
  });
}

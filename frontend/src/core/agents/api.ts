import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  Agent,
  AgentSource,
  CreateAgentRequest,
  DigitalEmployeeCategory,
  UpdateAgentRequest,
} from "./types";

const BACKEND_UNAVAILABLE_STATUSES = new Set([502, 503, 504]);

export class AgentNameCheckError extends Error {
  constructor(
    message: string,
    public readonly reason: "backend_unreachable" | "request_failed",
    /**
     * Raw backend `detail` string when the failure came from a backend
     * response carrying one. `null` when no detail was provided (e.g.
     * network-layer failure, empty response body, unparseable body) — in
     * which case `message` is a generated fallback like "Failed to check
     * agent name: Bad Gateway" and the UI should prefer its own localized
     * fallback instead of surfacing the generated string.
     */
    public readonly detail: string | null = null,
  ) {
    super(message);
    this.name = "AgentNameCheckError";
  }
}

export class AgentsApiDisabledError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AgentsApiDisabledError";
  }
}

function isAgentsApiDisabledDetail(detail: string | undefined): boolean {
  return typeof detail === "string" && detail.includes("agents_api.enabled");
}

export class AgentLoadError extends Error {
  constructor(public readonly status: number) {
    super("The selected employee could not be loaded");
    this.name = "AgentLoadError";
  }
}

export function shouldRetryAgentLoad(failureCount: number, error: Error) {
  if (
    error instanceof AgentLoadError &&
    [400, 401, 403, 404, 410, 422].includes(error.status)
  ) {
    return false;
  }
  return failureCount < 1;
}

export async function listAgents(): Promise<Agent[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/agents`);
  if (!res.ok) throw new Error(`Failed to load agents: ${res.statusText}`);
  const data = (await res.json()) as { agents: Agent[] };
  return data.agents;
}

export async function listTeamAgents(): Promise<Agent[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/team-agents`);
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to load team agents: ${res.statusText}`,
    );
  }
  const data = (await res.json()) as { agents: Agent[] };
  return data.agents;
}

export async function getAgent(
  name: string,
  source?: AgentSource,
): Promise<Agent> {
  const suffix = source ? `?source=${encodeURIComponent(source)}` : "";
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}${suffix}`,
  );
  if (!res.ok) throw new AgentLoadError(res.status);
  return res.json() as Promise<Agent>;
}

export async function createAgent(request: CreateAgentRequest): Promise<Agent> {
  const res = await fetch(`${getBackendBaseURL()}/api/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    if (isAgentsApiDisabledDetail(err.detail)) {
      throw new AgentsApiDisabledError(err.detail!);
    }
    throw new Error(err.detail ?? `Failed to create agent: ${res.statusText}`);
  }
  return res.json() as Promise<Agent>;
}

export class AgentDraftError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "AgentDraftError";
  }
}

export async function updateAgent(
  name: string,
  request: UpdateAgentRequest,
): Promise<Agent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new AgentDraftError(res.status, typeof err.detail === "string" ? err.detail : "Failed to save employee draft");
  }
  return res.json() as Promise<Agent>;
}

export async function deleteAgent(name: string, expectedDraftRevision: string): Promise<void> {
  if (!expectedDraftRevision) throw new AgentDraftError(428, "Reload the employee before deleting");
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}?expected_draft_revision=${encodeURIComponent(expectedDraftRevision)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new AgentDraftError(res.status, typeof err.detail === "string" ? err.detail : "Failed to delete employee");
  }
}

export class AgentPublicationError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "AgentPublicationError";
  }
}

export async function publishAgent(
  name: string,
  expectedRevision?: string | null,
  expectedDraftRevision?: string,
  grantIds: string[] = [],
): Promise<Agent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}/publish`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        overwrite: true,
        expected_revision: expectedRevision ?? null,
        expected_draft_revision: expectedDraftRevision,
        grant_ids: grantIds,
      }),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new AgentPublicationError(
      res.status,
      typeof err.detail === "string"
        ? err.detail
        : "Failed to publish employee",
    );
  }
  return res.json() as Promise<Agent>;
}

export async function unpublishTeamAgent(
  name: string,
  expectedRevision?: string | null,
): Promise<void> {
  const suffix = expectedRevision
    ? `?expected_revision=${encodeURIComponent(expectedRevision)}`
    : "";
  const res = await fetch(
    `${getBackendBaseURL()}/api/team-agents/${encodeURIComponent(name)}${suffix}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new AgentPublicationError(
      res.status,
      typeof err.detail === "string"
        ? err.detail
        : "Failed to withdraw employee",
    );
  }
}

export async function listDigitalEmployeeCategories(): Promise<
  DigitalEmployeeCategory[]
> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/digital-employee-categories`,
  );
  if (!res.ok) {
    throw new Error(`Failed to load digital employee categories`);
  }
  const data = (await res.json()) as { items: DigitalEmployeeCategory[] };
  return data.items;
}

export async function uploadAgentAvatar(name: string, file: File, expectedDraftRevision: string) {
  if (!expectedDraftRevision) throw new AgentDraftError(428, "Reload the employee draft before changing its avatar");
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}/avatar?expected_draft_revision=${encodeURIComponent(expectedDraftRevision)}`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new AgentDraftError(res.status, typeof err.detail === "string" ? err.detail : "Failed to upload avatar");
  }
  return res.json() as Promise<Agent>;
}

export async function deleteAgentAvatar(name: string, expectedDraftRevision: string): Promise<Agent> {
  if (!expectedDraftRevision) throw new AgentDraftError(428, "Reload the employee draft before changing its avatar");
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}/avatar?expected_draft_revision=${encodeURIComponent(expectedDraftRevision)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new AgentDraftError(res.status, typeof err.detail === "string" ? err.detail : "Failed to remove avatar");
  }
  return res.json() as Promise<Agent>;
}

interface FeaturesResponse {
  agents_api: { enabled: boolean };
}

export async function fetchAgentsApiEnabled(): Promise<boolean> {
  const res = await fetch(`${getBackendBaseURL()}/api/features`);
  if (!res.ok) {
    throw new Error(`Failed to load features: ${res.statusText}`);
  }
  const data = (await res.json()) as FeaturesResponse;
  return data.agents_api.enabled;
}

export async function recordExternalAgentClick(
  agentName: string,
): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agent-analytics/external-click`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName }),
      keepalive: true,
    },
  );
  if (!res.ok) {
    throw new Error(`Failed to record external agent click: ${res.status}`);
  }
}

export async function checkAgentName(
  name: string,
): Promise<{ available: boolean; name: string }> {
  let res: Response;
  try {
    res = await fetch(
      `${getBackendBaseURL()}/api/agents/check?name=${encodeURIComponent(name)}`,
    );
  } catch {
    throw new AgentNameCheckError(
      "Could not reach the backend service.",
      "backend_unreachable",
    );
  }

  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    if (isAgentsApiDisabledDetail(err.detail)) {
      throw new AgentsApiDisabledError(err.detail!);
    }
    if (BACKEND_UNAVAILABLE_STATUSES.has(res.status)) {
      throw new AgentNameCheckError(
        "Could not reach the backend service.",
        "backend_unreachable",
      );
    }
    const backendDetail = typeof err.detail === "string" ? err.detail : null;
    throw new AgentNameCheckError(
      backendDetail ?? `Failed to check agent name: ${res.statusText}`,
      "request_failed",
      backendDetail,
    );
  }
  return res.json() as Promise<{ available: boolean; name: string }>;
}

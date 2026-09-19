import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  DashboardFilters,
  DashboardSummary,
  DashboardRunRankings,
  ExistingSessionList,
  FailedRunList,
  KnowledgeUsageSummary,
  RegisteredUserSessionList,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    await throwGatewayApiError(
      response,
      `服务暂时不可用（HTTP ${response.status}），请稍后重试`,
    );
  }
  return (await response.json()) as T;
}

export async function getDashboardSummary(
  filters: DashboardFilters | number = 168,
): Promise<DashboardSummary> {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };
  const params = new URLSearchParams({
    hours: String(resolved.hours ?? 168),
    limit: "10",
    include_knowledge: "false",
    include_business_details: "false",
  });
  for (const [key, value] of Object.entries(resolved)) {
    if (
      key === "hours" ||
      value === undefined ||
      value === null ||
      value === "" ||
      value === "all"
    ) {
      continue;
    }
    params.set(key, String(value));
  }
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/summary?${params}`,
  );
  return readJson<DashboardSummary>(response);
}

export async function getDashboardKnowledge(
  filters: DashboardFilters | number = 168,
): Promise<KnowledgeUsageSummary> {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };
  const params = new URLSearchParams({
    hours: String(resolved.hours ?? 168),
    limit: "10",
  });
  for (const [key, value] of Object.entries(resolved)) {
    if (
      key === "hours" ||
      key === "status" ||
      key === "model" ||
      key === "agent" ||
      key === "feature" ||
      key === "value_profile" ||
      value === undefined ||
      value === null ||
      value === "" ||
      value === "all"
    ) {
      continue;
    }
    params.set(key, String(value));
  }
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/knowledge?${params}`,
  );
  return readJson<KnowledgeUsageSummary>(response);
}

export async function getDashboardRunRankings(
  filters: DashboardFilters | number = 168,
): Promise<DashboardRunRankings> {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };
  const params = new URLSearchParams({
    hours: String(resolved.hours ?? 168),
    limit: "10",
  });
  for (const key of ["start_at", "end_at"] as const) {
    const value = resolved[key];
    if (value) params.set(key, String(value));
  }
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/run-rankings?${params}`,
  );
  return readJson<DashboardRunRankings>(response);
}

export async function getDashboardUsers(
  limit = 5,
  offset = 0,
  query = "",
): Promise<RegisteredUserSessionList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (query.trim()) params.set("q", query.trim());
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/users?${params}`,
  );
  return readJson<RegisteredUserSessionList>(response);
}

export async function getDashboardUserSessions(
  userId: string,
  limit = 5,
  offset = 0,
): Promise<ExistingSessionList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/users/${encodeURIComponent(
      userId,
    )}/sessions?${params}`,
  );
  return readJson<ExistingSessionList>(response);
}

export async function getDashboardFailures(
  limit = 5,
  offset = 0,
  query = "",
): Promise<FailedRunList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (query.trim()) params.set("q", query.trim());
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/failures?${params}`,
  );
  return readJson<FailedRunList>(response);
}

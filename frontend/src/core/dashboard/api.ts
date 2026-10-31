import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  DashboardDateBounds,
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

export async function getDashboardDateBounds(
  signal?: AbortSignal,
): Promise<DashboardDateBounds> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(cancel, 15_000);
  try {
    const response = await fetch(
      `${getBackendBaseURL()}/api/admin/dashboard/date-bounds`,
      { signal: controller.signal },
    );
    return await readJson<DashboardDateBounds>(response);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

export async function getDashboardSummary(
  filters: DashboardFilters | number = 168,
  signal?: AbortSignal,
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
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 60_000);
  try {
    const response = await fetch(
      `${getBackendBaseURL()}/api/admin/dashboard/summary?${params}`,
      { signal: controller.signal },
    );
    return await readJson<DashboardSummary>(response);
  } catch (error) {
    if (timedOut) throw new Error("统计加载超时，请缩小区间或稍后重试");
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
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

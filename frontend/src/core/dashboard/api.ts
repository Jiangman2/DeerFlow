import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  DashboardFilters,
  DashboardSummary,
  ExistingSessionList,
  FailedRunList,
  RegisteredUserSessionList,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
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

export async function getDashboardUsers(
  limit = 5,
  offset = 0,
): Promise<RegisteredUserSessionList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
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
): Promise<FailedRunList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/dashboard/failures?${params}`,
  );
  return readJson<FailedRunList>(response);
}

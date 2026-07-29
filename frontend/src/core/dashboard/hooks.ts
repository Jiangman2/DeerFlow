import { useQuery } from "@tanstack/react-query";

import {
  getDashboardSummary,
  getDashboardUsers,
  getDashboardUserSessions,
} from "./api";
import type { DashboardFilters } from "./types";

export function useDashboardSummary(filters: DashboardFilters | number = 168) {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };

  return useQuery({
    queryKey: ["dashboard", "summary", resolved],
    queryFn: () => getDashboardSummary(resolved),
    staleTime: 5_000,
    refetchInterval: 15_000,
  });
}

export function useDashboardUsers(limit = 5, offset = 0) {
  return useQuery({
    queryKey: ["dashboard", "registered-users", limit, offset],
    queryFn: () => getDashboardUsers(limit, offset),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });
}

export function useDashboardUserSessions(
  userId: string | null,
  limit = 5,
  offset = 0,
) {
  return useQuery({
    queryKey: [
      "dashboard",
      "registered-users",
      userId,
      "sessions",
      limit,
      offset,
    ],
    queryFn: () => getDashboardUserSessions(userId ?? "", limit, offset),
    enabled: Boolean(userId),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });
}

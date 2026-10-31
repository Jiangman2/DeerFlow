import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  getDashboardDateBounds,
  getDashboardFailures,
  getDashboardKnowledge,
  getDashboardRunRankings,
  getDashboardSummary,
  getDashboardUsers,
  getDashboardUserSessions,
} from "./api";
import type { DashboardFilters } from "./types";

export function useDashboardDateBounds() {
  return useQuery({
    queryKey: ["dashboard", "date-bounds"],
    queryFn: ({ signal }) => getDashboardDateBounds(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: true,
    retry: false,
  });
}

export function dashboardRefreshInterval(
  filters: DashboardFilters,
): number | false {
  if (filters.start_at || filters.end_at || (filters.hours ?? 168) > 168) {
    return false;
  }
  return 60_000;
}

export function useDashboardSummary(filters: DashboardFilters | number = 168) {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };

  return useQuery({
    queryKey: ["dashboard", "summary", resolved],
    queryFn: ({ signal }) => getDashboardSummary(resolved, signal),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: dashboardRefreshInterval(resolved),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useDashboardKnowledge(
  filters: DashboardFilters | number = 168,
) {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };

  return useQuery({
    queryKey: ["dashboard", "knowledge", resolved],
    queryFn: () => getDashboardKnowledge(resolved),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    refetchInterval:
      dashboardRefreshInterval(resolved) === false ? false : 5 * 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
}

export function useDashboardRunRankings(
  filters: DashboardFilters | number = 168,
  enabled = true,
) {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };
  return useQuery({
    queryKey: ["dashboard", "run-rankings", resolved],
    queryFn: () => getDashboardRunRankings(resolved),
    placeholderData: keepPreviousData,
    enabled,
    staleTime: 60_000,
    refetchInterval: dashboardRefreshInterval(resolved),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
}

export function useDashboardUsers(limit = 5, offset = 0, query = "") {
  return useQuery({
    queryKey: ["dashboard", "registered-users", limit, offset, query],
    queryFn: () => getDashboardUsers(limit, offset, query),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
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
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
}

export function useDashboardFailures(limit = 5, offset = 0, query = "") {
  return useQuery({
    queryKey: ["dashboard", "failures", limit, offset, query],
    queryFn: () => getDashboardFailures(limit, offset, query),
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
}

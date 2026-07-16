import { useQuery } from "@tanstack/react-query";

import { getDashboardSummary } from "./api";
import type { DashboardFilters } from "./types";

export function useDashboardSummary(filters: DashboardFilters | number = 168) {
  const resolved =
    typeof filters === "number" ? { hours: filters } : { ...filters };

  return useQuery({
    queryKey: ["dashboard", "summary", resolved],
    queryFn: () => getDashboardSummary(resolved),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getObservabilityAnalytics,
  getObservabilitySnapshot,
  getRunDiagnostics,
  getTraceEvents,
  getUserActivity,
  recordRunDisposition,
  searchAuditLogs,
  searchTraces,
} from "./api";
import type { AuditFilters, DispositionInput, TraceFilters } from "./types";

export function useObservabilitySnapshot(
  hours = 24,
  startAt?: string,
  endAt?: string,
) {
  return useQuery({
    queryKey: ["observability", "snapshot", hours, startAt, endAt],
    queryFn: () => getObservabilitySnapshot(hours, startAt, endAt),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useObservabilityAnalytics(
  hours = 24,
  bucket: "hour" | "day" = "hour",
  startAt?: string,
  endAt?: string,
) {
  return useQuery({
    queryKey: ["observability", "analytics", hours, bucket, startAt, endAt],
    queryFn: () => getObservabilityAnalytics(hours, bucket, startAt, endAt),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });
}

export function useUserActivity(hours = 24, startAt?: string, endAt?: string) {
  return useQuery({
    queryKey: ["observability", "user-activity", hours, startAt, endAt],
    queryFn: () => getUserActivity(hours, startAt, endAt),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });
}

export function useTraceEvents(runId: string | null) {
  return useQuery({
    queryKey: ["observability", "trace-events", runId],
    queryFn: () => getTraceEvents(runId ?? ""),
    enabled: Boolean(runId),
  });
}

export function useObservabilityTraces(filters: TraceFilters) {
  return useQuery({
    queryKey: ["observability", "traces", filters],
    queryFn: () => searchTraces(filters),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useObservabilityAuditLogs(filters: AuditFilters) {
  return useQuery({
    queryKey: ["observability", "audit-logs", filters],
    queryFn: () => searchAuditLogs(filters),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useRunDiagnostics(runId: string | null) {
  return useQuery({
    queryKey: ["observability", "run-diagnostics", runId],
    queryFn: () => getRunDiagnostics(runId ?? ""),
    enabled: Boolean(runId),
  });
}

export function useRecordRunDisposition(runId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: DispositionInput) =>
      recordRunDisposition(runId ?? "", input),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["observability", "run-diagnostics", runId],
        }),
        queryClient.invalidateQueries({ queryKey: ["observability"] }),
      ]);
    },
  });
}

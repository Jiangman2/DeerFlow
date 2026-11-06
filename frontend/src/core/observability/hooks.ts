import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  analyzeChannelAudit,
  getChannelAudit,
  getObservabilityAnalytics,
  getObservabilitySnapshot,
  getRunDiagnostics,
  getTraceEvents,
  getUserActivity,
  recordRunDisposition,
  searchAuditLogs,
  searchTraces,
  sendChannelAudit,
} from "./api";
import type {
  AuditFilters,
  ChannelAuditFilters,
  DispositionInput,
  TraceFilters,
} from "./types";

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
    queryFn: ({ signal }) => getRunDiagnostics(runId ?? "", signal),
    enabled: Boolean(runId),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
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

export function useChannelAudit(filters: ChannelAuditFilters) {
  return useQuery({
    queryKey: ["observability", "channel-audit", filters],
    queryFn: () => getChannelAudit(filters),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useSendChannelAudit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      filters: ChannelAuditFilters & { chat_id?: string; title?: string },
    ) => sendChannelAudit(filters),
    onSuccess: async () => {
      // The send itself is audited server-side, so refresh the feed to show it.
      await queryClient.invalidateQueries({ queryKey: ["observability"] });
    },
  });
}

export function useAnalyzeChannelAudit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      filters: ChannelAuditFilters & {
        question?: string;
        model_name?: string;
        send_to_channel?: boolean;
        chat_id?: string;
        title?: string;
      },
    ) => analyzeChannelAudit(filters),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["observability"] });
    },
  });
}

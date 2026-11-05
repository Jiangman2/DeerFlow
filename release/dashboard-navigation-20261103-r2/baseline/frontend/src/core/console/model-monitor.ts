"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { getBackendBaseURL } from "@/core/config";

const count = z.number().nonnegative();
const counts = z.object({
  started: count,
  success: count,
  error: count,
  timeout: count,
  cancelled: count,
  duration_seconds: count,
});
const monitorSchema = z.object({
  status: z.enum([
    "observed",
    "not_configured",
    "no_fresh_samples",
    "unavailable",
  ]),
  generated_at: z.number(),
  reporting_processes: count,
  dropped_observations: count.optional(),
  oa_jobs: z
    .object({
      status: z.enum(["observed", "no_observations"]),
      running: count,
      queued: count,
      freshness_seconds: count,
    })
    .optional(),
  series: z
    .array(
      counts.extend({
        key: z.string(),
        kind: z.string(),
        model: z.string(),
        operation: z.string(),
        active: count,
        peak_concurrency: count.nullable(),
        average_seconds: count.nullable(),
      }),
    )
    .max(1024),
  trend: z
    .array(
      z.object({
        at: z.number(),
        series: z.record(counts).nullable(),
        concurrency: z.record(count).nullable(),
      }),
    )
    .max(60),
});
export type ModelMonitorData = z.infer<typeof monitorSchema>;

export function useModelMonitor(minutes: 15 | 60) {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["admin-model-monitor", user?.id, minutes],
    enabled: !!user?.id,
    queryFn: async ({ signal }) => {
      const response = await fetch(
        `${getBackendBaseURL()}/api/admin/model-monitor?minutes=${minutes}`,
        { signal },
      );
      if (!response.ok) throw new Error("Model monitoring unavailable");
      return monitorSchema.parse(await response.json());
    },
    staleTime: 15_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

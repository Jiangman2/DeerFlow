"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { getBackendBaseURL } from "@/core/config";

const usageSchema = z.object({
  scope: z.literal("current_user_recorded_platform_runs"),
  total_tokens: z.number().int().nonnegative(),
  input_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().nonnegative(),
  unclassified_tokens: z.number().int().nonnegative(),
  run_count: z.number().int().nonnegative(),
  recorded_since: z.string().nullable(),
});

export async function getAccountUsage(signal?: AbortSignal) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/console/token-summary`,
    { signal },
  );
  if (!response.ok) throw new Error("Account usage is unavailable");
  return usageSchema.parse(await response.json());
}

export function useAccountUsage() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["account-token-summary", user?.id],
    queryFn: ({ signal }) => getAccountUsage(signal),
    enabled: !!user?.id,
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}

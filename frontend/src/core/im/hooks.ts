import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchImCapabilities,
  fetchImGroupThreads,
  fetchImSessionTree,
  searchImThreads,
} from "./api";
import { IM_QUERY_KEY } from "./tree";
import type { ImSessionTreeParams, ImThreadPageParams } from "./types";

export function useImCapabilities() {
  return useQuery({
    queryKey: [...IM_QUERY_KEY, "capabilities"],
    queryFn: fetchImCapabilities,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useImSessionTree(
  params: ImSessionTreeParams = {},
  enabled = true,
) {
  return useQuery({
    queryKey: [
      ...IM_QUERY_KEY,
      "tree",
      params.groupsLimit ?? null,
      params.threadsPerGroup ?? null,
      params.groupOffset ?? 0,
    ],
    queryFn: () => fetchImSessionTree(params),
    staleTime: 30_000,
    enabled,
  });
}

export function useImGroupThreads(
  chatId: string | null,
  params: ImThreadPageParams = {},
  enabled = true,
) {
  return useQuery({
    queryKey: [
      ...IM_QUERY_KEY,
      "group",
      chatId,
      params.limit ?? null,
      params.cursor ?? null,
    ],
    queryFn: () => fetchImGroupThreads(chatId ?? "", params),
    staleTime: 30_000,
    enabled: enabled && Boolean(chatId),
  });
}

export function useImThreadSearch(query: string, enabled = true) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: [...IM_QUERY_KEY, "search", trimmed],
    queryFn: () => searchImThreads(trimmed),
    staleTime: 10_000,
    enabled: enabled && trimmed.length > 0,
  });
}

export function useInvalidateImQueries() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: IM_QUERY_KEY });
}

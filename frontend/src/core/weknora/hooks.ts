import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWeKnoraKnowledgeBase,
  createWeKnoraManualKnowledge,
  listAllWeKnoraKnowledge,
  listWeKnoraKnowledgeBases,
  listWeKnoraTags,
  searchAllWeKnoraKnowledge,
  uploadWeKnoraKnowledgeFile,
} from "./api";

export const weknoraKnowledgeBasesQueryKey = ["weknora", "knowledge-bases"];

export function weknoraKnowledgeQueryKey(
  knowledgeBaseId: string | undefined,
  tenantId?: string | number,
) {
  return ["weknora", "knowledge", knowledgeBaseId, tenantId];
}

export function weknoraTagsQueryKey(
  knowledgeBaseId: string | undefined,
  tenantId?: string | number,
) {
  return ["weknora", "tags", knowledgeBaseId, tenantId];
}

export function useWeKnoraKnowledgeBases(userId?: string) {
  return useQuery({
    queryKey: [...weknoraKnowledgeBasesQueryKey, userId ?? "anonymous"],
    queryFn: listWeKnoraKnowledgeBases,
    staleTime: 15_000,
  });
}

export function useWeKnoraKnowledge(
  knowledgeBaseId: string | undefined,
  tenantId?: string | number,
) {
  return useQuery({
    queryKey: weknoraKnowledgeQueryKey(knowledgeBaseId, tenantId),
    queryFn: () => listAllWeKnoraKnowledge(knowledgeBaseId!, tenantId),
    enabled: Boolean(knowledgeBaseId),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((item) => item.parse_status === "processing")
        ? 5_000
        : false;
    },
  });
}

export function useWeKnoraTags(
  knowledgeBaseId: string | undefined,
  tenantId?: string | number,
) {
  return useQuery({
    queryKey: weknoraTagsQueryKey(knowledgeBaseId, tenantId),
    queryFn: () => listWeKnoraTags(knowledgeBaseId!, tenantId),
    enabled: Boolean(knowledgeBaseId),
    staleTime: 30_000,
  });
}

export function useWeKnoraKnowledgeSearch(keyword: string, enabled = true) {
  return useQuery({
    queryKey: ["weknora", "knowledge-search", keyword.trim()],
    queryFn: () => searchAllWeKnoraKnowledge(keyword),
    enabled,
    staleTime: keyword.trim() ? 10_000 : 30_000,
  });
}

export function useCreateWeKnoraKnowledgeBase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createWeKnoraKnowledgeBase,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: weknoraKnowledgeBasesQueryKey,
      });
    },
  });
}

export function useUploadWeKnoraKnowledgeFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadWeKnoraKnowledgeFile,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: weknoraKnowledgeBasesQueryKey,
      });
      void queryClient.invalidateQueries({
        queryKey: weknoraKnowledgeQueryKey(
          variables.knowledgeBaseId,
          variables.tenantId,
        ),
      });
    },
  });
}

export function useCreateWeKnoraManualKnowledge() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createWeKnoraManualKnowledge,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: weknoraKnowledgeBasesQueryKey,
      });
      void queryClient.invalidateQueries({
        queryKey: weknoraKnowledgeQueryKey(
          variables.knowledgeBaseId,
          variables.tenantId,
        ),
      });
    },
  });
}

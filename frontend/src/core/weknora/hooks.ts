import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWeKnoraKnowledgeBase,
  createWeKnoraManualKnowledge,
  listWeKnoraKnowledge,
  listWeKnoraKnowledgeBases,
  uploadWeKnoraKnowledgeFile,
} from "./api";

export const weknoraKnowledgeBasesQueryKey = ["weknora", "knowledge-bases"];

export function weknoraKnowledgeQueryKey(knowledgeBaseId: string | undefined) {
  return ["weknora", "knowledge", knowledgeBaseId];
}

export function useWeKnoraKnowledgeBases() {
  return useQuery({
    queryKey: weknoraKnowledgeBasesQueryKey,
    queryFn: listWeKnoraKnowledgeBases,
    staleTime: 15_000,
  });
}

export function useWeKnoraKnowledge(knowledgeBaseId: string | undefined) {
  return useQuery({
    queryKey: weknoraKnowledgeQueryKey(knowledgeBaseId),
    queryFn: () => listWeKnoraKnowledge(knowledgeBaseId!),
    enabled: Boolean(knowledgeBaseId),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((item) => item.parse_status === "processing")
        ? 5_000
        : false;
    },
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
        queryKey: weknoraKnowledgeQueryKey(variables.knowledgeBaseId),
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
        queryKey: weknoraKnowledgeQueryKey(variables.knowledgeBaseId),
      });
    },
  });
}

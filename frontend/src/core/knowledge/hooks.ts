import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  copyKnowledgeFile,
  createKnowledgeFolder,
  deleteKnowledgeFolder,
  deleteKnowledgeFile,
  listKnowledgeFiles,
  moveKnowledgeFile,
  moveKnowledgeFolder,
  reindexKnowledgeFile,
  renameKnowledgeFolder,
  searchKnowledge,
  uploadKnowledgeFiles,
} from "./api";
import type {
  KnowledgeFileCopyInput,
  KnowledgeFileMoveInput,
  KnowledgeFolderCreateInput,
  KnowledgeFolderMoveInput,
  KnowledgeFolderRenameInput,
  KnowledgeListResponse,
} from "./types";

const KNOWLEDGE_QUERY_KEY = ["knowledge"];

export function useKnowledgeFiles() {
  const { data, isLoading, error } = useQuery({
    queryKey: KNOWLEDGE_QUERY_KEY,
    queryFn: () => listKnowledgeFiles(),
  });
  return {
    files: data?.files ?? [],
    folders: data?.folders ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
  };
}

export function useUploadKnowledgeFiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => uploadKnowledgeFiles(files),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useDeleteKnowledgeFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (fileId: string) => deleteKnowledgeFile(fileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useDeleteKnowledgeFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (folderPath: string) => deleteKnowledgeFolder(folderPath),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useCreateKnowledgeFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: KnowledgeFolderCreateInput) =>
      createKnowledgeFolder(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useMoveKnowledgeFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: KnowledgeFileMoveInput) => moveKnowledgeFile(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useCopyKnowledgeFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: KnowledgeFileCopyInput) => copyKnowledgeFile(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useRenameKnowledgeFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: KnowledgeFolderRenameInput) =>
      renameKnowledgeFolder(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useMoveKnowledgeFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: KnowledgeFolderMoveInput) => moveKnowledgeFolder(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KNOWLEDGE_QUERY_KEY });
    },
  });
}

export function useReindexKnowledgeFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (fileId: string) => reindexKnowledgeFile(fileId),
    onSuccess: (file) => {
      queryClient.setQueryData<KnowledgeListResponse>(
        KNOWLEDGE_QUERY_KEY,
        (current) => {
          if (!current) return current;
          return {
            ...current,
            files: current.files.map((item) =>
              item.id === file.id ? file : item,
            ),
          };
        },
      );
    },
  });
}

export function useSearchKnowledge() {
  return useMutation({
    mutationFn: ({
      query,
      limit,
      scopePaths,
    }: {
      query: string;
      limit?: number;
      scopePaths?: string[];
    }) => searchKnowledge(query, limit, scopePaths),
  });
}

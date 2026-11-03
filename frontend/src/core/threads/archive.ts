import { useMutation, useQueryClient } from "@tanstack/react-query";

import { patchThreadMetadata, type ThreadMetadataPatchResponse } from "./api";
import {
  INFINITE_THREADS_QUERY_KEY_PREFIX,
  setThreadMetadataInCaches,
} from "./hooks";
import { THREAD_ARCHIVED_METADATA_KEY } from "./utils";

export type ArchiveThreadVariables = {
  threadId: string;
  archived: boolean;
};

export type ArchiveThreadOptions = {
  onSuccess?: (
    data: ThreadMetadataPatchResponse,
    variables: ArchiveThreadVariables,
  ) => void;
  onError?: (error: Error, variables: ArchiveThreadVariables) => void;
};

export function useArchiveThread(options: ArchiveThreadOptions = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, archived }: ArchiveThreadVariables) =>
      patchThreadMetadata(threadId, {
        [THREAD_ARCHIVED_METADATA_KEY]: archived,
      }),
    // Keep callbacks at mutation level: a sidebar row may unmount as soon as
    // its archive membership changes, which drops per-call handlers.
    async onSuccess(_response, { threadId, archived }) {
      await Promise.all([
        queryClient.cancelQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        }),
        queryClient.cancelQueries({ queryKey: ["threads", "search"] }),
        queryClient.cancelQueries({
          queryKey: ["thread", "metadata", threadId],
        }),
      ]);
      setThreadMetadataInCaches(queryClient, threadId, {
        [THREAD_ARCHIVED_METADATA_KEY]: archived,
      });
      await Promise.all([
        queryClient.resetQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        }),
        queryClient.invalidateQueries({ queryKey: ["threads", "search"] }),
        queryClient.invalidateQueries({
          queryKey: ["thread", "metadata", threadId],
        }),
      ]);
      options.onSuccess?.(_response, { threadId, archived });
    },
    onError(error, variables) {
      options.onError?.(error, variables);
    },
  });
}

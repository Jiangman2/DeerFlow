import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { fetchScheduledTaskRuns } from "./api";
import type { ScheduledTaskRunStatus } from "./types";

export const RUN_HISTORY_PAGE_SIZE = 50;

export function useScheduledTaskRunHistory(
  taskId: string | undefined,
  status?: ScheduledTaskRunStatus,
) {
  const client = useQueryClient();
  const [position, setPosition] = useState({ taskId, status, page: 0 });
  const sameScope = position.taskId === taskId && position.status === status;
  const page = sameScope ? position.page : 0;
  if (!sameScope) {
    setPosition({ taskId, status, page: 0 });
  }
  const query = useQuery({
    queryKey: ["scheduled-tasks", "runs", taskId, status ?? "all", page],
    queryFn: ({ signal }) =>
      fetchScheduledTaskRuns(taskId ?? "", {
        limit: RUN_HISTORY_PAGE_SIZE + 1,
        offset: page * RUN_HISTORY_PAGE_SIZE,
        ...(status ? { status } : {}),
        signal,
      }),
    enabled: Boolean(taskId),
    refetchInterval: page === 0 ? 15000 : false,
    refetchIntervalInBackground: false,
    refetchOnMount: page === 0,
    refetchOnWindowFocus: page === 0,
    refetchOnReconnect: page === 0,
  });
  return {
    ...query,
    data: query.data?.slice(0, RUN_HISTORY_PAGE_SIZE),
    page,
    hasOlder: (query.data?.length ?? 0) > RUN_HISTORY_PAGE_SIZE,
    older: () => setPosition({ taskId, status, page: page + 1 }),
    newer: () => setPosition({ taskId, status, page: Math.max(0, page - 1) }),
    latest: () => {
      setPosition({ taskId, status, page: 0 });
      void client.invalidateQueries({
        queryKey: ["scheduled-tasks", "runs", taskId, status ?? "all", 0],
        exact: true,
      });
    },
  };
}

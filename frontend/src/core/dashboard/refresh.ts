import type { QueryClient } from "@tanstack/react-query";

type DashboardQueryClient = Pick<QueryClient, "refetchQueries">;

/**
 * Refresh every active query rendered by the dashboard, including queries
 * owned by nested panels such as registered users and expanded sessions.
 */
export async function refreshDashboardQueries(
  queryClient: DashboardQueryClient,
): Promise<void> {
  // Summary and top-N rankings share the intentionally tiny dashboard DB
  // admission budget. Refresh them serially so one admin click cannot create
  // competing analytical reads.
  await queryClient.refetchQueries({
    queryKey: ["dashboard", "summary"],
    type: "active",
  });
  await queryClient.refetchQueries({
    queryKey: ["dashboard", "run-rankings"],
    type: "active",
  });
  await Promise.all([
    queryClient.refetchQueries({
      queryKey: ["dashboard"],
      type: "active",
      predicate: (query) =>
        query.queryKey[1] !== "summary" && query.queryKey[1] !== "run-rankings",
    }),
    queryClient.refetchQueries({
      queryKey: ["observability"],
      type: "active",
    }),
  ]);
}

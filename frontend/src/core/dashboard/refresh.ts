import type { QueryClient } from "@tanstack/react-query";

type DashboardQueryClient = Pick<QueryClient, "refetchQueries">;

/**
 * Refresh every active query rendered by the dashboard, including queries
 * owned by nested panels such as registered users and expanded sessions.
 */
export async function refreshDashboardQueries(
  queryClient: DashboardQueryClient,
): Promise<void> {
  await Promise.all([
    queryClient.refetchQueries({
      queryKey: ["dashboard"],
      type: "active",
    }),
    queryClient.refetchQueries({
      queryKey: ["observability"],
      type: "active",
    }),
  ]);
}

import type { DashboardOverview } from "./types";

export interface DashboardHeadlineCounts {
  threadsCreatedInWindow: number;
  threadsCreatedAllTime: number;
  threadsRetainedAllTime: number;
  threadsDeletedAllTime: number;
  runsStartedInWindow: number;
  runsEnded: number;
  runsSuccessful: number;
  runsUnsuccessful: number;
  runsActive: number;
}

export function dashboardHeadlineCounts(
  overview?: DashboardOverview,
): DashboardHeadlineCounts {
  const threadsCreatedAllTime = overview?.all_time_threads ?? 0;
  const threadsRetainedAllTime = overview?.existing_threads ?? 0;

  return {
    threadsCreatedInWindow: overview?.threads ?? 0,
    threadsCreatedAllTime,
    threadsRetainedAllTime,
    threadsDeletedAllTime: Math.max(
      0,
      threadsCreatedAllTime - threadsRetainedAllTime,
    ),
    runsStartedInWindow: overview?.runs ?? 0,
    runsEnded: overview?.terminal_runs ?? 0,
    runsSuccessful: overview?.successful_runs ?? 0,
    runsUnsuccessful: overview?.failed_runs ?? 0,
    runsActive: overview?.running_runs ?? 0,
  };
}

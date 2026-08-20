import { describe, expect, it } from "@rstest/core";

import { dashboardHeadlineCounts } from "@/core/dashboard/headline";
import type { DashboardOverview } from "@/core/dashboard/types";

function overview(
  overrides: Partial<DashboardOverview> = {},
): DashboardOverview {
  return {
    total_users: 0,
    new_users: 0,
    active_users: 0,
    threads: 1,
    all_time_threads: 300,
    existing_threads: 156,
    runs: 3,
    terminal_runs: 3,
    successful_runs: 2,
    failed_runs: 1,
    running_runs: 0,
    success_rate: 2 / 3,
    avg_run_duration_ms: 0,
    total_tokens: 0,
    llm_calls: 0,
    artifact_runs: 0,
    knowledge_runs: 0,
    feedback_total: 0,
    satisfaction_rate: 0,
    estimated_minutes_saved: 0,
    ...overrides,
  };
}

describe("dashboardHeadlineCounts", () => {
  it("keeps thread inventory separate from runs started in the window", () => {
    expect(dashboardHeadlineCounts(overview())).toMatchObject({
      threadsCreatedInWindow: 1,
      threadsCreatedAllTime: 300,
      threadsRetainedAllTime: 156,
      threadsDeletedAllTime: 144,
      runsStartedInWindow: 3,
      runsEnded: 3,
      runsSuccessful: 2,
      runsUnsuccessful: 1,
      runsActive: 0,
    });
  });

  it("preserves the two run reconciliation rules", () => {
    const counts = dashboardHeadlineCounts(
      overview({
        runs: 39,
        terminal_runs: 37,
        successful_runs: 30,
        failed_runs: 7,
        running_runs: 2,
      }),
    );

    expect(counts.runsEnded + counts.runsActive).toBe(
      counts.runsStartedInWindow,
    );
    expect(counts.runsSuccessful + counts.runsUnsuccessful).toBe(
      counts.runsEnded,
    );
  });

  it("never reports a negative historical deletion count", () => {
    expect(
      dashboardHeadlineCounts(
        overview({ all_time_threads: 2, existing_threads: 3 }),
      ).threadsDeletedAllTime,
    ).toBe(0);
  });
});

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ScheduledTaskListFeedback } from "@/components/workspace/scheduled-task-list-feedback";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      scheduledTasks: {
        detail: {
          loading: "Loading tasks",
          loadFailed: "Could not load tasks",
          loadFailureDescription: "Please try again",
          noMatchesTitle: "No matches",
          noMatchesDescription: "Clear filters",
          emptyTitle: "No tasks yet",
          emptyDescription: "Create your first task",
          retry: "Reload",
        },
        filters: { clear: "Clear filters" },
        create: { title: "Create task" },
      },
    },
  }),
}));
afterEach(cleanup);
const props = {
  hasData: false,
  failed: false,
  fetching: true,
  filtered: false,
  onRetry: rs.fn(),
  onClear: rs.fn(),
  onCreate: rs.fn(),
};

describe("Scheduled task list feedback", () => {
  it("does not announce an empty list or offer creation before the first query resolves", () => {
    render(<ScheduledTaskListFeedback {...props} />);
    expect(screen.getByRole("status").textContent).toContain("Loading tasks");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("No tasks yet")).toBeNull();
  });
  it("offers retry for an unavailable list, including during a filtered view", () => {
    const retry = rs.fn();
    const { rerender } = render(
      <ScheduledTaskListFeedback
        {...props}
        failed
        filtered
        fetching={false}
        onRetry={retry}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "Could not load tasks",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(retry).toHaveBeenCalledTimes(1);
    rerender(
      <ScheduledTaskListFeedback {...props} failed filtered onRetry={retry} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("No tasks yet")).toBeNull();
  });
  it("clears an empty filter without offering task creation", () => {
    const clear = rs.fn();
    render(
      <ScheduledTaskListFeedback
        {...props}
        hasData
        filtered
        fetching={false}
        onClear={clear}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(clear).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Create task" })).toBeNull();
  });
  it("offers creation only for a successfully loaded, unfiltered empty list", () => {
    const create = rs.fn();
    render(
      <ScheduledTaskListFeedback
        {...props}
        hasData
        fetching={false}
        onCreate={create}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create task" }));
    expect(create).toHaveBeenCalledTimes(1);
  });
});

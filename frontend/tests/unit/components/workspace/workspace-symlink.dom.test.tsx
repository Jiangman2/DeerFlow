import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const { query } = rs.hoisted(() => ({ query: rs.fn() }));
rs.mock("@/core/workspace-changes/hooks", () => ({
  useWorkspaceChanges: query,
}));
rs.mock("@/core/artifacts/utils", () => ({
  resolveArtifactURL: (path: string) => `/api/artifacts/${path}`,
}));

import { WorkspaceChangeBadge } from "@/components/workspace/changes/workspace-change-badge";
import { I18nProvider } from "@/core/i18n/context";
import type { WorkspaceChangesResponse } from "@/core/workspace-changes/types";

function fixture(): WorkspaceChangesResponse {
  return {
    available: true,
    version: 1,
    summary: {
      created: 0,
      modified: 0,
      deleted: 0,
      symlink_created: 1,
      additions: 0,
      deletions: 0,
      truncated: false,
    },
    files: [
      {
        path: "/mnt/user-data/outputs/link.md",
        root: "outputs",
        status: "symlink_created",
        binary: false,
        sensitive: false,
        size_before: 3,
        size_after: 10,
        sha256_before: null,
        sha256_after: null,
        diff: "",
        diff_truncated: false,
        diff_unavailable_reason: "symlink",
        additions: 0,
        deletions: 0,
        symlink: true,
        symlink_target_before: null,
        symlink_target_after: "/private/secret",
      },
    ],
    limits: {},
  };
}

beforeEach(() => {
  query.mockReset();
});
afterEach(cleanup);

test("a link-only run remains visible, explains metadata-only review and cannot open target", () => {
  query.mockReturnValue({ data: fixture(), isLoading: false });
  render(
    <I18nProvider initialLocale="zh-CN">
      <WorkspaceChangeBadge threadId="thread" runId="run" />
    </I18nProvider>,
  );
  expect(
    screen.getByTestId("workspace-change-summary-row").textContent,
  ).toContain("link.md");
  fireEvent.click(
    screen.getByRole("button", { name: /查看变更|View changes/i }),
  );
  const trigger = screen.getByRole("button", {
    name: /\/mnt\/user-data\/outputs\/link.md/,
  });
  expect(trigger.textContent).toMatch(/符号链接|symlink/i);
  fireEvent.click(trigger);
  expect(
    screen.getByText(
      /符号链接变更，无法展示 diff|Symlink change. Diff unavailable/,
    ),
  ).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
  expect(document.body.textContent).not.toContain("/private/secret");
});

test("legacy ordinary file records still offer the existing open action", () => {
  const data = fixture();
  data.summary = {
    created: 1,
    modified: 0,
    deleted: 0,
    additions: 1,
    deletions: 0,
    truncated: false,
  };
  const file = data.files[0]!;
  file.status = "created";
  delete file.symlink;
  file.diff_unavailable_reason = null;
  query.mockReturnValue({ data, isLoading: false });
  render(
    <I18nProvider initialLocale="zh-CN">
      <WorkspaceChangeBadge threadId="thread" runId="run" />
    </I18nProvider>,
  );
  fireEvent.click(
    screen.getByRole("button", { name: /查看变更|View changes/i }),
  );
  expect(screen.getByRole("link").getAttribute("href")).toContain(
    "/api/artifacts/",
  );
});

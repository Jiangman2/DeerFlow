import { describe, expect, test } from "@rstest/core";

import {
  getChangedFileCount,
  getWorkspaceChangeBadgeLabel,
  getWorkspaceChangeLineClass,
  sortWorkspaceChanges,
} from "@/core/workspace-changes/summary";
import type {
  WorkspaceChangesResponse,
  WorkspaceFileChange,
} from "@/core/workspace-changes/types";

const changes: WorkspaceChangesResponse = {
  available: true,
  version: 1,
  summary: {
    created: 1,
    modified: 2,
    deleted: 0,
    additions: 12,
    deletions: 3,
    truncated: false,
  },
  files: [],
  limits: {},
};

describe("workspace change summary helpers", () => {
  test("counts link replacements while retaining legacy summary compatibility", () => {
    expect(
      getChangedFileCount({ ...changes.summary, symlink_created: 2 }),
    ).toBe(5);
    expect(getChangedFileCount(changes.summary)).toBe(3);
  });

  test("sorts link replacements with modifications without mutating input", () => {
    const files = [
      { status: "deleted", path: "z" },
      { status: "symlink_created", path: "b" },
      { status: "modified", path: "a" },
      { status: "created", path: "new" },
    ] as WorkspaceFileChange[];
    expect(sortWorkspaceChanges(files).map((file) => file.path)).toEqual([
      "new",
      "a",
      "b",
      "z",
    ]);
    expect(files[0]?.path).toBe("z");
  });

  test("counts created, modified, and deleted files", () => {
    expect(getChangedFileCount(changes.summary)).toBe(3);
  });

  test("formats the compact badge label", () => {
    expect(getWorkspaceChangeBadgeLabel(changes.summary)).toBe(
      "3 files changed +12 -3",
    );
  });

  test("classifies unified diff lines", () => {
    expect(getWorkspaceChangeLineClass("+new line")).toBe("addition");
    expect(getWorkspaceChangeLineClass("-old line")).toBe("deletion");
    expect(getWorkspaceChangeLineClass("@@ -1 +1 @@")).toBe("hunk");
    expect(getWorkspaceChangeLineClass(" unchanged")).toBe("context");
    expect(getWorkspaceChangeLineClass("+++ b/file.md")).toBe("meta");
    expect(getWorkspaceChangeLineClass("--- a/file.md")).toBe("meta");
  });

  test("treats content lines beginning with +++/--- as add/remove, not meta", () => {
    expect(getWorkspaceChangeLineClass("+++foo")).toBe("addition");
    expect(getWorkspaceChangeLineClass("---bar")).toBe("deletion");
  });
});

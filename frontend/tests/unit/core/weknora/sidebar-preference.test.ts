import { describe, expect, it } from "@rstest/core";

import {
  readKnowledgeSidebarCollapsed,
  writeKnowledgeSidebarCollapsed,
} from "@/core/weknora/sidebar-preference";

function createStorage(initialValue: string | null = null) {
  let value = initialValue;
  return {
    getItem: () => value,
    setItem: (_key: string, nextValue: string) => {
      value = nextValue;
    },
  };
}

describe("knowledge sidebar preference", () => {
  it("defaults to expanded when no preference exists", () => {
    expect(readKnowledgeSidebarCollapsed(createStorage())).toBe(false);
    expect(readKnowledgeSidebarCollapsed(undefined)).toBe(false);
  });

  it("persists and restores the collapsed state", () => {
    const storage = createStorage();

    writeKnowledgeSidebarCollapsed(storage, true);
    expect(readKnowledgeSidebarCollapsed(storage)).toBe(true);

    writeKnowledgeSidebarCollapsed(storage, false);
    expect(readKnowledgeSidebarCollapsed(storage)).toBe(false);
  });

  it("falls back safely when browser storage is blocked", () => {
    const blockedStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };

    expect(readKnowledgeSidebarCollapsed(blockedStorage)).toBe(false);
    expect(() =>
      writeKnowledgeSidebarCollapsed(blockedStorage, true),
    ).not.toThrow();
  });
});

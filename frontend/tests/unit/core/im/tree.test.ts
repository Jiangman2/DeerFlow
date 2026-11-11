import { describe, expect, test } from "@rstest/core";

import {
  countTreeThreads,
  groupDisplayName,
  groupStatusNotice,
  isImThread,
  mergeGroupThreadPage,
  threadDisplayTitle,
  treeIsEmpty,
} from "@/core/im/tree";
import type { ImGroupNode, ImSessionTree, ImThreadItem } from "@/core/im/types";
import type { AgentThread } from "@/core/threads/types";

function makeThread(metadata: Record<string, unknown> = {}): AgentThread {
  return {
    thread_id: "thread-1",
    metadata,
    values: { title: "title" },
  } as unknown as AgentThread;
}

function makeItem(overrides: Partial<ImThreadItem> = {}): ImThreadItem {
  return {
    thread_id: "thread-1",
    title: "问答",
    channel_type: "private",
    chat_id: "oc_dm",
    chat_name: "私聊",
    last_active_at: "2026-01-01T00:00:00Z",
    is_current: false,
    status: "active",
    ...overrides,
  };
}

function makeGroup(overrides: Partial<ImGroupNode> = {}): ImGroupNode {
  return {
    chat_id: "oc_group_x",
    name: "研发一群",
    name_source: "CHAT_INFO",
    chat_status: "ACTIVE",
    thread_count: 2,
    last_active_at: "2026-01-01T00:00:00Z",
    current_thread_id: null,
    threads: [makeItem({ thread_id: "x1", channel_type: "group" })],
    ...overrides,
  };
}

function makeTree(overrides: Partial<ImSessionTree> = {}): ImSessionTree {
  return {
    private: { threads: [], total: 0 },
    groups: [],
    groups_total: 0,
    ...overrides,
  };
}

describe("IM thread detection", () => {
  test("detects only im_channel metadata", () => {
    expect(
      isImThread(
        makeThread({
          channel_source: { type: "im_channel", provider: "feishu" },
        }),
      ),
    ).toBe(true);
    expect(isImThread(makeThread({}))).toBe(false);
    expect(isImThread(makeThread({ channel_source: { type: "web" } }))).toBe(
      false,
    );
  });
});

describe("IM session tree emptiness", () => {
  test("treats missing and empty trees as empty", () => {
    expect(treeIsEmpty(null)).toBe(true);
    expect(treeIsEmpty(undefined)).toBe(true);
    expect(treeIsEmpty(makeTree())).toBe(true);
  });

  test("reports content when either section has threads", () => {
    expect(
      treeIsEmpty(makeTree({ private: { threads: [makeItem()], total: 1 } })),
    ).toBe(false);
    expect(treeIsEmpty(makeTree({ groups: [makeGroup()] }))).toBe(false);
    expect(
      countTreeThreads(
        makeTree({
          private: { threads: [makeItem()], total: 1 },
          groups: [makeGroup()],
        }),
      ),
    ).toBe(2);
  });
});

describe("IM group status notices", () => {
  test("maps LEFT and DISSOLVED case-insensitively", () => {
    expect(groupStatusNotice("LEFT")).toBe("left");
    expect(groupStatusNotice(" left ")).toBe("left");
    expect(groupStatusNotice("DISSOLVED")).toBe("dissolved");
    expect(groupStatusNotice("ACTIVE")).toBeNull();
    expect(groupStatusNotice(null)).toBeNull();
  });
});

describe("IM display names", () => {
  test("prefers the thread title, then the chat name, then the id", () => {
    expect(threadDisplayTitle(makeItem({ title: "标题" }))).toBe("标题");
    expect(threadDisplayTitle(makeItem({ title: "  " }))).toBe("私聊");
    expect(threadDisplayTitle(makeItem({ title: null, chat_name: null }))).toBe(
      "thread-1",
    );
  });

  test("falls back to the chat id when a group has no cached name", () => {
    expect(groupDisplayName(makeGroup())).toBe("研发一群");
    expect(groupDisplayName({ name: null, chat_id: "oc_group_y" })).toBe(
      "oc_group_y",
    );
  });
});

describe("IM group thread pagination", () => {
  test("appends only unseen threads", () => {
    const group = makeGroup();
    const merged = mergeGroupThreadPage(group, [
      makeItem({ thread_id: "x1", channel_type: "group" }),
      makeItem({ thread_id: "x2", channel_type: "group" }),
    ]);
    expect(merged.threads.map((thread) => thread.thread_id)).toEqual([
      "x1",
      "x2",
    ]);
    expect(group.threads).toHaveLength(1);
  });
});

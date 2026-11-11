import { expect, type Page, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const PRIVATE_THREAD = "p2";
const GROUP_CHAT_ID = "oc_group_x";
const GROUP_THREAD = "x2";

const IM_THREAD_TITLE = "飞书私聊里的历史会话";

const WEB_THREAD = {
  thread_id: "00000000-0000-0000-0000-000000000101",
  title: "网页端的历史会话",
  updated_at: new Date(Date.UTC(2025, 5, 30, 12)).toISOString(),
};

const IM_METADATA_THREAD = {
  thread_id: "00000000-0000-0000-0000-000000000102",
  title: IM_THREAD_TITLE,
  updated_at: new Date(Date.UTC(2025, 5, 30, 11)).toISOString(),
  metadata: {
    channel_source: { type: "im_channel", provider: "feishu" },
  },
};

type TreeThread = {
  thread_id: string;
  title: string;
  channel_type: "private" | "group";
  chat_id: string;
  chat_name: string;
  last_active_at: string;
  is_current: boolean;
  status: string;
};

function thread(overrides: Partial<TreeThread>): TreeThread {
  return {
    thread_id: PRIVATE_THREAD,
    title: "私聊里的新会话",
    channel_type: "private",
    chat_id: "oc_dm_alice_bot",
    chat_name: "飞书私聊",
    last_active_at: "2025-06-30T12:00:00Z",
    is_current: true,
    status: "active",
    ...overrides,
  };
}

const TREE = {
  private: { threads: [thread({})], total: 1 },
  groups: [
    {
      chat_id: GROUP_CHAT_ID,
      name: "研发一群",
      name_source: "CHAT_INFO",
      chat_status: "ACTIVE",
      thread_count: 2,
      last_active_at: "2025-06-30T12:00:00Z",
      current_thread_id: GROUP_THREAD,
      threads: [
        thread({
          thread_id: GROUP_THREAD,
          title: "群里的新会话",
          channel_type: "group",
          chat_id: GROUP_CHAT_ID,
          chat_name: "研发一群",
        }),
      ],
    },
  ],
  groups_total: 1,
};

const EMPTY_TREE = {
  private: { threads: [], total: 0 },
  groups: [],
  groups_total: 0,
};

const SEARCH_RESULT = thread({
  thread_id: "x1",
  title: "昨天的方案",
  channel_type: "group",
  is_current: false,
  chat_id: GROUP_CHAT_ID,
  chat_name: "研发一群",
});

async function mockImEndpoints(
  page: Page,
  options: { enabled: boolean; tree?: typeof TREE | typeof EMPTY_TREE },
) {
  const tree = options.tree ?? TREE;
  await page.route("**/api/im/capabilities", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        im_session_tree_enabled: options.enabled,
        provider: "feishu",
        max_group_threads: 50,
        search_enabled: options.enabled,
      }),
    }),
  );
  await page.route("**/api/im/session-tree*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(tree),
    }),
  );
  await page.route("**/api/im/threads/search*", (route) => {
    const url = new URL(route.request().url());
    const query = url.searchParams.get("q") ?? "";
    const results =
      query.length > 0 && "方案".includes(query) ? [SEARCH_RESULT] : [];
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results, limit: 20, offset: 0 }),
    });
  });
  await page.route("**/api/im/groups/*/threads*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ threads: [], next_cursor: null }),
    }),
  );
}

test.describe("IM session tree", () => {
  test("shows Feishu private and group sections and hides IM threads from Web chats", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [WEB_THREAD, IM_METADATA_THREAD] });
    await mockImEndpoints(page, { enabled: true });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.getByText("Private", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await expect(sidebar.getByText("Group", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("研发一群")).toBeVisible();
    await expect(sidebar.getByText("私聊里的新会话")).toBeVisible();

    // IM threads are represented by the tree, not duplicated in Web chats.
    await expect(sidebar.getByText(WEB_THREAD.title)).toBeVisible();
    await expect(sidebar.getByText(IM_THREAD_TITLE)).toHaveCount(0);
  });

  test("marks the Feishu current conversation", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [WEB_THREAD] });
    await mockImEndpoints(page, { enabled: true });

    await page.goto("/workspace/chats/new");

    await expect(
      page.getByLabel("Current Feishu conversation").first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("expands a group and opens one of its threads", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [WEB_THREAD] });
    await mockImEndpoints(page, { enabled: true });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.getByText("研发一群")).toBeVisible({
      timeout: 15_000,
    });
    await expect(sidebar.getByText("群里的新会话")).toHaveCount(0);

    await sidebar.getByText("研发一群").click();
    const groupThread = sidebar.getByText("群里的新会话");
    await expect(groupThread).toBeVisible();

    await groupThread.click();
    await page.waitForURL(`**/workspace/chats/${GROUP_THREAD}`);
  });

  test("searches IM history and opens the matching thread", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [WEB_THREAD] });
    await mockImEndpoints(page, { enabled: true });

    await page.goto("/workspace/chats/new");

    const input = page.getByTestId("im-session-search-input");
    await expect(input).toBeVisible({ timeout: 15_000 });
    await input.fill("方案");

    const result = page.getByText("昨天的方案");
    await expect(result).toBeVisible();
    await expect(
      page
        .getByTestId("im-session-search-results")
        .getByText("Chat", { exact: true })
        .first(),
    ).toBeVisible();

    await result.click();
    await page.waitForURL("**/workspace/chats/x1");
  });

  test("keeps IM threads in Web chats while the feature is off", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [WEB_THREAD, IM_METADATA_THREAD] });
    await mockImEndpoints(page, { enabled: false });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.getByText(IM_THREAD_TITLE)).toBeVisible({
      timeout: 15_000,
    });
    await expect(sidebar.getByText("Private", { exact: true })).toHaveCount(0);
  });

  test("renders no empty directory for a tree without threads", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [WEB_THREAD] });
    await mockImEndpoints(page, { enabled: true, tree: EMPTY_TREE });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.getByText(WEB_THREAD.title)).toBeVisible({
      timeout: 15_000,
    });
    await expect(sidebar.getByText("Private", { exact: true })).toHaveCount(0);
    await expect(sidebar.getByText("Group", { exact: true })).toHaveCount(0);
  });
});

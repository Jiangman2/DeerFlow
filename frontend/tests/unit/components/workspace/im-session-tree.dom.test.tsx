import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SidebarProvider } from "@/components/ui/sidebar";
import { ImSessionTree } from "@/components/workspace/im-session-tree";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import {
  useImCapabilities,
  useImGroupThreads,
  useImSessionTree,
} from "@/core/im/hooks";
import type { ImSessionTree as ImSessionTreeData } from "@/core/im/types";

rs.mock("@/core/im/hooks", () => ({
  useImCapabilities: rs.fn(),
  useImGroupThreads: rs.fn(),
  useImSessionTree: rs.fn(),
  useImThreadSearch: () => ({ data: undefined, isFetching: false }),
}));
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: zhCN, locale: "zh-CN" }),
}));
rs.mock("next/navigation", () => ({
  usePathname: () => "/workspace/chats/other-thread",
}));

const mockedCapabilities = rs.mocked(useImCapabilities);
const mockedTree = rs.mocked(useImSessionTree);
const mockedGroupThreads = rs.mocked(useImGroupThreads);

function renderTree() {
  return render(
    <SidebarProvider>
      <ImSessionTree />
    </SidebarProvider>,
  );
}

function treeFixture(): ImSessionTreeData {
  return {
    private: {
      threads: [
        {
          thread_id: "p2",
          title: "私聊里的新会话",
          channel_type: "private",
          chat_id: "oc_dm_alice_bot",
          chat_name: "飞书私聊",
          last_active_at: "2026-01-02T00:00:00Z",
          is_current: true,
          status: "active",
        },
      ],
      total: 1,
    },
    groups: [
      {
        chat_id: "oc_group_x",
        name: "研发一群",
        name_source: "CHAT_INFO",
        chat_status: "ACTIVE",
        thread_count: 2,
        last_active_at: "2026-01-02T00:00:00Z",
        current_thread_id: "x2",
        threads: [
          {
            thread_id: "x2",
            title: "群里的新会话",
            channel_type: "group",
            chat_id: "oc_group_x",
            chat_name: "研发一群",
            last_active_at: "2026-01-02T00:00:00Z",
            is_current: true,
            status: "active",
          },
        ],
      },
    ],
    groups_total: 3,
  };
}

afterEach(cleanup);

beforeEach(() => {
  mockedCapabilities.mockReturnValue({
    data: {
      im_session_tree_enabled: true,
      provider: "feishu",
      max_group_threads: 50,
      search_enabled: true,
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useImCapabilities>);
  mockedTree.mockReturnValue({
    data: treeFixture(),
    isPending: false,
    isError: false,
    refetch: rs.fn(),
  } as unknown as ReturnType<typeof useImSessionTree>);
  mockedGroupThreads.mockReturnValue({
    data: undefined,
  } as unknown as ReturnType<typeof useImGroupThreads>);
});

describe("IM session tree", () => {
  it("renders the private section and group section without empty directories", () => {
    renderTree();
    expect(screen.getByText(zhCN.sidebar.privateSection)).toBeTruthy();
    expect(screen.getByText(zhCN.sidebar.groupSection)).toBeTruthy();
    expect(screen.getByText("私聊里的新会话")).toBeTruthy();
    expect(screen.getByText("研发一群")).toBeTruthy();
  });

  it("marks the Feishu current conversation with an accessible label", () => {
    renderTree();
    expect(
      screen.getAllByLabelText(zhCN.sidebar.imCurrentThread).length,
    ).toBeGreaterThan(0);
  });

  it("reveals a group's threads only after expanding it", () => {
    renderTree();
    expect(screen.queryByText("群里的新会话")).toBeNull();
    fireEvent.click(screen.getByText("研发一群"));
    expect(screen.getByText("群里的新会话")).toBeTruthy();
  });

  it("offers to show all groups when more groups exist", () => {
    renderTree();
    const button = screen.getByTestId("im-show-all-groups");
    expect(button.textContent).toContain("3");
    fireEvent.click(button);
    const lastCall = mockedTree.mock.calls.at(-1);
    expect(lastCall?.[0]).toMatchObject({ groupsLimit: 200 });
  });

  it("renders nothing while the feature flag is off", () => {
    mockedCapabilities.mockReturnValue({
      data: {
        im_session_tree_enabled: false,
        provider: "feishu",
        max_group_threads: 50,
        search_enabled: false,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useImCapabilities>);
    renderTree();
    expect(screen.queryByText(zhCN.sidebar.privateSection)).toBeNull();
    expect(screen.queryAllByTestId("im-session-thread")).toHaveLength(0);
  });

  it("renders nothing for an empty tree", () => {
    mockedTree.mockReturnValue({
      data: { private: { threads: [], total: 0 }, groups: [], groups_total: 0 },
      isPending: false,
      isError: false,
      refetch: rs.fn(),
    } as unknown as ReturnType<typeof useImSessionTree>);
    renderTree();
    expect(screen.queryByText(zhCN.sidebar.privateSection)).toBeNull();
    expect(screen.queryAllByTestId("im-session-thread")).toHaveLength(0);
  });

  it("surfaces a load failure with an explicit retry", () => {
    const refetch = rs.fn();
    mockedTree.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useImSessionTree>);
    renderTree();
    expect(screen.getByText(zhCN.sidebar.imLoadFailed)).toBeTruthy();
    fireEvent.click(screen.getByText(zhCN.sidebar.imRetry));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
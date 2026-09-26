import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { PropsWithChildren } from "react";

const { rename, notify, thread } = rs.hoisted(() => ({
  rename: rs.fn(),
  notify: rs.fn(),
  thread: {
    thread_id: "thread-1",
    values: { title: "Original" },
    metadata: {},
    created_at: "2026-09-25T00:00:00Z",
    updated_at: "2026-09-25T00:00:00Z",
  },
}));

rs.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: [] }) }));
rs.mock("next/navigation", () => ({
  usePathname: () => "/workspace/chats/thread-1",
}));
rs.mock("next/link", () => ({
  default: ({ children }: PropsWithChildren) => <span>{children}</span>,
}));
rs.mock("sonner", () => ({ toast: { error: notify } }));
rs.mock("@/env", () => ({ env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" } }));
rs.mock("@/core/api", () => ({ getAPIClient: rs.fn() }));
rs.mock("@/core/threads/hooks", () => ({
  useInfiniteThreads: () => ({
    data: { pages: [[thread]] },
    hasNextPage: false,
  }),
  usePinThread: () => ({ mutate: rs.fn() }),
  useRenameThread: () => ({ mutate: rename }),
}));
rs.mock("@/components/workspace/thread-delete-dialog", () => ({
  useThreadDeleteDialog: () => rs.fn(),
}));
rs.mock("@/components/workspace/use-thread-archive-action", () => ({
  useThreadArchiveAction: () => ({ isPending: false }),
}));
rs.mock("@/components/ui/sidebar", () => {
  const Wrapper = ({ children }: PropsWithChildren) => <div>{children}</div>;
  return Object.fromEntries(
    [
      "SidebarGroup",
      "SidebarGroupContent",
      "SidebarGroupLabel",
      "SidebarMenu",
      "SidebarMenuAction",
      "SidebarMenuButton",
      "SidebarMenuItem",
    ].map((name) => [name, Wrapper]),
  );
});
// Exercise the actual list's callback/state behavior, not Radix positioning.
rs.mock("@/components/ui/dropdown-menu", () => {
  const Wrapper = ({ children }: PropsWithChildren) => <div>{children}</div>;
  return {
    ...Object.fromEntries(
      [
        "DropdownMenu",
        "DropdownMenuContent",
        "DropdownMenuSeparator",
        "DropdownMenuSub",
        "DropdownMenuSubContent",
        "DropdownMenuSubTrigger",
        "DropdownMenuTrigger",
      ].map((name) => [name, Wrapper]),
    ),
    DropdownMenuItem: ({
      children,
      onSelect,
    }: PropsWithChildren<{ onSelect: () => void }>) => (
      <button onClick={onSelect}>{children}</button>
    ),
  };
});
rs.mock("@/components/ui/dialog", () => {
  const Wrapper = ({ children }: PropsWithChildren) => <div>{children}</div>;
  return {
    Dialog: ({ children, open }: PropsWithChildren<{ open: boolean }>) =>
      open ? <div role="dialog">{children}</div> : null,
    DialogContent: Wrapper,
    DialogFooter: Wrapper,
    DialogHeader: Wrapper,
    DialogTitle: Wrapper,
  };
});

import { RecentChatList } from "@/components/workspace/recent-chat-list";
import { I18nProvider } from "@/core/i18n/context";

beforeEach(() => {
  rename.mockReset();
  notify.mockReset();
  document.cookie = "locale=zh-CN; path=/";
});
afterEach(cleanup);

function submit() {
  render(
    <I18nProvider initialLocale="zh-CN">
      <RecentChatList />
    </I18nProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "重命名" }));
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "新标题" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(rename).toHaveBeenCalledTimes(1);
  expect(rename.mock.calls[0]?.[0]).toEqual({
    threadId: "thread-1",
    title: "新标题",
  });
  return rename.mock.calls[0]?.[1] as {
    onError: (error: unknown) => void;
    onSuccess: () => void;
  };
}

test("rename conflict stays visible and retains user input without retrying", () => {
  const callbacks = submit();
  expect(screen.getByRole("dialog")).toBeDefined();
  act(() => callbacks.onError(new Error("Thread has a run in flight.")));
  expect(notify).toHaveBeenCalledWith("Thread has a run in flight.");
  expect(screen.getByRole<HTMLInputElement>("textbox").value).toBe("新标题");
  expect(rename).toHaveBeenCalledTimes(1);
});

test("rename closes only after server success", () => {
  const callbacks = submit();
  act(() => callbacks.onSuccess());
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(notify).not.toHaveBeenCalled();
});

test("rename failure without details uses Chinese fallback", () => {
  const callbacks = submit();
  act(() => callbacks.onError({}));
  expect(notify).toHaveBeenCalledWith("重命名会话失败。");
  expect(screen.getByRole<HTMLInputElement>("textbox").value).toBe("新标题");
});

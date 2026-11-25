import { afterEach, expect, test, rs } from "@rstest/core";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";

rs.mock("@/core/auth/AuthProvider", () => ({ useAuth: rs.fn() }));
import { useAuth } from "@/core/auth/AuthProvider";
import {
  HainengHistoryProvider,
  useHainengHistory,
} from "@/core/haineng/history";

function setUser(id: string) {
  rs.mocked(useAuth).mockReturnValue({ user: { id } } as ReturnType<
    typeof useAuth
  >);
}
function Wrapper({ children }: PropsWithChildren) {
  return <HainengHistoryProvider>{children}</HainengHistoryProvider>;
}
afterEach(() => {
  cleanup();
  localStorage.clear();
});

test("restores messages, draft and upstream session after remount", async () => {
  setUser("alice");
  const first = renderHook(useHainengHistory, { wrapper: Wrapper });
  await waitFor(() => expect(first.result.current.ready).toBe(true));
  const id = first.result.current.activeId;
  act(() =>
    first.result.current.save({
      id,
      title: "问题",
      updatedAt: 1,
      draft: "草稿",
      sessionId: "upstream-1",
      messages: [{ id: "m", role: "assistant", text: "回答", think: "" }],
    }),
  );
  first.unmount();
  const second = renderHook(useHainengHistory, { wrapper: Wrapper });
  await waitFor(() => expect(second.result.current.records).toHaveLength(1));
  expect(second.result.current.activeId).toBe(id);
  expect(second.result.current.records[0]?.draft).toBe("草稿");
  expect(second.result.current.records[0]?.sessionId).toBe("upstream-1");
  expect(second.result.current.records[0]?.messages[0]?.text).toBe("回答");
});

test("account change isolates history, busy blocks navigation, opening requests chat mode", async () => {
  setUser("alice");
  const hook = renderHook(useHainengHistory, { wrapper: Wrapper });
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  const id = hook.result.current.activeId;
  act(() =>
    hook.result.current.save({
      id,
      title: "Alice",
      updatedAt: 1,
      draft: "",
      messages: [],
    }),
  );
  act(() => hook.result.current.setBusy(true));
  act(() => hook.result.current.create());
  expect(hook.result.current.activeId).toBe(id);
  act(() => hook.result.current.setBusy(false));
  act(() => hook.result.current.open(id));
  expect(hook.result.current.openVersion).toBe(1);
  setUser("bob");
  hook.rerender();
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  expect(hook.result.current.records).toHaveLength(0);
  setUser("alice");
  hook.rerender();
  await waitFor(() => expect(hook.result.current.records).toHaveLength(1));
});

test("malformed storage is preserved and reported instead of overwritten", async () => {
  setUser("alice");
  const key = "deerflow:haineng:history:v1:live:chat:alice";
  localStorage.setItem(key, "broken");
  const hook = renderHook(useHainengHistory, { wrapper: Wrapper });
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  expect(hook.result.current.storageError).toBeTruthy();
  expect(localStorage.getItem(key)).toBe("broken");
});

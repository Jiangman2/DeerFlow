import { afterEach, expect, rs, test } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import {
  PptSubmissionChoiceProvider,
  PptSubmissionChoiceSurface,
  usePptSubmissionChoice,
} from "@/components/workspace/ppt-submission-choice";

rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ locale: "zh-CN" }) }));
afterEach(cleanup);
rs.mock("@/core/features/hooks", () => ({
  usePptConfirmationEnabled: () => true,
}));

type Handle = NonNullable<ReturnType<typeof usePptSubmissionChoice>>;
function Harness({
  done,
  expose,
  hasQuotes = false,
}: {
  done: (mode: string | null) => void;
  expose?: (h: Handle) => void;
  hasQuotes?: boolean;
}) {
  const h = usePptSubmissionChoice()!;
  expose?.(h);
  return (
    <>
      <button
        onClick={() => {
          void h
            .request({ text: "/ppt-master 测试需求", fileCount: 2, hasQuotes })
            .then((x) => done(x.isCurrent() ? x.mode : null));
        }}
      >
        Send
      </button>
      <main>
        <PptSubmissionChoiceSurface />
      </main>
    </>
  );
}
function mount(scope = "alice:thread", hasQuotes = false) {
  const done = rs.fn();
  let handle: Handle;
  const tree = (s: string) => (
    <PptSubmissionChoiceProvider scope={s}>
      <Harness
        done={done}
        hasQuotes={hasQuotes}
        expose={(h) => {
          handle = h;
        }}
      />
    </PptSubmissionChoiceProvider>
  );
  const view = render(tree(scope));
  return { done, view, handle: () => handle, tree };
}

test("no choice while typing; send shows manual default without starting", () => {
  const { done } = mount();
  expect(screen.queryByTestId("ppt-submission-choice")).toBeNull();
  fireEvent.click(screen.getByText("Send"));
  expect(screen.getByTestId("ppt-pending-request").textContent).toBe(
    "/ppt-master 测试需求",
  );
  expect(
    screen.getByRole<HTMLInputElement>("radio", { name: /^人工确认/ }).checked,
  ).toBe(true);
  expect(
    screen.getByRole<HTMLInputElement>("radio", { name: /^60 秒/ }).checked,
  ).toBe(false);
  expect(done).not.toHaveBeenCalled();
});

test("explicit start dispatches manual once, even with a stale second finish", async () => {
  const { done, handle } = mount();
  fireEvent.click(screen.getByText("Send"));
  const finish = handle().finish;
  fireEvent.click(screen.getByRole("button", { name: "开始任务" }));
  act(() => finish("auto_after_60s"));
  await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
  expect(done).toHaveBeenCalledWith("manual");
});

test("automatic is sent only after explicit radio and start", async () => {
  const { done } = mount();
  fireEvent.click(screen.getByText("Send"));
  fireEvent.click(screen.getByRole("radio", { name: /^60 秒/ }));
  expect(done).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "开始任务" }));
  await waitFor(() => expect(done).toHaveBeenCalledWith("auto_after_60s"));
});

test("return to edit resolves cancellation without a run choice", async () => {
  const { done } = mount();
  fireEvent.click(screen.getByText("Send"));
  fireEvent.click(screen.getByRole("button", { name: "返回修改" }));
  await waitFor(() => expect(done).toHaveBeenCalledWith(null));
  expect(screen.queryByTestId("ppt-submission-choice")).toBeNull();
});

test("quoted context cannot enable automatic consent", () => {
  const { done } = mount("alice:thread", true);
  fireEvent.click(screen.getByText("Send"));
  expect(
    screen.getByRole<HTMLInputElement>("radio", { name: /^60 秒/ }).disabled,
  ).toBe(true);
  expect(done).not.toHaveBeenCalled();
});

test("second request cannot replace the reviewed draft", async () => {
  const { handle } = mount();
  fireEvent.click(screen.getByText("Send"));
  await expect(
    handle().request({ text: "replacement", fileCount: 0, hasQuotes: false }),
  ).rejects.toThrow("already_pending");
  expect(screen.getByTestId("ppt-pending-request").textContent).toBe(
    "/ppt-master 测试需求",
  );
});

test("account or thread switch cancels the pending request", async () => {
  const { done, view, tree } = mount();
  fireEvent.click(screen.getByText("Send"));
  view.rerender(tree("bob:other-thread"));
  await waitFor(() => expect(done).toHaveBeenCalledWith(null));
  expect(screen.queryByTestId("ppt-submission-choice")).toBeNull();
});

test("old request and finish stay inert after alice-bob-alice", async () => {
  const { handle, view, tree, done } = mount();
  const old = handle();
  view.rerender(tree("bob:thread"));
  view.rerender(tree("alice:thread"));
  await expect(
    old.request({ text: "stale", fileCount: 0, hasQuotes: false }),
  ).rejects.toThrow();
  fireEvent.click(screen.getByText("Send"));
  act(() => old.finish("auto_after_60s"));
  expect(screen.getByTestId("ppt-submission-choice")).toBeTruthy();
  expect(done).not.toHaveBeenCalled();
});

test("unmount cancels without leaving an unresolved send", async () => {
  const { view, done } = mount();
  fireEvent.click(screen.getByText("Send"));
  view.unmount();
  await waitFor(() => expect(done).toHaveBeenCalledWith(null));
});

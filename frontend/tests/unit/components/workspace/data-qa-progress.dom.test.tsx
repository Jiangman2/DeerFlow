import { afterEach, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";

import { DataQAProgressView } from "@/components/workspace/messages/data-qa-progress";
import type {
  DataQAProgress,
  DataQAStage,
} from "@/core/threads/data-qa-progress";

rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ locale: "zh-CN" }) }));
afterEach(cleanup);
const progress = (stages: DataQAStage[]): DataQAProgress => ({
  threadId: "t",
  runId: "r",
  callId: "c",
  sequence: stages.length,
  stages,
});

test("shows progress before result, expands bounded steps, completes while assistant continues", () => {
  const { rerender, container } = render(<DataQAProgressView isLoading />);
  expect(screen.getByRole("status").textContent).toContain("等待查询进展");
  expect(container.querySelector(".animate-spin")).toBeTruthy();
  rerender(
    <DataQAProgressView
      isLoading
      progress={progress(["waiting", "processing"])}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "智能问数正在处理您的查询",
  );
  fireEvent.click(screen.getByText("查看问数进展"));
  expect(container.querySelectorAll("li")).toHaveLength(2);
  rerender(
    <DataQAProgressView
      isLoading
      progress={progress(["waiting", "processing", "answering"])}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("正在整理问数回答");
  rerender(
    <DataQAProgressView
      isLoading
      progress={progress(["waiting", "processing", "answering"])}
      result={{ status: "done", answer: "PRIVATE BUSINESS ANSWER" }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("智能问数调用完成");
  expect(container.querySelector(".animate-spin")).toBeNull();
  expect(container.textContent).not.toContain("PRIVATE BUSINESS ANSWER");
});

test("no events, failure, timeout, cancellation, clarification and stopped runs settle", () => {
  const { rerender, container } = render(
    <DataQAProgressView isLoading={false} />,
  );
  expect(screen.getByRole("status").textContent).toContain("未收到完成状态");
  for (const [result, label] of [
    [{ status: "done" }, "智能问数调用完成"],
    [{ error: "private" }, "智能问数调用失败"],
    [{ error: "private", code: "ReadTimeout" }, "智能问数等待超时"],
    [{ status: "empty" }, "未查询到可用结果"],
    [{ status: "interrupted" }, "需要补充查询条件"],
  ] as const) {
    rerender(<DataQAProgressView isLoading={false} result={result} />);
    expect(screen.getByRole("status").textContent).toBe(label);
    expect(container.querySelector(".animate-spin")).toBeNull();
  }
  rerender(
    <DataQAProgressView
      isLoading
      progress={progress(["waiting", "cancelled"])}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("已取消智能问数调用");
  expect(container.querySelector(".animate-spin")).toBeNull();
});

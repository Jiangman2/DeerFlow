import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { MessageTokenUsageList } from "@/components/workspace/messages/message-token-usage";
import { zhCN } from "@/core/i18n/locales/zh-CN";

rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ t: zhCN }) }));
afterEach(cleanup);

function answer(usage?: {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}): Message {
  return {
    id: "answer",
    type: "ai",
    content: "正在回答",
    ...(usage ? { usage_metadata: usage } : {}),
  } as Message;
}

test("streaming text waits for usage instead of displaying zero, then updates before completion", () => {
  const { rerender, container } = render(
    <MessageTokenUsageList enabled isLoading messages={[answer()]} />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    zhCN.tokenUsage.waitingForUsage,
  );
  expect(container.textContent).not.toContain("输入: 0");
  rerender(
    <MessageTokenUsageList
      enabled
      isLoading
      messages={[
        answer({ input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
      ]}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    zhCN.tokenUsage.waitingForUsage,
  );
  rerender(
    <MessageTokenUsageList
      enabled
      isLoading
      messages={[
        answer({ input_tokens: 20, output_tokens: 4, total_tokens: 24 }),
      ]}
    />,
  );
  expect(container.textContent).toContain("输入: 20");
  expect(container.textContent).toContain("输出: 4");
  expect(container.textContent).toContain("总计: 24");
  expect(container.textContent).toContain(zhCN.tokenUsage.reportedSoFar);
  rerender(
    <MessageTokenUsageList
      enabled
      messages={[
        answer({ input_tokens: 20, output_tokens: 9, total_tokens: 29 }),
      ]}
    />,
  );
  expect(container.textContent).toContain("总计: 29");
  expect(container.textContent).not.toContain(zhCN.tokenUsage.reportedSoFar);
});

test("stopped or completed replies without usage settle as unavailable", () => {
  const { rerender } = render(
    <MessageTokenUsageList enabled isLoading messages={[answer()]} />,
  );
  rerender(<MessageTokenUsageList enabled messages={[answer()]} />);
  expect(screen.getByRole("status").textContent).toBe(
    zhCN.tokenUsage.unavailableShort,
  );
});

test("completed explicit zero usage remains zero", () => {
  const { container } = render(
    <MessageTokenUsageList
      enabled
      messages={[
        answer({ input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
      ]}
    />,
  );
  expect(container.textContent).toContain("总计: 0");
  expect(screen.queryByRole("status")).toBeNull();
});

test("partial turn retains completed call usage while the next call streams", () => {
  const first = {
    ...answer({ input_tokens: 30, output_tokens: 7, total_tokens: 37 }),
    id: "call-1",
  };
  const { container } = render(
    <MessageTokenUsageList enabled isLoading messages={[first, answer()]} />,
  );
  expect(container.textContent).toContain("总计: 37");
  expect(container.textContent).toContain(zhCN.tokenUsage.reportedSoFar);
});

test("a completed earlier turn does not become pending while another turn streams", () => {
  render(
    <>
      <MessageTokenUsageList
        enabled
        messages={[
          answer({ input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
        ]}
      />
      <MessageTokenUsageList enabled isLoading messages={[answer()]} />
    </>,
  );
  expect(screen.getByText("总计: 0")).toBeTruthy();
  expect(screen.getAllByRole("status")).toHaveLength(1);
});

test("disabled statistics and turns without assistant messages remain hidden", () => {
  const { container, rerender } = render(
    <MessageTokenUsageList messages={[answer()]} />,
  );
  expect(container.textContent).toBe("");
  rerender(<MessageTokenUsageList enabled isLoading messages={[]} />);
  expect(container.textContent).toBe("");
});

import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

const STOPPED_TASK_DESCRIPTION = "Research stopped reload regression";
const STOPPED_TASK_PROMPT =
  "Investigate why the stopped subtask card should not remain running after reload.";

const stoppedSubtaskMessages = [
  {
    type: "human",
    id: "msg-human-stopped-subtask",
    content: [
      {
        type: "text",
        text: "Start a subtask and then stop before the task tool returns.",
      },
    ],
  },
  {
    type: "ai",
    id: "msg-ai-stopped-subtask",
    content: "",
    additional_kwargs: {},
    response_metadata: {},
    tool_calls: [
      {
        id: "call-stopped-subtask",
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: STOPPED_TASK_DESCRIPTION,
          prompt: STOPPED_TASK_PROMPT,
        },
        type: "tool_call",
      },
    ],
    invalid_tool_calls: [],
  },
];

const parallelCompletedSubtaskMessages = [
  {
    type: "human",
    id: "msg-human-parallel-subtasks",
    content: [{ type: "text", text: "Research alpha and beta in parallel." }],
  },
  {
    type: "ai",
    id: "msg-ai-parallel-subtasks",
    content: "",
    additional_kwargs: {},
    response_metadata: {},
    tool_calls: [
      {
        id: "call-parallel-alpha",
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: "Parallel alpha research",
          prompt: "Return ALPHA_RESULT.",
        },
        type: "tool_call",
      },
      {
        id: "call-parallel-beta",
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: "Parallel beta research",
          prompt: "Return BETA_RESULT.",
        },
        type: "tool_call",
      },
    ],
    invalid_tool_calls: [],
  },
  {
    type: "tool",
    id: "msg-tool-parallel-alpha",
    name: "task",
    tool_call_id: "call-parallel-alpha",
    content: "Task Succeeded. Result: ALPHA_RESULT",
    additional_kwargs: {
      subagent_status: "completed",
      subagent_result_brief: "ALPHA_RESULT",
      subagent_model_name: "qwen-bailian",
      subagent_token_usage: {
        input_tokens: 100,
        output_tokens: 5,
        total_tokens: 105,
      },
    },
  },
  {
    type: "tool",
    id: "msg-tool-parallel-beta",
    name: "task",
    tool_call_id: "call-parallel-beta",
    content: "Task Succeeded. Result: BETA_RESULT",
    additional_kwargs: {
      subagent_status: "completed",
      subagent_result_brief: "BETA_RESULT",
      subagent_model_name: "qwen-bailian",
      subagent_token_usage: {
        input_tokens: 120,
        output_tokens: 6,
        total_tokens: 126,
      },
    },
  },
  {
    type: "ai",
    id: "msg-ai-parallel-summary",
    content: "Combined ALPHA_RESULT and BETA_RESULT.",
    additional_kwargs: {},
    response_metadata: {},
    tool_calls: [],
    invalid_tool_calls: [],
  },
];

test.describe("Subtask card", () => {
  test("shows failed after a stopped task thread is reloaded", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Stopped subtask",
          updated_at: "2026-06-18T12:00:00Z",
          messages: stoppedSubtaskMessages,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.reload();

    await expect(page.getByText(STOPPED_TASK_DESCRIPTION)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Subtask failed")).toBeVisible();
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });

  test("keeps two completed parallel subtasks isolated after reload", async ({
    page,
  }) => {
    test.slow();
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Parallel completed subtasks",
          updated_at: "2026-07-16T12:00:00Z",
          messages: parallelCompletedSubtaskMessages,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.reload();

    await expect(page.getByText("Parallel alpha research")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Parallel beta research")).toBeVisible();
    await expect(page.getByText("Subtask completed")).toHaveCount(2);
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });
});

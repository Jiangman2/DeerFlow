import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

const STOPPED_TASK_DESCRIPTION =
  "Research stopped reload regression with an intentionally very long subtask description that must remain inside the card instead of widening the entire conversation viewport";
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

const pptMasterTimelineMessages = [
  {
    type: "human",
    id: "msg-human-ppt-master-timeline",
    content: [{ type: "text", text: "/ppt-master 创建 5 页演示文稿" }],
  },
  {
    type: "ai",
    id: "msg-ai-ppt-master-timeline",
    content: "PPTX 已交付。",
    additional_kwargs: {
      ppt_master_task: {
        task_id: "run-ppt-master-timeline",
        status: "completed",
        description: "ppt-master 官方工作流",
        model_name: "ppt-master-agent-host",
        result: "PPTX 已交付。",
        steps: [
          {
            message_index: 1,
            kind: "ai",
            text: "",
            tool_calls: [
              {
                name: "bash",
                args: {
                  command:
                    "python3 scripts/project_manager.py validate /mnt/user-data/workspace/projects/demo",
                },
              },
            ],
          },
          {
            message_index: 2,
            kind: "tool",
            text: "validation passed",
            tool_name: "bash",
          },
          {
            message_index: 3,
            kind: "ai",
            text: "Live Preview 已启动：[http://127.0.0.1:5050](http://127.0.0.1:5050/)",
          },
          {
            message_index: 4,
            kind: "tool",
            text: "preview server ready",
            tool_name: "bash",
          },
        ],
      },
    },
    response_metadata: {},
    tool_calls: [],
    invalid_tool_calls: [],
  },
];

test.describe("Subtask card", () => {
  test("shows incomplete after a stopped task thread is reloaded", async ({
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
    await expect(page.getByText("Subtask not completed")).toBeVisible();
    await expect(page.getByText("Running subtask")).toHaveCount(0);
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      )
      .toBe(true);
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

  test("shows persisted PPT Agent commands, outputs, and proxied Live Preview links", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "PPT Agent timeline",
          updated_at: "2026-08-11T00:00:00Z",
          messages: pptMasterTimelineMessages,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.getByText("ppt-master 官方工作流").click();

    await expect(page.getByText(/project_manager\.py validate/)).toBeVisible();
    await page
      .getByRole("button", {
        name: /View details: bash · validation passed/,
      })
      .click();
    await expect(page.getByText("Tool output")).toBeVisible();

    const previewLink = page.locator(
      `a[href="/api/threads/${MOCK_THREAD_ID}/live-preview/5050/"]`,
    );
    await expect(previewLink).toBeVisible();
    await expect(previewLink).not.toContainText(/127\.0\.0\.1|localhost/);
    await expect(previewLink).toHaveAttribute(
      "href",
      `/api/threads/${MOCK_THREAD_ID}/live-preview/5050/`,
    );
  });
});

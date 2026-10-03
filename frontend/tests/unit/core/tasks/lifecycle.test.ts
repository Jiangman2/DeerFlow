import { describe, expect, it } from "@rstest/core";

import {
  pptMasterTaskMessageToSubtaskUpdate,
  taskEventToSubtaskUpdate,
} from "@/core/tasks/lifecycle";

describe("taskEventToSubtaskUpdate", () => {
  it("maps a task-start event to the effective model for that task", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_started",
        task_id: "call-1",
        description: "Research auth",
        model_name: "claude-3-7-sonnet",
      }),
    ).toEqual({
      id: "call-1",
      status: "in_progress",
      subagent_type: "ppt-master",
      description: "Research auth",
      prompt: "",
      modelName: "claude-3-7-sonnet",
    });
  });

  it("maps a running event to its cumulative token snapshot", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_running",
        task_id: "call-1",
        model_name: "claude-3-7-sonnet",
        usage: {
          input_tokens: 100,
          output_tokens: 20,
          total_tokens: 120,
        },
      }),
    ).toEqual({
      id: "call-1",
      modelName: "claude-3-7-sonnet",
      usage: {
        inputTokens: 100,
        outputTokens: 20,
        totalTokens: 120,
      },
    });
  });

  it("maps external Agent terminal events without a synthetic task tool call", () => {
    expect(
      taskEventToSubtaskUpdate({
        type: "task_completed",
        task_id: "run-1",
        result: "Stage 1 complete",
        model_name: "ppt-master-agent-host",
      }),
    ).toEqual({
      id: "run-1",
      status: "completed",
      result: "Stage 1 complete",
      modelName: "ppt-master-agent-host",
    });
  });

  it("restores a completed ppt-master task card from durable message metadata", () => {
    expect(
      pptMasterTaskMessageToSubtaskUpdate({
        additional_kwargs: {
          ppt_master_task: {
            task_id: "run-2",
            status: "completed",
            description: "ppt-master 官方工作流",
            model_name: "ppt-master-agent-host",
            result: "PPTX delivered",
          },
        },
      }),
    ).toEqual({
      id: "run-2",
      status: "completed",
      subagent_type: "ppt-master",
      description: "ppt-master 官方工作流",
      prompt: "",
      modelName: "ppt-master-agent-host",
      result: "PPTX delivered",
    });
  });

  it("restores the real persisted step timeline embedded in demo metadata", () => {
    expect(
      pptMasterTaskMessageToSubtaskUpdate({
        additional_kwargs: {
          ppt_master_task: {
            task_id: "run-demo",
            status: "completed",
            steps: [
              {
                message_index: 2,
                kind: "tool",
                text: "validator passed",
                tool_name: "bash",
              },
              {
                message_index: 1,
                kind: "ai",
                text: "Run the official validator",
                tool_calls: [{ name: "bash", args: { command: "validate" } }],
              },
              { message_index: "invalid", kind: "tool" },
            ],
          },
        },
      })?.steps,
    ).toEqual([
      {
        message_index: 1,
        kind: "ai",
        text: "Run the official validator",
        tool_calls: [{ name: "bash", args: { command: "validate" } }],
      },
      {
        message_index: 2,
        kind: "tool",
        text: "validator passed",
        tool_name: "bash",
      },
    ]);
  });
});

import type { Agent } from "./types";

export interface AgentStarterPrompt {
  label: string;
  prompt: string;
}

/** Editable examples only: metadata matching never enables tools or permissions. */
export function getAgentStarterPrompts(
  agent: Agent | null | undefined,
  locale: string,
): AgentStarterPrompt[] {
  if (!locale.startsWith("zh")) {
    return [
      {
        label: "Explore this assistant",
        prompt:
          "Based on your configured role, what tasks can you help me with and what information should I provide?",
      },
      {
        label: "Plan a task",
        prompt:
          "Help me break down a task. First ask about my objective, available materials and expected output.",
      },
      {
        label: "Review my materials",
        prompt:
          "Help me organize the materials I provide into key points, questions and next steps. Wait for me to provide the materials.",
      },
    ];
  }
  const metadata = [
    agent?.name,
    agent?.display_name,
    agent?.description,
    ...(agent?.skills ?? []),
    ...(agent?.tags ?? []),
  ]
    .join(" ")
    .toLowerCase();
  if (/制度|合规|条款|policy|compliance/.test(metadata)) {
    return [
      {
        label: "梳理制度要点",
        prompt:
          "请根据我提供的制度材料，梳理适用范围、关键要求和注意事项，并标明依据。请先等我提供材料。",
      },
      {
        label: "对比制度版本",
        prompt:
          "我想对比两版制度的条款变化，请先告诉我需要提供哪些材料，再整理差异及可能影响。",
      },
      {
        label: "整理执行清单",
        prompt:
          "请根据我提供的制度和业务场景，整理可执行的检查清单。信息不足时请先向我确认。",
      },
    ];
  }
  if (/财务|预算|决算|finance|settlement/.test(metadata)) {
    return [
      {
        label: "整理编报材料",
        prompt:
          "请根据本次财务编报任务，帮我列出所需材料和待确认事项。请先询问报告类型和要求。",
      },
      {
        label: "核对收支数据",
        prompt:
          "请根据我提供的数据和口径，核对收支明细并列出需要确认的差异。请先等我提供数据。",
      },
      {
        label: "梳理报告提纲",
        prompt:
          "请结合我提供的报告要求和材料，整理报告提纲，并标出尚缺的信息。",
      },
    ];
  }
  return [
    {
      label: "了解可协助事项",
      prompt:
        "请结合你的职责介绍可以协助我的事项，以及开始任务前需要我提供的信息。",
    },
    {
      label: "梳理任务步骤",
      prompt:
        "请帮我梳理一项任务的执行步骤，先确认我的目标、现有材料和预期交付物。",
    },
    {
      label: "整理已有材料",
      prompt:
        "请帮我将提供的材料整理成要点、待确认问题和后续行动。请先等我提供材料。",
    },
  ];
}

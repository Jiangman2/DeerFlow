/** Additive locale-owned copy: do not replace the production locale catalog. */
import type { ExecutionNotice } from "@/core/tasks/ppt-confirmation";

type ProgressCopy = {
  progress: string;
  region: string;
  confirm: (stage: string) => string;
  stage1Received: string;
  preparing: string;
  confirmed: string;
  ownerError: string;
  review: string;
  unavailable: string;
  confirmation: string;
  preview: string;
  refresh: string;
  latest: string;
  noOwner: string;
  unspecified: string;
  labels: Record<string, string>;
  modes: Record<string, string>;
  notices: Record<ExecutionNotice, { title: string; detail: string }>;
};

const zh: ProgressCopy = {
  progress: "PPT 任务进展",
  region: "PPT 主对话进度与确认",
  confirm: (stage) => `${stage}：请你确认`,
  stage1Received: "Stage 1 确认已收到；下一阶段尚未准备好",
  preparing: "确认内容尚未准备好",
  confirmed: "确认已完成",
  ownerError: "确认页面报告准备异常",
  review: "请核对下面的实际建议，再打开确认页面提交；打开链接本身不会确认。",
  unavailable:
    "暂时无法核实最新确认状态，可能是页面服务已结束或连接异常；不会继续把旧状态标为待确认。",
  confirmation: "打开确认页面",
  preview: "打开预览",
  refresh: "刷新确认状态",
  latest: "最近一条 Agent 进度 / 系统通知（不作为确认凭据）",
  noOwner:
    "尚未取得可核实的官方确认状态。进度与可用预览入口会显示在这里，无需展开子任务。",
  unspecified: "未指定",
  modes: { free_design: "自由设计", templates: "使用模板" },
  labels: {
    audience: "受众",
    communication_intent: "沟通意图",
    audience_outcome: "期望受众达成的结果",
    core_message: "核心信息",
    delivery_context: "使用场景",
    artifact_afterlife: "会后用途",
    content_divergence: "内容调整空间",
    canvas: "画布",
    template_mode: "建议设计方式",
    preselected_count: "预选模板数",
    page_count: "页数",
    image_notes: "图片要求",
    template_application: "模板使用方案",
    design_spec_depth: "设计规格详细程度",
    direction_1: "设计方案 1",
    direction_2: "设计方案 2",
    direction_3: "设计方案 3",
  },
  notices: {
    stop_unconfirmed: {
      title: "停止尚未核实",
      detail:
        "已请求停止，但尚未确认外部 Agent 已退出。请先核实任务状态，不要反复发送继续或重试；页面确认不会清除这个状态。",
    },
    model_deadline: {
      title: "本轮因模型响应超时失败",
      detail:
        "Agent 报告模型请求达到总时限，并以失败结束本轮。这不是人工确认等待；下方保留已核实的确认状态和最近进度。",
    },
    tool_arguments: {
      title: "本轮因模型工具参数错误失败",
      detail:
        "Agent 报告工具参数校验失败。无效响应中的工具未被派发，不代表之前的工具没有执行；请保留已有项目和确认结果。",
    },
    failed: {
      title: "本轮 Agent 执行失败",
      detail:
        "子任务已报告失败，不是继续等待用户确认。下方显示已有确认状态和最近进度；系统不会自动重启或重复提交确认。",
    },
    incomplete: {
      title: "本轮状态尚未完整核实",
      detail:
        "主对话已结束，但没有完整的子任务结束回执；不能据此认定外部 Agent 已停止或任务已完成。请先核实状态。",
    },
    turn_ended: {
      title: "本轮 Agent 已结束",
      detail:
        "轮次结束不等于 PPT 已交付。页面确认结果会保留，但提交确认不会自动重启已结束的轮次。",
    },
  },
};

const en: ProgressCopy = {
  progress: "PPT progress",
  region: "PPT progress and confirmation",
  confirm: (stage) => `${stage}: your confirmation is needed`,
  stage1Received: "Stage 1 confirmed; the next stage is not ready yet",
  preparing: "Confirmation content is not ready yet",
  confirmed: "Confirmation received",
  ownerError: "The confirmation page reports a preparation error",
  review:
    "Review the actual recommendations, then submit on the confirmation page. Opening it does not confirm anything.",
  unavailable:
    "The latest confirmation state is unavailable. The page may have stopped or disconnected; an old waiting state is not treated as current.",
  confirmation: "Open confirmation page",
  preview: "Open preview",
  refresh: "Refresh confirmation state",
  latest: "Latest Agent progress / system notice (not a confirmation receipt)",
  noOwner:
    "No verified official confirmation state yet. Progress and preview entries appear here without expanding the subtask.",
  unspecified: "Not specified",
  modes: { free_design: "Free design", templates: "Use templates" },
  labels: {},
  notices: {
    stop_unconfirmed: {
      title: "Stop is not yet verified",
      detail:
        "Stop was requested, but external Agent exit is unverified. Check task status before sending Continue or Retry again. Page confirmation does not clear this state.",
    },
    model_deadline: {
      title: "This turn failed on a model response timeout",
      detail:
        "The Agent reported the model request's total deadline and ended this turn as failed. This is not a human-confirmation wait. Verified confirmation state and recent progress are retained below.",
    },
    tool_arguments: {
      title: "This turn failed on invalid model tool arguments",
      detail:
        "The Agent reported invalid tool arguments. Tools in that invalid response were not dispatched; earlier tools may have executed. Preserve the existing project and confirmations.",
    },
    failed: {
      title: "This Agent turn failed",
      detail:
        "The task reported failure, not a continuing wait for confirmation. Existing confirmation state and recent progress remain below. No automatic restart or repeated confirmation is performed.",
    },
    incomplete: {
      title: "This turn's state is not fully verified",
      detail:
        "The main turn ended without a complete subtask terminal receipt. External Agent exit or task completion cannot be assumed. Verify its state first.",
    },
    turn_ended: {
      title: "This Agent turn ended",
      detail:
        "An ended turn does not prove PPT delivery. Confirmation results are retained, but submitting them does not automatically restart an ended turn.",
    },
  },
};

export function pptProgressCopy(locale: string): ProgressCopy {
  return locale.startsWith("zh") ? zh : en;
}

/** Keep empty/missing summary values unspecified without hiding literal "0". */
export function pptSummaryValue(
  copy: Pick<ProgressCopy, "modes" | "unspecified">,
  key: string,
  value: string,
): string {
  const text = key === "template_mode" ? copy.modes[value] : value;
  return text === "" ? copy.unspecified : (text ?? copy.unspecified);
}

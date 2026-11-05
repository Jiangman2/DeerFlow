import type { DataQAStage } from "../threads/data-qa-progress";

const zh: Record<DataQAStage, string> = {
  waiting: "正在调用智能问数，等待查询进展……",
  processing: "智能问数正在处理您的查询",
  answering: "正在整理问数回答",
  completed: "智能问数调用完成",
  empty: "未查询到可用结果",
  clarification: "需要补充查询条件",
  failed: "智能问数调用失败",
  timeout: "智能问数等待超时",
  cancelled: "已取消智能问数调用",
  stopped: "智能问数已停止，未收到完成状态",
};
const en: Record<DataQAStage, string> = {
  waiting: "Calling Data QA, waiting for progress…",
  processing: "Data QA is processing your query",
  answering: "Preparing the Data QA answer",
  completed: "Data QA call completed",
  empty: "No usable results found",
  clarification: "More query information is needed",
  failed: "Data QA call failed",
  timeout: "Data QA timed out",
  cancelled: "Data QA call cancelled",
  stopped: "Data QA stopped without a completion status",
};
export function dataQAProgressCopy(locale: string) {
  return {
    stages: locale === "en-US" ? en : zh,
    details: locale === "en-US" ? "Query progress" : "查看问数进展",
  };
}

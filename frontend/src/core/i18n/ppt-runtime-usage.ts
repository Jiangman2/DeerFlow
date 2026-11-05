/** Small release-scoped copy; no change to the live translation schema. */
export function pptRuntimeUsageCopy(locale: string) {
  return locale.startsWith("zh")
    ? {
        note: "最近采集的会话累计 Token，含历史轮次；非本轮结算，不将主 Agent 与 Worker 相加。",
        parent: "主 Agent",
        worker: "Worker",
        unknown: "尚未采集到有效用量",
        partial: "部分用量尚未采集完整",
        coverage: (seen: number, total: number | null) =>
          `Worker 用量覆盖：${seen}/${total ?? "未知"}`,
      }
    : {
        note: "Last observed session-cumulative tokens, including prior turns; not this run's settlement. Root and Worker counts are not added.",
        parent: "Root Agent",
        worker: "Worker",
        unknown: "No valid usage receipt observed yet",
        partial: "Some usage receipts are still unavailable",
        coverage: (seen: number, total: number | null) =>
          `Worker usage coverage: ${seen}/${total ?? "unknown"}`,
      };
}

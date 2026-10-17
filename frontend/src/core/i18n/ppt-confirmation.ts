const messages = {
  "zh-CN": {
    title: "PPT 确认与继续状态",
    entry: "打开稳定的确认入口",
    loading: "正在读取已保存的确认状态……",
    unavailableState:
      "暂时无法读取确认状态。不会据此自动确认或启动新任务，请稍后刷新。",
    legacy:
      "此任务未启用持久确认协议。请回到原对话查看旧任务的确认入口；不会替换旧运行时。",
    waiting: "官方确认内容已就绪",
    preparing: "尚未取得官方确认内容，任务状态仍在检查中。",
    confirmed: "已取得官方确认回执，正在核实原任务的继续状态。",
    continuing: "原任务已继续执行。",
    uncertain: "确认或继续操作尚未核实，不会重复提交或另起任务。",
    inactive: "本轮运行已结束或已停止。记录仍保留，此入口不会自动启动旧任务。",
    automaticWait:
      "本次任务已开启 60 秒自动确认；无需打开预览页面。人工接管将关闭本任务后续自动确认。",
    manualWait:
      "当前需要人工确认。打开页面本身不会提交确认，仍需核对内容并点击官方页面的确认按钮。",
    takeover: "人工接管并打开确认页面",
    opening: "正在核实或恢复页面……",
    openFailed:
      "页面暂时无法恢复。原记录仍保留，未提交确认、未启动新 Agent；请回到对话核实任务状态。",
    refresh: "刷新状态",
    back: "返回原对话",
    enable: "本次 PPT 任务：60 秒后自动确认",
    manual:
      "默认人工确认。开启后，每个有效确认阶段等待 60 秒，未操作则采用官方页面当前默认选项并继续，无需打开预览。仅适用于本次新 PPT 任务。",
    automatic:
      "已为本次新 PPT 任务开启。到达有效确认阶段后等待 60 秒；人工接管、内容变更或状态无法核实会暂停自动确认。",
    quoted: "带引用上下文时暂不支持自动确认；移除引用或关闭此选项后再发送。",
    unavailable:
      "无法按当前自动确认选项提交。请确认这是新 PPT 对话、移除引用并刷新功能状态，或关闭自动确认后重试。任务尚未提交。",
  },
  "en-US": {
    title: "PPT confirmation and continuation",
    entry: "Open stable confirmation entry",
    loading: "Reading saved confirmation state…",
    unavailableState:
      "Confirmation state is unavailable. This will not confirm anything or start a new task. Refresh later.",
    legacy:
      "This task predates durable confirmation. Return to its chat for the legacy confirmation entry. Its runtime will not be replaced.",
    waiting: "Official confirmation content is ready",
    preparing:
      "Official confirmation content is not verified yet. Task state is still being checked.",
    confirmed:
      "Official confirmation received; verifying continuation of the original task.",
    continuing: "The original task has continued.",
    uncertain:
      "Confirmation or continuation is unverified. No duplicate submission or new task will be started.",
    inactive:
      "This run has ended or stopped. Records remain available; this entry will not restart the old task.",
    automaticWait:
      "This task has 60-second automatic confirmation enabled. No preview visit is required. Manual takeover disables automation for the remainder of this task.",
    manualWait:
      "Manual confirmation is required. Opening the page does not confirm it. Review the content and use the official Confirm button.",
    takeover: "Take over and open confirmation page",
    opening: "Checking or recovering the page…",
    openFailed:
      "The page could not be recovered. Records remain intact; no confirmation was submitted and no Agent was started. Return to the chat to check its status.",
    refresh: "Refresh status",
    back: "Return to the original chat",
    enable: "This PPT task: auto-confirm after 60 seconds",
    manual:
      "Manual confirmation is the default. When enabled, each valid confirmation stage waits 60 seconds, then uses the official page's current defaults and continues without opening the preview. This new PPT task only.",
    automatic:
      "Enabled for this new PPT task. Each valid confirmation stage waits 60 seconds. Manual takeover, content changes or unverified state suspend automatic confirmation.",
    quoted:
      "Automatic confirmation is unavailable with quoted context. Remove the quotes or turn this option off before sending.",
    unavailable:
      "Cannot submit with the selected automatic confirmation mode. Check that this is a new PPT chat, remove quotes and refresh feature availability, or turn this option off and retry. No task was submitted.",
  },
};

export function pptConfirmationMessages(locale: string) {
  return messages[locale === "en-US" ? "en-US" : "zh-CN"];
}

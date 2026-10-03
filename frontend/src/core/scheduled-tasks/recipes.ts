import type { ScheduleValue } from "@/components/workspace/scheduled-task-schedule-input";

export type RecipeTitleKey =
  | "trending"
  | "news"
  | "energyIntel"
  | "weekly";

export type Recipe = {
  id: string;
  icon: string;
  titleKey: RecipeTitleKey;
  prompt: string;
  schedule: ScheduleValue;
};

// Front-end-only starter recipes. The schedule's timezone is left empty so the
// ScheduleInput falls back to the browser-detected timezone when applied.
export const RECIPES: Recipe[] = [
  {
    id: "trending",
    icon: "🔥",
    titleKey: "trending",
    prompt:
      "请使用 web_search 收集今天 GitHub Trending 中排名靠前的开源项目，筛选出最值得关注的 10 个项目。每个项目请包含：项目名称、主要编程语言、今日新增 Star 数、项目一句话介绍，以及它为什么值得关注。请用 Markdown 列表输出。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "0 9 * * *" },
      timezone: "",
    },
  },
  {
    id: "news",
    icon: "📰",
    titleKey: "news",
    prompt:
      "请使用 web_search 收集今天 AI、开发工具、基础设施和网络安全方向的重要科技新闻，筛选出最重要的 5 条。每条新闻请包含：标题、来源、核心结论、对企业智能化建设的启发。请用 Markdown 列表输出。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "0 9 * * *" },
      timezone: "",
    },
  },
  {
    id: "energy-intel",
    icon: "⚡",
    titleKey: "energyIntel",
    prompt:
      "请使用 web_search 收集过去 24 小时内与能源科技、油气勘探开发、海上油气工程、AI+油气、数据湖、智能化生产和自动化办公相关的重要信息，筛选出最值得关注的 5 条情报。每条情报请包含：标题、来源、所属方向、关键信息、可能影响、建议关注部门或角色。最后给出 3 条一句话趋势判断。请用 Markdown 表格和简短要点输出，适合早会快速浏览。这是普通定时摘要任务，只使用可用搜索工具生成 Markdown 摘要；不要启动本地 HTML 日报技能流水线。如果搜索工具当前不可用，请直接说明不可用原因，不要改走本地技能流水线或手工伪造来源。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "0 9 * * *" },
      timezone: "",
    },
  },
  {
    id: "weekly",
    icon: "📅",
    titleKey: "weekly",
    prompt:
      "请生成一份周报：总结本周已完成的工作、当前阻塞事项，以及下周最重要的 3 个优先事项。要求简洁、清晰、便于快速浏览。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "0 9 * * 1" },
      timezone: "",
    },
  },
];

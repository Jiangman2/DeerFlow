export const dashboardSections = [
  "overview",
  "runs",
  "users",
  "knowledge",
  "models",
  "oa",
] as const;

export type DashboardSection = (typeof dashboardSections)[number];

export function isDashboardSection(value: string): value is DashboardSection {
  return dashboardSections.some((section) => section === value);
}

export function dashboardNavigationCopy(locale: string) {
  return locale.startsWith("zh")
    ? {
        navigation: "看板模块",
        customRange: "自定义区间",
        collapseRange: "收起区间",
        labels: {
          overview: "总览",
          runs: "运行诊断",
          users: "用户与员工",
          knowledge: "知识库",
          models: "模型监控",
          oa: "OA 接口",
        },
        descriptions: {
          overview: "平台用量与用户活跃趋势",
          runs: "失败诊断、慢运行、高 Token 运行与定时任务",
          users: "用户运行排行、数字员工点击、保留会话与使用反馈",
          knowledge: "知识库、文档与共享空间的使用情况",
          models: "模型调用、耗时与并发采样；使用本模块独立的时间范围",
          oa: "OA 文生图任务与接口调用；使用本模块独立的时间范围",
        },
      }
    : {
        navigation: "Dashboard sections",
        customRange: "Custom range",
        collapseRange: "Hide range",
        labels: {
          overview: "Overview",
          runs: "Run diagnostics",
          users: "Users & employees",
          knowledge: "Knowledge",
          models: "Models",
          oa: "OA API",
        },
        descriptions: {
          overview: "Platform usage and user activity",
          runs: "Failures, slow runs, high-token runs, and scheduled tasks",
          users: "User rankings, employee clicks, retained chats, and feedback",
          knowledge: "Knowledge bases, documents, and shared spaces",
          models:
            "Model calls, latency, and sampled concurrency; independent time range",
          oa: "OA diagram jobs and API calls; independent time range",
        },
      };
}

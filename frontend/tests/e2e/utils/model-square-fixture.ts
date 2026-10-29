import type { Page } from "@playwright/test";

import { mockLangGraphAPI } from "./mock-api";

// Synthetic API data; every screenshot still renders the real application.
export async function modelSquareFixture(page: Page) {
  const names = [
    "制度研究助手",
    "项目财务助手",
    "油藏分析助手",
    "合同审核助手",
    "科研资料助手",
    "采购管理助手",
    "安全生产助手",
    "工程设计助手",
    "成本分析助手",
    "设备运维助手",
    "年度报告助手",
    "人才发展助手",
  ];
  const agents = names.map((display_name, i) => ({
    name: `qa-employee-${i}`,
    display_name,
    description: "整理业务资料，梳理关键要点，协助完成日常工作。",
    source: "team" as const,
    tags: ["业务分析", "资料整理"],
    avatar_id: "knowledge-service",
    model: null,
    tool_groups: null,
    skills: null,
  }));
  const token = "a".repeat(64);
  const launches: { url: string; body: unknown }[] = [];
  mockLangGraphAPI(page, {
    agents: agents.map((a) => ({ ...a, source: "personal" })),
    teamAgents: agents,
  });
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      json: {
        id: "default",
        display_name: "本地预览用户",
        email: "preview@example.test",
        organization_name: "示例业务部门",
        system_role: "admin",
        needs_setup: false,
        oauth_provider: "企业单点登录",
      },
    }),
  );
  await page.route("**/api/console/token-summary", (route) =>
    route.fulfill({
      json: {
        scope: "current_user_recorded_platform_runs",
        total_tokens: 1286450,
        input_tokens: 960000,
        output_tokens: 326450,
        unclassified_tokens: 0,
        run_count: 96,
        recorded_since: "2026-09-01T00:00:00Z",
      },
    }),
  );
  await page.route("**/api/employee-directory", (route) =>
    route.fulfill({
      json: {
        employees: agents.map((a) => ({
          source: "team",
          name: a.name,
          is_hot: true,
          version: 1,
          identity_token: token,
          creator_name: "示例管理员",
        })),
      },
    }),
  );
  await page.route("**/api/employee-directory/*/*/click", async (route) => {
    launches.push({
      url: route.request().url(),
      body: route.request().postDataJSON(),
    });
    await route.fulfill({ status: 204 });
  });
  await page.route("**/api/models", (route) =>
    route.fulfill({
      json: {
        models: [
          {
            name: "deepseek-v4-flash",
            display_name: "DeepSeek-V4-Flash",
            supports_thinking: true,
          },
          {
            name: "deepseek-v4.1-flash-1017",
            display_name: "DeepSeek-V4.1-Flash-1017",
            supports_thinking: true,
            supports_vision: true,
            supports_reasoning_effort: true,
          },
        ],
        token_usage: { enabled: false },
      },
    }),
  );
  const feedback = names.map((_, i) => ({
    feedback_id: `feedback-${i}`,
    display_name: `示例用户${String(i + 1).padStart(2, "0")}`,
    email: `user${i + 1}@example.test`,
    organization_name: i % 2 ? "示例研究部门" : "示例业务部门",
    rating: i % 3 ? 1 : -1,
    created_at: "2026-10-28T08:30:00Z",
    thread_id: "11111111-1111-4111-8111-111111111111",
    run_id: `run-${i}`,
    thread_title: `业务资料整理与核对 ${i + 1}`,
  }));
  const clicks = names.map((name, i) => ({
    identity: `employee-${i}`,
    name,
    source: "team",
    clicks: i === 11 ? 0 : 168 - i * 11,
    users: i === 11 ? 0 : 76 - i * 5,
    total_clicks: i === 11 ? 0 : 685 - i * 30,
  }));
  await page.route("**/api/admin/dashboard/**", async (route) => {
    const url = new URL(route.request().url());
    const kind = url.pathname.split("/").at(-1);
    const offset = Number(url.searchParams.get("offset") ?? 0),
      limit = Number(url.searchParams.get("limit") ?? 10),
      query = url.searchParams.get("query") ?? "";
    let data: unknown = { items: [], total: 0 };
    if (kind === "summary")
      data = {
        window: {
          start_at: "2026-10-21T08:30:00Z",
          end_at: "2026-10-28T08:30:00Z",
          hours: 168,
        },
        overview: {
          total_users: 128,
          new_users: 12,
          active_users: 86,
          threads: 196,
          all_time_threads: 1580,
          existing_threads: 1302,
          runs: 326,
          terminal_runs: 321,
          successful_runs: 317,
          failed_runs: 4,
          running_runs: 5,
          success_rate: 0.988,
          avg_run_duration_ms: 26000,
          total_tokens: 1286450,
          llm_calls: 982,
          artifact_runs: 32,
          knowledge_runs: 56,
          feedback_total: 12,
          satisfaction_rate: 0.667,
          estimated_minutes_saved: 0,
        },
        activity_bucket: "day",
        user_activity: [],
        user_usage: [],
        feature_usage: [],
        agent_usage: [],
        model_usage: [],
        recent_failures: [],
        recent_business_events: [],
        feedback: {
          total: 12,
          positive: 8,
          negative: 4,
          satisfaction_rate: 0.667,
          negative_rate: 0.333,
          recent_comments: [],
          recent_negative: [],
          by_feature: [],
        },
        scheduled_task_usage: { users: [] },
        aggregation: {
          source: "raw",
          schema_version: 1,
          rollup_days: 0,
          rollup_hours: 0,
          raw_segments: 1,
        },
      };
    if (kind === "run-rankings") data = { slow_runs: [], high_token_runs: [] };
    if (kind === "knowledge") data = { available: false, error: null };
    if (kind === "employee-clicks") {
      const rows = clicks.filter((row) => row.name.includes(query));
      data = {
        items: rows.slice(offset, offset + limit),
        total: rows.length,
        offset,
        limit,
      };
    }
    if (kind === "feedback-actors") {
      const rating = url.searchParams.get("rating");
      const rows = feedback.filter(
        (row) =>
          (!rating || row.rating === Number(rating)) &&
          [row.display_name, row.email, row.organization_name].some((v) =>
            v.includes(query),
          ),
      );
      data = {
        items: rows.slice(offset, offset + limit),
        total: rows.length,
        offset,
        limit,
      };
    }
    await route.fulfill({ json: data });
  });
  return { launches, agents, token };
}

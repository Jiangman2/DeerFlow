import { defineConfig, devices } from "@playwright/test";

const basePort = Number(process.env.STANDARDS_E2E_BASE_PORT ?? "3161");
if (!Number.isInteger(basePort) || basePort < 1 || basePort > 65533) {
  throw new Error("STANDARDS_E2E_BASE_PORT must be an integer from 1 to 65533");
}

const complete = {
  qa: "标准知识问答测试",
  search: "标准信息检索测试",
  policy: "Policy-Expert",
  comparison: "标准对比分析测试",
  applicability: "标准适用性分析测试",
  compliance: "标准合规自检测试",
  authoring: "标准编审辅助测试",
};
const partial = { qa: complete.qa, authoring: complete.authoring };
const cases = [
  { name: "unconfigured", agents: {}, raw: "" },
  { name: "configured", agents: complete, raw: JSON.stringify(complete) },
  {
    name: "partial",
    agents: partial,
    raw: JSON.stringify({
      ...partial,
      search: "https://invalid.example/employee",
      unknown: "ignored",
    }),
  },
].map((item, index) => ({
  ...item,
  port: basePort + index,
  baseURL: `http://127.0.0.1:${basePort + index}`,
}));

// Build once before this suite. Each server then reads a different runtime
// mapping from the same build; no Gateway or real employee is contacted.
export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "standards-portal.spec.ts",
  outputDir: "test-results/standards-portal",
  forbidOnly: !!process.env.CI,
  workers: 2,
  reporter: process.env.CI ? [["line"], ["github"]] : "list",
  use: {
    ...devices["Desktop Chrome"],
    ...(process.env.PLAYWRIGHT_CHANNEL
      ? { channel: process.env.PLAYWRIGHT_CHANNEL }
      : {}),
    trace: "retain-on-failure",
  },
  projects: cases.map((item) => ({
    name: item.name,
    metadata: { standardsPortalAgents: item.agents },
    use: { baseURL: item.baseURL },
  })),
  webServer: cases.map((item) => ({
    command: `node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port ${item.port}`,
    url: `${item.baseURL}/workspace/standards`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      SKIP_ENV_VALIDATION: "1",
      DEER_FLOW_AUTH_DISABLED: "1",
      DEER_FLOW_ENV: "test",
      ENVIRONMENT: "test",
      DEER_FLOW_SSO_ONLY: "false",
      NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "",
      DEER_FLOW_INTERNAL_GATEWAY_BASE_URL: "http://127.0.0.1:9",
      NEXT_TELEMETRY_DISABLED: "1",
      STANDARDS_PORTAL_AGENTS_JSON: item.raw,
    },
  })),
});

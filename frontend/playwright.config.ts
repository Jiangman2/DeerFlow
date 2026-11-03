import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
const skipWebServer = process.env.PLAYWRIGHT_SKIP_WEB_SERVER === "1";
const parsedBaseURL = new URL(baseURL);
const webServerPort =
  parsedBaseURL.port || (parsedBaseURL.protocol === "https:" ? "443" : "80");

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "html",
  timeout: 30_000,

  use: {
    baseURL,
    locale: "en-US",
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: skipWebServer
    ? undefined
    : {
        command: `pnpm exec next build && pnpm exec next start --port ${webServerPort}`,
        url: baseURL,
        // Reusing an arbitrary process on the same port can silently test the
        // wrong worktree or auth configuration. Make reuse an explicit opt-in.
        reuseExistingServer:
          process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "1",
        timeout: 300_000,
        env: {
          SKIP_ENV_VALIDATION: "1",
          DEER_FLOW_AUTH_DISABLED: process.env.DEER_FLOW_AUTH_DISABLED ?? "1",
          ...(process.env.DEER_FLOW_SSO_ONLY
            ? { DEER_FLOW_SSO_ONLY: process.env.DEER_FLOW_SSO_ONLY }
            : {}),
          ...(process.env.DEER_FLOW_UNAUTHENTICATED_HOME_URL
            ? {
                DEER_FLOW_UNAUTHENTICATED_HOME_URL:
                  process.env.DEER_FLOW_UNAUTHENTICATED_HOME_URL,
              }
            : {}),
        },
      },
});

import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

test("Next loads identification suppression without losing API fallback routing", () => {
  const script = `
    import loadConfig from 'next/dist/server/config.js';
    import constants from 'next/constants.js';
    const config = await loadConfig.default(constants.PHASE_PRODUCTION_SERVER, process.cwd());
    const routes = await config.rewrites();
    console.log(JSON.stringify({ poweredByHeader: config.poweredByHeader, routes }));
  `;
  const result = execFileSync(
    process.execPath,
    ["--input-type=module", "-e", script],
    {
      cwd: resolve(import.meta.dirname, "../../.."),
      env: {
        ...process.env,
        SKIP_ENV_VALIDATION: "1",
        NEXT_PUBLIC_BACKEND_BASE_URL: "",
        NEXT_PUBLIC_LANGGRAPH_BASE_URL: "",
      },
      encoding: "utf-8",
      timeout: 30000,
    },
  );
  const loaded = JSON.parse(result.trim().split("\n").at(-1) ?? "{}");
  expect(loaded.poweredByHeader).toBe(false);
  expect(loaded.routes.beforeFiles).toEqual([]);
  expect(loaded.routes.afterFiles).toEqual([]);
  expect(loaded.routes.fallback).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ source: "/api/:path*" }),
      expect.objectContaining({ source: "/api/langgraph/:path*" }),
    ]),
  );
});

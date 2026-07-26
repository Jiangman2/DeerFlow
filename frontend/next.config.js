/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import "./src/env.js";

function getInternalServiceURL(envKey, fallbackURL) {
  const configured = process.env[envKey]?.trim();
  return configured && configured.length > 0
    ? configured.replace(/\/+$/, "")
    : fallbackURL;
}

import nextra from "nextra";

const withNextra = nextra({});

/** @type {import("next").NextConfig} */
const config = {
  output:
    process.env.NEXT_CONFIG_BUILD_OUTPUT === "standalone"
      ? "standalone"
      : undefined,
  i18n: {
    locales: ["en", "zh"],
    defaultLocale: "en",
  },
  devIndicators: false,
  async rewrites() {
    const gatewayFallbackRewrites = [];
    const gatewayURL = getInternalServiceURL(
      "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL",
      "http://127.0.0.1:8001",
    );

    if (!process.env.NEXT_PUBLIC_LANGGRAPH_BASE_URL) {
      gatewayFallbackRewrites.push({
        source: "/api/langgraph",
        destination: `${gatewayURL}/api`,
      });
      gatewayFallbackRewrites.push({
        source: "/api/langgraph/:path*",
        destination: `${gatewayURL}/api/:path*`,
      });
    }

    if (!process.env.NEXT_PUBLIC_BACKEND_BASE_URL) {
      gatewayFallbackRewrites.push({
        source: "/api/agents",
        destination: `${gatewayURL}/api/agents`,
      });
      gatewayFallbackRewrites.push({
        source: "/api/agents/:path*",
        destination: `${gatewayURL}/api/agents/:path*`,
      });
      gatewayFallbackRewrites.push({
        source: "/api/skills",
        destination: `${gatewayURL}/api/skills`,
      });
      gatewayFallbackRewrites.push({
        source: "/api/skills/:path*",
        destination: `${gatewayURL}/api/skills/:path*`,
      });

      // Catch-all for remaining gateway API routes (models, threads, memory,
      // mcp, artifacts, uploads, suggestions, runs, etc.) that don't have
      // their own NEXT_PUBLIC_* env var toggle.
      //
      // NOTE: this must come AFTER the /api/langgraph rewrite above so that
      // LangGraph-compatible routes keep their public prefix while Gateway
      // receives its native /api/* paths.
      gatewayFallbackRewrites.push({
        source: "/api/:path*",
        destination: `${gatewayURL}/api/:path*`,
      });
    }

    // Gateway proxying is deliberately a fallback. Next.js must resolve
    // repository-owned App Router handlers first, including the CNOOC
    // external-agent launch route that reads the HttpOnly platform-token
    // cookie. An array return is treated as an earlier rewrite phase in the
    // production server and can make the catch-all shadow those handlers.
    return {
      beforeFiles: [],
      afterFiles: [],
      fallback: gatewayFallbackRewrites,
    };
  },
};

export default withNextra(config);

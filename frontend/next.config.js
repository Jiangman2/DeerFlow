/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import "./src/env.js";

/** @type {import("next").NextConfig} */
const config = {
  devIndicators: false,
  allowedDevOrigins: [
    "http://127.0.0.1",
    "http://localhost",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
  ],
  output: "standalone",
  webpack: (config) => {
    const watchOptions = config.watchOptions ?? {};
    const ignored = watchOptions.ignored ?? [];
    const ignoreList = Array.isArray(ignored) ? ignored : [ignored];
    const normalizedIgnoreList = ignoreList.filter(
      (v) => typeof v === "string" && v.trim().length > 0,
    );
    return {
      ...config,
      watchOptions: {
        ...watchOptions,
        ignored: [
          ...normalizedIgnoreList,
          "**/.deer-flow/**",
          "**/better-auth.sqlite",
          "**/better-auth.sqlite-*",
          "**/better-auth.sqlite-wal",
          "**/better-auth.sqlite-shm",
        ],
      },
    };
  },
  async redirects() {
    if (process.env.NODE_ENV === "production") return [];
    return [
      {
        source: "/:path*",
        has: [{ type: "host", value: "127.0.0.1" }],
        destination: "http://localhost:3000/:path*",
        permanent: false,
      },
      {
        source: "/:path*",
        has: [{ type: "host", value: "127.0.0.1:3000" }],
        destination: "http://localhost:3000/:path*",
        permanent: false,
      },
      {
        source: "/:path*",
        has: [{ type: "host", value: "[::1]" }],
        destination: "http://localhost:3000/:path*",
        permanent: false,
      },
      {
        source: "/:path*",
        has: [{ type: "host", value: "[::1]:3000" }],
        destination: "http://localhost:3000/:path*",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    const gatewayBaseUrl =
      process.env.DEERFLOW_GATEWAY_BASE_URL ?? "http://127.0.0.1:8001";
    return [
      {
        source: "/@vite/client",
        destination: "/api/empty",
      },
      {
        source: "/api/models",
        destination: `${gatewayBaseUrl}/api/models`,
      },
      {
        source: "/api/mcp/:path*",
        destination: `${gatewayBaseUrl}/api/mcp/:path*`,
      },
      {
        source: "/api/convert/:path*",
        destination: `${gatewayBaseUrl}/api/convert/:path*`,
      },
      {
        source: "/api/channels/:path*",
        destination: `${gatewayBaseUrl}/api/channels/:path*`,
      },
    ];
  },
};

export default config;

import { createEnv } from "@t3-oss/env-nextjs";
import { z } from "zod";

export const env = createEnv({
  /**
   * Specify your server-side environment variables schema here. This way you can ensure the app
   * isn't built with invalid env vars.
   */
  server: {
    BETTER_AUTH_SECRET:
      process.env.NODE_ENV === "production"
        ? z.string()
        : z.string().optional(),
    BETTER_AUTH_URL: z.string().optional(),
    BETTER_AUTH_BASE_URL: z.string().optional(),
    BETTER_AUTH_DB_DIR: z.string().optional(),
    BETTER_AUTH_GITHUB_CLIENT_ID: z.string().optional(),
    BETTER_AUTH_GITHUB_CLIENT_SECRET: z.string().optional(),
    GITHUB_OAUTH_TOKEN: z.string().optional(),
    NODE_ENV: z
      .enum(["development", "test", "production"])
      .default("development"),
    DEERFLOW_GATEWAY_BASE_URL: z.string().optional(),
    DEERFLOW_PROXY_SECRET: z.string().optional(),
    SHOP_CART_DOWNLOAD_TOKEN: z.string().optional(),
    SHOP_CART_DOWNLOAD_TLS_VERIFY: z.string().optional(),
  },

  /**
   * Specify your client-side environment variables schema here. This way you can ensure the app
   * isn't built with invalid env vars. To expose them to the client, prefix them with
   * `NEXT_PUBLIC_`.
   */
  client: {
    NEXT_PUBLIC_BACKEND_BASE_URL: z.string().optional(),
    NEXT_PUBLIC_LANGGRAPH_BASE_URL: z.string().optional(),
    NEXT_PUBLIC_STATIC_WEBSITE_ONLY: z.string().optional(),
    NEXT_PUBLIC_ALLOW_PUBLIC_MODELS: z.string().optional(),
    NEXT_PUBLIC_SHOP_CART_IFRAME_URL: z.string().optional(),
    NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN: z.string().optional(),
  },

  /**
   * You can't destruct `process.env` as a regular object in the Next.js edge runtimes (e.g.
   * middlewares) or client-side so we need to destruct manually.
   */
  runtimeEnv: {
    BETTER_AUTH_SECRET: process.env.BETTER_AUTH_SECRET,
    BETTER_AUTH_URL: process.env.BETTER_AUTH_URL,
    BETTER_AUTH_BASE_URL: process.env.BETTER_AUTH_BASE_URL,
    BETTER_AUTH_DB_DIR: process.env.BETTER_AUTH_DB_DIR,
    BETTER_AUTH_GITHUB_CLIENT_ID: process.env.BETTER_AUTH_GITHUB_CLIENT_ID,
    BETTER_AUTH_GITHUB_CLIENT_SECRET:
      process.env.BETTER_AUTH_GITHUB_CLIENT_SECRET,
    NODE_ENV: process.env.NODE_ENV,
    DEERFLOW_GATEWAY_BASE_URL: process.env.DEERFLOW_GATEWAY_BASE_URL,
    DEERFLOW_PROXY_SECRET: process.env.DEERFLOW_PROXY_SECRET,
    SHOP_CART_DOWNLOAD_TOKEN: process.env.SHOP_CART_DOWNLOAD_TOKEN,
    SHOP_CART_DOWNLOAD_TLS_VERIFY:
      process.env.SHOP_CART_DOWNLOAD_TLS_VERIFY,

    NEXT_PUBLIC_BACKEND_BASE_URL: process.env.NEXT_PUBLIC_BACKEND_BASE_URL,
    NEXT_PUBLIC_LANGGRAPH_BASE_URL: process.env.NEXT_PUBLIC_LANGGRAPH_BASE_URL,
    NEXT_PUBLIC_STATIC_WEBSITE_ONLY:
      process.env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY,
    NEXT_PUBLIC_ALLOW_PUBLIC_MODELS:
      process.env.NEXT_PUBLIC_ALLOW_PUBLIC_MODELS,
    NEXT_PUBLIC_SHOP_CART_IFRAME_URL:
      process.env.NEXT_PUBLIC_SHOP_CART_IFRAME_URL,
    NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN:
      process.env.NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN,
    GITHUB_OAUTH_TOKEN: process.env.GITHUB_OAUTH_TOKEN,
  },
  /**
   * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially
   * useful for Docker builds.
   */
  skipValidation: !!process.env.SKIP_ENV_VALIDATION,
  /**
   * Makes it so that empty strings are treated as undefined. `SOME_VAR: z.string()` and
   * `SOME_VAR=''` will throw an error.
   */
  emptyStringAsUndefined: true,
});

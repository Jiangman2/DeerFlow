import { createEnv } from "@t3-oss/env-nextjs";
import { z } from "zod";

export const env = createEnv({
  /**
   * Specify your server-side environment variables schema here. This way you can ensure the app
   * isn't built with invalid env vars.
   */
  server: {
    GITHUB_OAUTH_TOKEN: z.string().optional(),
    SHOP_CART_API_BASE_URL: z.string().optional(),
    SHOP_CART_DOWNLOAD_TOKEN: z.string().optional(),
    SHOP_CART_DOWNLOAD_TLS_VERIFY: z.string().optional(),
    WEKNORA_FRONTEND_URL: z.string().optional(),
    NODE_ENV: z
      .enum(["development", "test", "production"])
      .default("development"),
  },

  /**
   * Specify your client-side environment variables schema here. This way you can ensure the app
   * isn't built with invalid env vars. To expose them to the client, prefix them with
   * `NEXT_PUBLIC_`.
   */
  client: {
    NEXT_PUBLIC_BACKEND_BASE_URL: z.string().optional(),
    NEXT_PUBLIC_LANGGRAPH_BASE_URL: z.string().optional(),
    NEXT_PUBLIC_SHOP_CART_MODE: z.string().optional(),
    NEXT_PUBLIC_SHOP_CART_IFRAME_URL: z.string().optional(),
    NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN: z.string().optional(),
    NEXT_PUBLIC_WEKNORA_FRONTEND_URL: z.string().optional(),
    NEXT_PUBLIC_STATIC_WEBSITE_ONLY: z.string().optional(),
  },

  /**
   * You can't destruct `process.env` as a regular object in the Next.js edge runtimes (e.g.
   * middlewares) or client-side so we need to destruct manually.
   */
  runtimeEnv: {
    NODE_ENV: process.env.NODE_ENV,

    NEXT_PUBLIC_BACKEND_BASE_URL: process.env.NEXT_PUBLIC_BACKEND_BASE_URL,
    NEXT_PUBLIC_LANGGRAPH_BASE_URL: process.env.NEXT_PUBLIC_LANGGRAPH_BASE_URL,
    NEXT_PUBLIC_SHOP_CART_MODE: process.env.NEXT_PUBLIC_SHOP_CART_MODE,
    NEXT_PUBLIC_SHOP_CART_IFRAME_URL:
      process.env.NEXT_PUBLIC_SHOP_CART_IFRAME_URL,
    NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN:
      process.env.NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN,
    NEXT_PUBLIC_WEKNORA_FRONTEND_URL:
      process.env.NEXT_PUBLIC_WEKNORA_FRONTEND_URL,
    NEXT_PUBLIC_STATIC_WEBSITE_ONLY:
      process.env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY,
    GITHUB_OAUTH_TOKEN: process.env.GITHUB_OAUTH_TOKEN,
    SHOP_CART_API_BASE_URL: process.env.SHOP_CART_API_BASE_URL,
    SHOP_CART_DOWNLOAD_TOKEN: process.env.SHOP_CART_DOWNLOAD_TOKEN,
    SHOP_CART_DOWNLOAD_TLS_VERIFY: process.env.SHOP_CART_DOWNLOAD_TLS_VERIFY,
    WEKNORA_FRONTEND_URL: process.env.WEKNORA_FRONTEND_URL,
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

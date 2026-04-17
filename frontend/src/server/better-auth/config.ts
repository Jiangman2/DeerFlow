import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { betterAuth } from "better-auth";
import { getMigrations } from "better-auth/db";

function resolveAuthDbPath() {
  const root =
    process.env.BETTER_AUTH_DB_DIR ??
    process.env.DEER_FLOW_HOME ??
    path.join(process.cwd(), ".deer-flow");
  fs.mkdirSync(root, { recursive: true });
  return path.join(root, "better_auth.sqlite");
}

function extraTrustedOrigins(): string[] {
  const raw = process.env.BETTER_AUTH_TRUSTED_ORIGINS ?? "";
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function defaultTrustedOrigins(): string[] {
  return [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:2026",
    "http://127.0.0.1:2026",
    ...(process.env.BETTER_AUTH_URL ? [process.env.BETTER_AUTH_URL] : []),
    ...(process.env.BETTER_AUTH_BASE_URL ? [process.env.BETTER_AUTH_BASE_URL] : []),
    ...extraTrustedOrigins(),
  ];
}

export const auth = betterAuth({
  secret: process.env.BETTER_AUTH_SECRET,
  baseURL: process.env.BETTER_AUTH_URL ?? process.env.BETTER_AUTH_BASE_URL,
  trustedOrigins: async (request) => {
    if (!request) return defaultTrustedOrigins();
    const derived = new URL(request.url).origin;
    return Array.from(new Set([...defaultTrustedOrigins(), derived]));
  },
  database: new DatabaseSync(resolveAuthDbPath()),
  emailAndPassword: { enabled: true },
});

let migrationsPromise: Promise<void> | null = null;
export function ensureAuthMigrations() {
  migrationsPromise ??= (async () => {
    const { runMigrations } = await getMigrations(auth.options);
    await runMigrations();
  })();
  return migrationsPromise;
}

export type Session = typeof auth.$Infer.Session;

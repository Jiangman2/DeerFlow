import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

function resolveAuthDbPath() {
  return path.join(resolveDataRoot(), "better_auth.sqlite");
}

function resolveDataRoot() {
  const root =
    process.env.BETTER_AUTH_DB_DIR ??
    process.env.DEER_FLOW_HOME ??
    path.join(process.cwd(), ".deer-flow");
  fs.mkdirSync(root, { recursive: true });
  return root;
}

function resolveDeerflowDbPath() {
  return path.join(resolveDataRoot(), "deerflow.db");
}

function safeUserId(userId: string) {
  const raw = userId.trim();
  if (!raw) return "user";
  const safe = raw.replace(/[^A-Za-z0-9._-]+/g, "_");
  if (!safe) return "user";
  const safeRe = /^[A-Za-z0-9._-]+$/;
  if (safe !== raw || safe.length > 64 || !safeRe.test(safe)) {
    const digest = crypto.createHash("sha1").update(raw, "utf8").digest("hex").slice(0, 10);
    const prefix = safe.slice(0, 48).replace(/_+$/g, "");
    return prefix ? `${prefix}_${digest}` : digest;
  }
  return safe;
}

export type UserRow = {
  id: string;
  name: string;
  email: string;
  emailVerified: number;
  image: string | null;
  createdAt: string;
  updatedAt: string;
};

export type UserAdminFlags = {
  isAdmin: boolean;
  isDisabled: boolean;
};

export type ManagedUser = {
  id: string;
  name: string;
  email: string;
  emailVerified: boolean;
  createdAt: string;
  updatedAt: string;
  isAdmin: boolean;
  isDisabled: boolean;
};

export type SsoUserInput = {
  id: string;
  name: string;
  email: string;
};

export type CreatedSsoSession = {
  token: string;
  expiresAt: Date;
};

type Db = {
  exec: (sql: string) => void;
  prepare: (sql: string) => {
    get: (...args: unknown[]) => unknown;
    all: (...args: unknown[]) => unknown[];
    run: (...args: unknown[]) => { changes?: number };
  };
};

function asString(v: unknown) {
  return typeof v === "string" ? v : "";
}

function asBool(v: unknown) {
  return v === 1 || v === true || v === "1";
}

function asNumber(v: unknown) {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  if (typeof v === "boolean") return v ? 1 : 0;
  return 0;
}

function openDb() {
  const db = new DatabaseSync(resolveAuthDbPath()) as unknown as Db;
  db.exec("PRAGMA foreign_keys = ON");
  return db;
}

export function ensurePlatformTables(db: Db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS platform_user_flags (
      user_id TEXT PRIMARY KEY REFERENCES user(id) ON DELETE CASCADE,
      is_admin INTEGER NOT NULL DEFAULT 0,
      is_disabled INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
  `);
  db.exec(`
    CREATE TABLE IF NOT EXISTS platform_settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
  `);
  db.exec(`
    INSERT OR IGNORE INTO platform_settings(key, value) VALUES('allow_signup', '1')
  `);
}

function rowToManagedUser(row: Record<string, unknown>): ManagedUser {
  return {
    id: asString(row.id),
    name: asString(row.name),
    email: asString(row.email),
    emailVerified: asBool(row.emailVerified),
    createdAt: asString(row.createdAt),
    updatedAt: asString(row.updatedAt),
    isAdmin: asNumber(row.is_admin) === 1,
    isDisabled: asNumber(row.is_disabled) === 1,
  };
}

export function getAllowSignup() {
  const db = openDb();
  ensurePlatformTables(db);
  const row = db
    .prepare("SELECT value FROM platform_settings WHERE key = 'allow_signup'")
    .get() as { value?: string } | undefined;
  return row?.value !== "0";
}

export function setAllowSignup(allow: boolean) {
  const db = openDb();
  ensurePlatformTables(db);
  db.prepare(
    "INSERT INTO platform_settings(key, value, updated_at) VALUES('allow_signup', ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
  ).run(allow ? "1" : "0");
}

export function listManagedUsers(): ManagedUser[] {
  const db = openDb();
  ensurePlatformTables(db);
  const rows = db
    .prepare(
      `
      SELECT
        u.id,
        u.name,
        u.email,
        u.emailVerified,
        u.createdAt,
        u.updatedAt,
        COALESCE(f.is_admin, 0) AS is_admin,
        COALESCE(f.is_disabled, 0) AS is_disabled
      FROM user u
      LEFT JOIN platform_user_flags f ON f.user_id = u.id
      ORDER BY u.createdAt DESC
      `,
    )
    .all() as Record<string, unknown>[];
  return rows.map(rowToManagedUser);
}

export function getUserFlags(userId: string): UserAdminFlags {
  const db = openDb();
  ensurePlatformTables(db);
  const row = db
    .prepare("SELECT is_admin, is_disabled FROM platform_user_flags WHERE user_id = ?")
    .get(userId) as { is_admin?: number; is_disabled?: number } | undefined;
  return {
    isAdmin: Number(row?.is_admin ?? 0) === 1,
    isDisabled: Number(row?.is_disabled ?? 0) === 1,
  };
}

export function setUserFlags(userId: string, flags: Partial<UserAdminFlags>) {
  const db = openDb();
  ensurePlatformTables(db);
  const existing = getUserFlags(userId);
  const isAdmin = flags.isAdmin ?? existing.isAdmin;
  const isDisabled = flags.isDisabled ?? existing.isDisabled;
  db.prepare(
    `
    INSERT INTO platform_user_flags(user_id, is_admin, is_disabled, updated_at)
    VALUES(?, ?, ?, datetime('now'))
    ON CONFLICT(user_id) DO UPDATE SET
      is_admin=excluded.is_admin,
      is_disabled=excluded.is_disabled,
      updated_at=excluded.updated_at
    `,
  ).run(userId, isAdmin ? 1 : 0, isDisabled ? 1 : 0);
}

export function revokeUserSessions(userId: string) {
  const db = openDb();
  ensurePlatformTables(db);
  db.prepare("DELETE FROM session WHERE userId = ?").run(userId);
}

export function updateUserIdentity(userId: string, patch: { name?: string; email?: string }) {
  const db = openDb();
  ensurePlatformTables(db);
  const name = typeof patch.name === "string" ? patch.name.trim() : "";
  const email = typeof patch.email === "string" ? patch.email.trim().toLowerCase() : "";

  if (name && name.length > 80) throw new Error("name too long");
  if (email && email.length > 320) throw new Error("email too long");

  if (email) {
    const existing = db
      .prepare("SELECT id FROM user WHERE lower(email) = ? LIMIT 1")
      .get(email) as { id?: string } | undefined;
    if (existing?.id && existing.id !== userId) {
      throw new Error("email already exists");
    }
  }

  const parts: string[] = [];
  const values: unknown[] = [];
  if (name) {
    parts.push("name = ?");
    values.push(name);
  }
  if (email) {
    parts.push("email = ?");
    values.push(email);
  }
  if (parts.length === 0) return;

  parts.push("updatedAt = datetime('now')");
  values.push(userId);
  db.prepare(`UPDATE user SET ${parts.join(", ")} WHERE id = ?`).run(...values);
}

export function upsertSsoUser(input: SsoUserInput) {
  const id = input.id.trim();
  const name = input.name.trim();
  const email = input.email.trim().toLowerCase();

  if (!id) throw new Error("user id is required");
  if (!name) throw new Error("user name is required");
  if (!email) throw new Error("user email is required");
  if (name.length > 80) throw new Error("name too long");
  if (email.length > 320) throw new Error("email too long");

  const db = openDb();
  ensurePlatformTables(db);

  db.exec("BEGIN");
  try {
    const byId = db
      .prepare("SELECT id FROM user WHERE id = ? LIMIT 1")
      .get(id) as { id?: string } | undefined;

    if (byId?.id) {
      db.prepare(
        `
        UPDATE user
        SET name = ?, email = ?, emailVerified = 1, updatedAt = datetime('now')
        WHERE id = ?
        `,
      ).run(name, email, byId.id);
      db.exec("COMMIT");
      return byId.id;
    }

    const byEmail = db
      .prepare("SELECT id FROM user WHERE lower(email) = ? LIMIT 1")
      .get(email) as { id?: string } | undefined;

    if (byEmail?.id) {
      db.prepare(
        `
        UPDATE user
        SET name = ?, email = ?, emailVerified = 1, updatedAt = datetime('now')
        WHERE id = ?
        `,
      ).run(name, email, byEmail.id);
      db.exec("COMMIT");
      return byEmail.id;
    }

    db.prepare(
      `
      INSERT INTO user(id, name, email, emailVerified, image, createdAt, updatedAt)
      VALUES(?, ?, ?, 1, NULL, datetime('now'), datetime('now'))
      `,
    ).run(id, name, email);
    db.exec("COMMIT");
    return id;
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  }
}

export function createSsoSession(userId: string): CreatedSsoSession {
  const id = crypto.randomBytes(18).toString("base64url");
  const token = crypto.randomBytes(32).toString("base64url");
  const expiresAt = new Date(Date.now() + 60 * 60 * 24 * 7 * 1000);
  const db = openDb();
  ensurePlatformTables(db);
  db.prepare(
    `
    INSERT INTO session(id, expiresAt, token, createdAt, updatedAt, ipAddress, userAgent, userId)
    VALUES(?, ?, ?, datetime('now'), datetime('now'), NULL, NULL, ?)
    `,
  ).run(id, expiresAt.toISOString(), token, userId);
  return { token, expiresAt };
}

function hashPassword(plain: string) {
  const saltHex = crypto.randomBytes(16).toString("hex");
  const normalized = plain.normalize("NFKC");
  const derived = crypto.scryptSync(normalized, saltHex, 64, {
    N: 16384,
    r: 16,
    p: 1,
    maxmem: 128 * 16384 * 16 * 2,
  });
  return `${saltHex}:${derived.toString("hex")}`;
}

export function setCredentialPassword(userId: string, plainPassword: string) {
  const db = openDb();
  ensurePlatformTables(db);
  const value = hashPassword(plainPassword);
  const updatedAt = new Date().toISOString();
  const r = db
    .prepare("UPDATE account SET password = ?, updatedAt = ? WHERE userId = ? AND providerId = 'credential'")
    .run(value, updatedAt, userId);
  const changed = Number(r.changes ?? 0);
  if (changed === 0) {
    const id = crypto.randomBytes(18).toString("base64url");
    db.prepare(
      `
      INSERT INTO account(
        id, createdAt, updatedAt,
        providerId, accountId, userId,
        password
      ) VALUES(
        ?, datetime('now'), datetime('now'),
        'credential', ?, ?,
        ?
      )
      `,
    ).run(id, userId, userId, value);
  }
  db.prepare("UPDATE user SET updatedAt = ? WHERE id = ?").run(updatedAt, userId);
}

export function deleteUser(userId: string) {
  const db = openDb();
  ensurePlatformTables(db);
  db.exec("BEGIN");
  try {
    db.prepare("DELETE FROM session WHERE userId = ?").run(userId);
    db.prepare("DELETE FROM account WHERE userId = ?").run(userId);
    db.prepare("DELETE FROM platform_user_flags WHERE user_id = ?").run(userId);
    db.prepare("DELETE FROM user WHERE id = ?").run(userId);
    db.exec("COMMIT");
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  }
}

function deleteThreadDataForUser(userId: string) {
  const dbPath = resolveDeerflowDbPath();
  if (!fs.existsSync(dbPath)) return [];
  const db = new DatabaseSync(dbPath) as unknown as Db;
  db.exec("PRAGMA foreign_keys = ON");
  try {
    const threads = db
      .prepare("SELECT thread_id AS threadId FROM threads WHERE owner_user_id = ?")
      .all(userId) as { threadId: string }[];
    db.exec("BEGIN");
    try {
      db.prepare("DELETE FROM threads WHERE owner_user_id = ?").run(userId);
      db.exec("COMMIT");
    } catch (e) {
      db.exec("ROLLBACK");
      throw e;
    }
    return threads.map((t) => asString(t.threadId)).filter(Boolean);
  } finally {
    (db as unknown as { close: () => void }).close();
  }
}

function deleteUserDirs(userId: string, threadIds: string[]) {
  const root = resolveDataRoot();
  const userDir = path.join(root, "users", safeUserId(userId));
  if (fs.existsSync(userDir)) {
    fs.rmSync(userDir, { recursive: true, force: true });
  }
  for (const tid of threadIds) {
    const legacyThreadDir = path.join(root, "threads", tid);
    if (fs.existsSync(legacyThreadDir)) {
      fs.rmSync(legacyThreadDir, { recursive: true, force: true });
    }
  }
}

export function deleteUserAndData(userId: string) {
  const threadIds = deleteThreadDataForUser(userId);
  deleteUser(userId);
  deleteUserDirs(userId, threadIds);
}

export function countAdmins() {
  const db = openDb();
  ensurePlatformTables(db);
  const admins = db
    .prepare("SELECT COUNT(1) AS cnt FROM platform_user_flags WHERE is_admin = 1")
    .get() as { cnt?: number } | undefined;
  return Number(admins?.cnt ?? 0);
}

export function getUserIdByEmail(email: string) {
  const e = email.trim().toLowerCase();
  if (!e) return null;
  const db = openDb();
  ensurePlatformTables(db);
  const row = db
    .prepare("SELECT id FROM user WHERE lower(email) = ? LIMIT 1")
    .get(e) as { id?: string } | undefined;
  return row?.id ?? null;
}

export function ensureBootstrapAdmin(_userId: string) {
  const db = openDb();
  ensurePlatformTables(db);
  const admins = db
    .prepare("SELECT COUNT(1) AS cnt FROM platform_user_flags WHERE is_admin = 1")
    .get() as { cnt?: number } | undefined;
  if (Number(admins?.cnt ?? 0) > 0) return false;

  const first = db
    .prepare("SELECT id FROM user ORDER BY createdAt ASC LIMIT 1")
    .get() as { id?: string } | undefined;
  if (!first?.id) return false;

  setUserFlags(first.id, { isAdmin: true });
  return true;
}

export function isAdminUser(userId: string) {
  ensureBootstrapAdmin(userId);
  return getUserFlags(userId).isAdmin;
}

export function isDisabledUser(userId: string) {
  return getUserFlags(userId).isDisabled;
}

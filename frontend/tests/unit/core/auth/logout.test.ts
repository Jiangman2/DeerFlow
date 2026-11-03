import { afterEach, expect, rs, test } from "@rstest/core";

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
  rs.resetModules();
});

test("resets the persisted Data Lake preference during logout", async () => {
  const storage = new Map<string, string>();
  (globalThis as { window?: unknown }).window = {
    addEventListener: () => undefined,
  };
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  };

  const { resetLocalSettingsForLogout } = await import("@/core/auth/logout");
  const { getLocalSettings } = await import("@/core/settings/local");
  const { getBaseSettingsSnapshot, updateLocalSettings } =
    await import("@/core/settings/store");

  updateLocalSettings("context", { datalake_enabled: true });
  resetLocalSettingsForLogout();

  expect(getBaseSettingsSnapshot().context.datalake_enabled).toBe(false);
  expect(getLocalSettings().context.datalake_enabled).toBe(false);
});

test("does not abort logout when reading local storage fails", async () => {
  (globalThis as { window?: unknown }).window = {
    addEventListener: () => undefined,
  };
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: () => {
      throw new DOMException("Storage is disabled", "SecurityError");
    },
    setItem: () => undefined,
  };

  const { resetLocalSettingsForLogout } = await import("@/core/auth/logout");

  expect(() => resetLocalSettingsForLogout()).not.toThrow();
});

test("does not abort logout when writing local storage fails", async () => {
  (globalThis as { window?: unknown }).window = {
    addEventListener: () => undefined,
  };
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: () => null,
    setItem: () => {
      throw new DOMException("Storage quota exceeded", "QuotaExceededError");
    },
  };

  const { resetLocalSettingsForLogout } = await import("@/core/auth/logout");

  expect(() => resetLocalSettingsForLogout()).not.toThrow();
});

import { afterEach, expect, test } from "@rstest/core";

import {
  DEFAULT_LOCAL_SETTINGS,
  LOCAL_SETTINGS_KEY,
  applyThreadModelOverride,
  getLocalSettings,
  mergeLocalSettings,
  saveLocalSettings,
} from "@/core/settings/local";
import {
  getBaseSettingsSnapshot,
  updateThreadSettings,
} from "@/core/settings/store";

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

test("defaults the user Data Lake setting to disabled", () => {
  expect(DEFAULT_LOCAL_SETTINGS.context.datalake_enabled).toBe(false);
});

test("defaults a missing chat mode to flash without overriding a saved choice", () => {
  expect(DEFAULT_LOCAL_SETTINGS.context.mode).toBe("flash");
  expect(mergeLocalSettings({ context: {} }).context.mode).toBe("flash");
  expect(mergeLocalSettings({ context: { mode: "pro" } }).context.mode).toBe(
    "pro",
  );
});

test("defaults an invalid persisted Data Lake setting to disabled", () => {
  const invalidSettings = JSON.parse(
    '{"context":{"datalake_enabled":"false"}}',
  ) as Partial<typeof DEFAULT_LOCAL_SETTINGS>;

  expect(mergeLocalSettings(invalidSettings).context.datalake_enabled).toBe(
    false,
  );
});

test("keeps the persisted Data Lake setting across thread model overrides", () => {
  const userSettings = mergeLocalSettings({
    context: {
      ...DEFAULT_LOCAL_SETTINGS.context,
      datalake_enabled: false,
    },
  });

  expect(
    applyThreadModelOverride(userSettings, "model-a").context.datalake_enabled,
  ).toBe(false);
  expect(
    applyThreadModelOverride(userSettings, "model-b").context.datalake_enabled,
  ).toBe(false);
  expect(userSettings.context.datalake_enabled).toBe(false);
});

test("persists the Data Lake setting in one global localStorage document", () => {
  const storage = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  };
  (globalThis as { window?: unknown }).window = {};
  (globalThis as { localStorage?: unknown }).localStorage = localStorage;

  const settings = mergeLocalSettings({
    context: {
      ...DEFAULT_LOCAL_SETTINGS.context,
      datalake_enabled: false,
    },
  });
  saveLocalSettings(settings);

  expect([...storage.keys()]).toEqual([LOCAL_SETTINGS_KEY]);
  expect(getLocalSettings().context.datalake_enabled).toBe(false);
});

test("shares one Data Lake value across threads and supports re-enable", () => {
  const storage = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  };
  (globalThis as { window?: unknown }).window = {
    addEventListener: () => undefined,
  };
  (globalThis as { localStorage?: unknown }).localStorage = localStorage;

  updateThreadSettings("thread-a", "context", { datalake_enabled: false });
  expect(getBaseSettingsSnapshot().context.datalake_enabled).toBe(false);

  updateThreadSettings("thread-b", "context", { datalake_enabled: true });
  expect(getBaseSettingsSnapshot().context.datalake_enabled).toBe(true);
  expect(getLocalSettings().context.datalake_enabled).toBe(true);
});

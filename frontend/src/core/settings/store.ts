import {
  DEFAULT_LOCAL_SETTINGS,
  THREAD_DATA_LAKE_KEY_PREFIX,
  LOCAL_SETTINGS_KEY,
  THREAD_MODEL_KEY_PREFIX,
  getThreadDataLakeEnabled,
  getLocalSettings,
  getThreadModelName,
  saveLocalSettings,
  saveThreadDataLakeEnabled,
  saveThreadModelName,
  type LocalSettings,
} from "./local";

type Listener = () => void;

export type LocalSettingsSetter = <K extends keyof LocalSettings>(
  key: K,
  value: Partial<LocalSettings[K]>,
) => void;

const listeners = new Set<Listener>();
const threadModelNames = new Map<string, string | undefined>();
const threadDataLakeEnabled = new Map<string, boolean>();

let baseSettings: LocalSettings = DEFAULT_LOCAL_SETTINGS;
let baseSettingsLoaded = false;
let storageListenerRegistered = false;

function emitChange() {
  for (const listener of listeners) {
    listener();
  }
}

function ensureBaseSettingsLoaded() {
  if (baseSettingsLoaded || typeof window === "undefined") {
    return;
  }

  baseSettings = getLocalSettings();
  baseSettingsLoaded = true;
}

function ensureStorageListenerRegistered() {
  if (storageListenerRegistered || typeof window === "undefined") {
    return;
  }

  window.addEventListener("storage", handleStorage);
  storageListenerRegistered = true;
}

function mergeSettingsSection<K extends keyof LocalSettings>(
  settings: LocalSettings,
  key: K,
  value: Partial<LocalSettings[K]>,
): LocalSettings {
  return {
    ...settings,
    [key]: {
      ...settings[key],
      ...value,
    },
  } as LocalSettings;
}

function handleStorage(event: StorageEvent) {
  if (event.storageArea && event.storageArea !== localStorage) {
    return;
  }

  ensureBaseSettingsLoaded();

  if (event.key === null) {
    baseSettings = getLocalSettings();
    threadModelNames.clear();
    threadDataLakeEnabled.clear();
    emitChange();
    return;
  }

  if (event.key === LOCAL_SETTINGS_KEY) {
    baseSettings = getLocalSettings();
    emitChange();
    return;
  }

  if (!event.key.startsWith(THREAD_MODEL_KEY_PREFIX)) {
    if (!event.key.startsWith(THREAD_DATA_LAKE_KEY_PREFIX)) {
      return;
    }

    const threadId = event.key.slice(THREAD_DATA_LAKE_KEY_PREFIX.length);
    threadDataLakeEnabled.set(threadId, getThreadDataLakeEnabled(threadId));
    emitChange();
    return;
  }

  const threadId = event.key.slice(THREAD_MODEL_KEY_PREFIX.length);
  threadModelNames.set(threadId, getThreadModelName(threadId));
  emitChange();
}

export function subscribe(listener: Listener): () => void {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  listeners.add(listener);

  return () => {
    listeners.delete(listener);
  };
}

export function getBaseSettingsSnapshot(): LocalSettings {
  ensureBaseSettingsLoaded();
  return baseSettings;
}

export function getThreadModelSnapshot(threadId: string): string | undefined {
  ensureBaseSettingsLoaded();

  if (!threadModelNames.has(threadId)) {
    threadModelNames.set(threadId, getThreadModelName(threadId));
  }

  return threadModelNames.get(threadId);
}

export function getThreadDataLakeSnapshot(threadId: string): boolean {
  ensureBaseSettingsLoaded();

  if (!threadDataLakeEnabled.has(threadId)) {
    threadDataLakeEnabled.set(threadId, getThreadDataLakeEnabled(threadId));
  }

  return threadDataLakeEnabled.get(threadId) ?? true;
}

export const updateLocalSettings: LocalSettingsSetter = (key, value) => {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();

  baseSettings = mergeSettingsSection(baseSettings, key, value);
  saveLocalSettings(baseSettings);
  emitChange();
};

export function updateThreadSettings<K extends keyof LocalSettings>(
  threadId: string,
  key: K,
  value: Partial<LocalSettings[K]>,
) {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();

  const isDataLakeUpdate =
    key === "context" &&
    Object.prototype.hasOwnProperty.call(value, "datalake_enabled");
  const baseContextValue = isDataLakeUpdate
    ? Object.fromEntries(
        Object.entries(value as Partial<LocalSettings["context"]>).filter(
          ([contextKey]) => contextKey !== "datalake_enabled",
        ),
      )
    : value;
  const nextBaseSettings = isDataLakeUpdate
    ? mergeSettingsSection(baseSettings, key, baseContextValue)
    : mergeSettingsSection(baseSettings, key, value);
  baseSettings = nextBaseSettings;
  saveLocalSettings(baseSettings);

  if (
    key === "context" &&
    Object.prototype.hasOwnProperty.call(value, "model_name")
  ) {
    const contextValue = value as Partial<LocalSettings["context"]>;
    const threadModelName = contextValue.model_name;
    threadModelNames.set(threadId, threadModelName);
    saveThreadModelName(threadId, threadModelName);
  }

  if (isDataLakeUpdate) {
    const contextValue = value as Partial<LocalSettings["context"]>;
    const enabled = contextValue.datalake_enabled;
    if (typeof enabled === "boolean") {
      threadDataLakeEnabled.set(threadId, enabled);
      saveThreadDataLakeEnabled(threadId, enabled);
    }
  }

  emitChange();
}

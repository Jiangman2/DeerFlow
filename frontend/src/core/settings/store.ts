import {
  DEFAULT_LOCAL_SETTINGS,
  LOCAL_SETTINGS_KEY,
  THREAD_MODEL_KEY_PREFIX,
  getLocalSettings,
  getThreadModelName,
  saveLocalSettings,
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

let baseSettings: LocalSettings = DEFAULT_LOCAL_SETTINGS;
let baseSettingsLoaded = false;
let storageListenerRegistered = false;
let dataLakeAuthorizationLifetime = new AbortController();

function invalidateDataLakeAuthorization() {
  const previous = dataLakeAuthorizationLifetime;
  dataLakeAuthorizationLifetime = new AbortController();
  previous.abort();
}

// Cancellation identity only: the authorization value remains in settings.context.
export function getDataLakeAuthorizationSignal(): AbortSignal {
  ensureStorageListenerRegistered();
  return dataLakeAuthorizationLifetime.signal;
}

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
  window.addEventListener("pagehide", () => {
    updateLocalSettings("context", { datalake_enabled: false });
  });
  storageListenerRegistered = true;
}

function mergeSettingsSection<K extends keyof LocalSettings>(
  settings: LocalSettings,
  key: K,
  value: Partial<LocalSettings[K]>,
): LocalSettings {
  if (
    key === "context" &&
    (value as Partial<LocalSettings["context"]>).datalake_enabled === false
  ) {
    invalidateDataLakeAuthorization();
  }
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
    invalidateDataLakeAuthorization();
    baseSettings = getLocalSettings();
    threadModelNames.clear();
    emitChange();
    return;
  }

  if (event.key === LOCAL_SETTINGS_KEY) {
    const restored = getLocalSettings();
    // Other tabs may update preferences, but cannot grant/revoke this tab's
    // confirmed state just by saving their (always disabled) persisted flag.
    baseSettings = {
      ...restored,
      context: {
        ...restored.context,
        datalake_enabled: baseSettings.context.datalake_enabled,
      },
    };
    emitChange();
    return;
  }

  if (!event.key.startsWith(THREAD_MODEL_KEY_PREFIX)) {
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

export function initializeThreadModel(threadId: string, modelName: string) {
  // Only initialize this conversation. Never replace an explicit selection or
  // rewrite the global preference used by existing chats and other surfaces.
  if (getThreadModelSnapshot(threadId)) {
    return;
  }
  threadModelNames.set(threadId, modelName);
  saveThreadModelName(threadId, modelName);
  emitChange();
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

  baseSettings = mergeSettingsSection(baseSettings, key, value);
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

  emitChange();
}

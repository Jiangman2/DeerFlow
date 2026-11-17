/** Stable portal keys; values are native employee names, never destination URLs. */
export const STANDARDS_MODULE_KEYS = [
  "qa",
  "search",
  "policy",
  "comparison",
  "applicability",
  "compliance",
  "authoring",
] as const;

export type StandardsModuleKey = (typeof STANDARDS_MODULE_KEYS)[number];

export type StandardsPortalIssue = {
  code:
    | "too_large"
    | "invalid_json"
    | "invalid_root"
    | "unknown_module"
    | "invalid_agent_name";
  module?: StandardsModuleKey;
};

export type StandardsPortalConfig = {
  modules: Partial<Record<StandardsModuleKey, string>>;
  issues: StandardsPortalIssue[];
};

// Seven names of at most 64 Unicode characters fit even when JSON-escaped.
export const MAX_STANDARDS_PORTAL_CONFIG_LENGTH = 8 * 1024;

const MAX_AGENT_NAME_LENGTH = 64;
const AGENT_NAME_PATTERN = /^[\p{L}\p{N}-]+$/u;
const moduleKeys = new Set<string>(STANDARDS_MODULE_KEYS);

function normalizeAgentName(value: unknown): string | undefined {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_AGENT_NAME_LENGTH * 2 ||
    !AGENT_NAME_PATTERN.test(value) ||
    [...value].length > MAX_AGENT_NAME_LENGTH
  ) {
    return undefined;
  }

  // Native employee creation normalizes internal names to lowercase. Validate
  // again because a Unicode lowercase conversion may expand a character.
  const normalized = value.toLowerCase();
  return AGENT_NAME_PATTERN.test(normalized) &&
    [...normalized].length <= MAX_AGENT_NAME_LENGTH
    ? normalized
    : undefined;
}

/** Build a same-origin new-chat route, encoding a validated name exactly once. */
export function buildStandardsAgentHref(name: string): string | undefined {
  const normalized = normalizeAgentName(name);
  return normalized
    ? `/workspace/agents/${encodeURIComponent(normalized)}/chats/new`
    : undefined;
}

/**
 * Parse STANDARDS_PORTAL_AGENTS_JSON supplied by the frontend server at request
 * time. Missing entries stay unconfigured; invalid entries cannot redirect to
 * another origin. Diagnostics deliberately contain no raw config values.
 */
export function parseStandardsPortalConfig(
  raw: string | undefined,
): StandardsPortalConfig {
  const result: StandardsPortalConfig = { modules: {}, issues: [] };
  if (raw === undefined || raw === "") return result;
  if (raw.length > MAX_STANDARDS_PORTAL_CONFIG_LENGTH) {
    result.issues.push({ code: "too_large" });
    return result;
  }
  if (raw.trim() === "") return result;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    result.issues.push({ code: "invalid_json" });
    return result;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    result.issues.push({ code: "invalid_root" });
    return result;
  }

  let unknownModuleReported = false;
  for (const [key, value] of Object.entries(parsed)) {
    if (!moduleKeys.has(key)) {
      if (!unknownModuleReported) {
        result.issues.push({ code: "unknown_module" });
        unknownModuleReported = true;
      }
      continue;
    }

    const moduleKey = key as StandardsModuleKey;
    if (value === "") continue;
    const name = normalizeAgentName(value);
    if (name === undefined) {
      result.issues.push({ code: "invalid_agent_name", module: moduleKey });
      continue;
    }
    result.modules[moduleKey] = name;
  }
  return result;
}

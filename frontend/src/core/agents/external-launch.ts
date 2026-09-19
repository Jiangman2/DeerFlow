const EXTERNAL_AGENT_KEYS = [
  "zywdjxzj",
  "qqyjbgbxzj",
  "zjbgjszj",
  "zdsczj",
  "dzsjtpzj",
] as const;

const LEGACY_EXTERNAL_AGENT_HOST = "datalake.cnooc";
const CNOOCCLAW_EXTERNAL_AGENT_HOST = "cnoocclaw.cnooc.com.cn";

/**
 * Keep the current CNOOCClaw aliases as direct, same-origin entry points. The
 * legacy Data Lake URLs still use the authenticated launch boundary so browser
 * JavaScript never needs to append the platform token itself.
 */
export function resolveExternalAgentLaunchHref(
  externalUrl: string | null | undefined,
): string | undefined {
  const value = externalUrl?.trim();
  if (!value) return undefined;

  try {
    const url = new URL(value);
    const key = url.pathname.replace(/^\/+|\/+$/g, "");
    const hostname = url.hostname.toLowerCase();
    const isApprovedAgent = EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );

    if (
      url.protocol === "https:" &&
      hostname === CNOOCCLAW_EXTERNAL_AGENT_HOST &&
      isApprovedAgent
    ) {
      return value;
    }

    if (
      url.protocol === "https:" &&
      hostname === LEGACY_EXTERNAL_AGENT_HOST &&
      isApprovedAgent
    ) {
      return `/api/cnooc/external-agents/${key}/launch`;
    }
  } catch {
    // Preserve the existing behavior for a non-URL custom external target.
  }

  return value;
}

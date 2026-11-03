const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

export function isSsoOnlyMode(value: string | undefined): boolean {
  return TRUE_VALUES.has(value?.trim().toLowerCase() ?? "");
}

export function resolveSsoOnlyAuthRedirect(
  enabled: string | undefined,
  configuredUrl: string | undefined,
): string | null {
  if (!isSsoOnlyMode(enabled)) {
    return null;
  }

  const candidate = configuredUrl?.trim();
  if (candidate) {
    try {
      const target = new URL(candidate);
      if (target.protocol === "https:") {
        return target.toString();
      }
    } catch {
      // Fall through to the fail-closed local landing page.
    }
  }

  // Never render local login/setup forms merely because the external portal
  // URL is missing or malformed. The landing page contains no credential form.
  return "/";
}

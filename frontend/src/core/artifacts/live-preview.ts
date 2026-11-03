/** Audited upstream Confirm UI and SVG editor first-free-port windows. */
export function isLivePreviewPort(port: string): boolean {
  if (!/^\d{4}$/.test(port)) return false;
  const number = Number(port);
  return (
    (number >= 5050 && number <= 5099) || (number >= 6060 && number <= 6109)
  );
}

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);
const LOOPBACK_ORIGIN =
  /^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):(\d{4})(?=$|[/?#。！？；，])/i;
const PREVIEW_TEXT_URL =
  /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):(\d{4})(?=$|[/?#。！？；，\s*`\)])/gi;
const DISPLAY_TEXT_URL = /https?:\/\/[^\s<>`*\)]+/gi;

/** Display-only summaries; never rewrite persisted receipts or executable args. */
export function maskSandboxURLDestinations(
  text: string,
  label: string,
): string {
  return text.replace(DISPLAY_TEXT_URL, (href) =>
    isSandboxInternalURL(href) ? label : href,
  );
}

export function parseLivePreviewURL(href: string): URL | null {
  const origin = LOOPBACK_ORIGIN.exec(href)?.[0];
  const suffix = origin ? href.slice(origin.length) : "";
  const candidate =
    origin && /^[。！？；，]/.test(suffix) ? `${origin}/` : href;
  try {
    const parsed = new URL(candidate);
    return ["http:", "https:"].includes(parsed.protocol) &&
      LOOPBACK_HOSTS.has(parsed.hostname.toLowerCase()) &&
      !parsed.username &&
      !parsed.password &&
      isLivePreviewPort(parsed.port)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

export function containsLivePreviewURL(text: string): boolean {
  // matchAll uses a fresh iterator: no stateful RegExp.test across card renders.
  for (const match of text.matchAll(PREVIEW_TEXT_URL)) {
    if (isLivePreviewPort(match[1] ?? "")) return true;
  }
  return false;
}

export function isSandboxInternalURL(href: string | undefined): boolean {
  if (!href) return false;
  if (LOOPBACK_ORIGIN.test(href)) return true;
  try {
    const host = new URL(href).hostname.toLowerCase();
    return (
      LOOPBACK_HOSTS.has(host) ||
      host === "0.0.0.0" ||
      /^sandbox-[a-z0-9-]+(?:\.deer-flow)?\.svc(?:\.cluster\.local)?$/.test(
        host,
      )
    );
  } catch {
    return false;
  }
}

export function previewTrailingProse(text: string): string | undefined {
  const origin = LOOPBACK_ORIGIN.exec(text)?.[0];
  const suffix = origin ? text.slice(origin.length) : "";
  return origin && parseLivePreviewURL(origin) && /^[。！？；，]/.test(suffix)
    ? suffix
    : undefined;
}

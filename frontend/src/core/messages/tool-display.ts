/** Model arguments and provider results are untrusted, including mid-stream. */
export function toolDisplayRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function toolDisplayText(
  value: Record<string, unknown>,
  key: string,
): string | undefined {
  return typeof value[key] === "string" ? value[key] : undefined;
}

/** Tool-result navigations allow absolute HTTP(S), never OS/local handlers. */
export function toolWebHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const href = value.trim();
  const hasControlOrBackslash = [...href].some(
    (char) =>
      char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 || char === "\\",
  );
  if (!/^https?:\/\//i.test(href) || hasControlOrBackslash) {
    return null;
  }
  try {
    const url = new URL(href);
    return (url.protocol === "http:" || url.protocol === "https:") &&
      url.hostname &&
      !url.username &&
      !url.password
      ? href
      : null;
  } catch {
    return null;
  }
}

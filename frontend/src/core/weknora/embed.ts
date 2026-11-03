export const WEKNORA_EMBED_PATH = "/platform/knowledge-bases";

const STANDARD_USER_PATH_PREFIXES = [
  "/platform/creatChat",
  "/platform/chat/",
  "/platform/knowledge-bases",
  "/platform/organizations",
  "/platform/agents",
] as const;

export function isStandardWeKnoraPath(pathname: string): boolean {
  return STANDARD_USER_PATH_PREFIXES.some(
    (prefix) =>
      pathname === prefix ||
      pathname.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`),
  );
}

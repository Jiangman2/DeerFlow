import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import type { AgentThreadState } from "../threads";

const EMPTY_ARTIFACT_PATHS: readonly string[] = [];

export function urlOfArtifact({
  filepath,
  threadId,
  download = false,
  isMock = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
  isMock?: boolean;
}) {
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath, threadId, download });
  }
  if (isMock) {
    return `${getBackendBaseURL()}/mock/api/threads/${threadId}/artifacts${filepath}${download ? "?download=true" : ""}`;
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${filepath}${download ? "?download=true" : ""}`;
}

export function findPreviewPdfPath(
  filepath: string,
  files: string[],
): string | null {
  return guessPreviewPdfPaths(filepath).find((path) => files.includes(path)) ?? null;
}

export function guessPreviewPdfPath(filepath: string): string | null {
  return guessPreviewPdfPaths(filepath)[0] ?? null;
}

export function guessPreviewPdfPaths(filepath: string): string[] {
  if (filepath.toLowerCase().endsWith(".pptx")) {
    return [
      filepath.replace(/\.pptx$/i, ".preview.pdf"),
      filepath.replace(/\.pptx$/i, ".pdf"),
    ];
  }
  if (filepath.toLowerCase().endsWith(".ppt")) {
    return [
      filepath.replace(/\.ppt$/i, ".preview.pdf"),
      filepath.replace(/\.ppt$/i, ".pdf"),
    ];
  }
  return [];
}

export function artifactPathFromHref(
  href: string | undefined,
  threadId: string,
): string | null {
  if (!href) {
    return null;
  }
  let parsed: URL;
  try {
    parsed = new URL(href, "http://deerflow.local");
  } catch {
    return null;
  }
  if (parsed.searchParams.get("download") === "true") {
    return null;
  }
  if (parsed.pathname.startsWith("/mnt/")) {
    return decodeURIComponent(parsed.pathname);
  }
  const prefix = `/api/threads/${encodeURIComponent(threadId)}/artifacts`;
  if (!parsed.pathname.startsWith(`${prefix}/mnt/`)) {
    return null;
  }
  return decodeURIComponent(parsed.pathname.slice(prefix.length));
}

export function extractArtifactsFromThread(thread: {
  values: Pick<AgentThreadState, "artifacts">;
}) {
  return thread.values.artifacts ?? EMPTY_ARTIFACT_PATHS;
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath: absolutePath, threadId });
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${absolutePath}`;
}

export function resolveMessageImageURL(
  src: string,
  threadId: string,
  artifactPaths: readonly string[],
) {
  if (/^resource:\/\/[A-Za-z0-9_-]{1,256}$/.test(src)) {
    return `${getBackendBaseURL()}/api/weknora/files?file_path=${encodeURIComponent(src)}`;
  }

  if (src.startsWith("/mnt/")) {
    return resolveArtifactURL(src, threadId);
  }

  const [relativePath = ""] = src.split(/[?#]/, 1);
  const normalizedPath = relativePath.replace(/^(?:\.\/)+/, "");
  if (
    !normalizedPath ||
    normalizedPath.startsWith("/") ||
    /^[a-z][a-z\d+.-]*:/i.test(normalizedPath) ||
    normalizedPath.startsWith("//") ||
    normalizedPath.split("/").includes("..")
  ) {
    return src;
  }

  const matches = artifactPaths.filter((path) =>
    path.endsWith(`/${normalizedPath}`),
  );
  if (matches.length !== 1) {
    return src;
  }

  return `${resolveArtifactURL(matches[0]!, threadId)}${src.slice(relativePath.length)}`;
}

function staticDemoArtifactURL({
  filepath,
  threadId,
  download = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
}) {
  const demoPath = filepath.replace(/^\/mnt\//, "/");
  return `${getBackendBaseURL()}/demo/threads/${threadId}${demoPath}${download ? "?download=true" : ""}`;
}

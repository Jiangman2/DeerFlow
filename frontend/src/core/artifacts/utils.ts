import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import type { AgentThreadState } from "../threads";

const EMPTY_ARTIFACT_PATHS: readonly string[] = [];

export function urlOfArtifact({
  filepath,
  threadId,
  download = false,
  format,
  isMock = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
  format?: string;
  isMock?: boolean;
}) {
  const query = new URLSearchParams();
  if (download) {
    query.set("download", "true");
  }
  if (format) {
    query.set("format", format);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath, threadId, suffix });
  }
  if (isMock) {
    return `${getBackendBaseURL()}/mock/api/threads/${threadId}/artifacts${filepath}${suffix}`;
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${filepath}${suffix}`;
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
  suffix = "",
}: {
  filepath: string;
  threadId: string;
  suffix?: string;
}) {
  const demoPath = filepath.replace(/^\/mnt\//, "/");
  return `${getBackendBaseURL()}/demo/threads/${threadId}${demoPath}${suffix}`;
}

export function findPreviewPdfPath(
  filepath: string,
  files: string[],
): string | null {
  const previewPath = guessPreviewPdfPath(filepath);
  if (!previewPath) {
    return null;
  }
  return files.includes(previewPath) ? previewPath : null;
}

export function guessPreviewPdfPath(filepath: string): string | null {
  if (filepath.toLowerCase().endsWith(".pptx")) {
    return filepath.replace(/\.pptx$/i, ".preview.pdf");
  }
  if (filepath.toLowerCase().endsWith(".ppt")) {
    return filepath.replace(/\.ppt$/i, ".preview.pdf");
  }
  return null;
}

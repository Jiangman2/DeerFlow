import type { AgentThread } from "../threads";

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
  const qs = new URLSearchParams();
  if (download) qs.set("download", "true");
  if (format) qs.set("format", format);
  const suffix = qs.size ? `?${qs.toString()}` : "";
  if (isMock) {
    return `/mock/api/threads/${threadId}/artifacts${filepath}${suffix}`;
  }
  return `/api/threads/${threadId}/artifacts${filepath}${suffix}`;
}

export function extractArtifactsFromThread(thread: AgentThread) {
  return thread.values.artifacts ?? [];
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  return `/api/threads/${threadId}/artifacts${absolutePath}`;
}

export function findPreviewPdfPath(
  filepath: string,
  files: string[],
): string | null {
  if (!filepath.toLowerCase().endsWith(".pptx")) {
    return null;
  }
  const previewPath = filepath.replace(/\.pptx$/i, ".preview.pdf");
  return files.includes(previewPath) ? previewPath : null;
}

export function guessPreviewPdfPath(filepath: string): string | null {
  if (!filepath.toLowerCase().endsWith(".pptx")) {
    return null;
  }
  return filepath.replace(/\.pptx$/i, ".preview.pdf");
}

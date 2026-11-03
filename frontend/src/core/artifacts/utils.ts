import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import type { AgentThreadState } from "../threads";
import { isStaticDemoThreadId } from "../threads/static-demo";

const EMPTY_ARTIFACT_PATHS: readonly string[] = [];
const OFFICE_PREVIEW_EXTENSIONS = [
  ".docx",
  ".doc",
  ".xlsx",
  ".xls",
  ".pptx",
  ".ppt",
  ".odt",
  ".ods",
  ".odp",
] as const;

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
  if (isStaticWebsiteOnly() || isStaticDemoThreadId(threadId)) {
    return staticDemoArtifactURL({ filepath, threadId, download });
  }
  if (isMock) {
    return `${getBackendBaseURL()}/mock/api/threads/${threadId}/artifacts${filepath}${download ? "?download=true" : ""}`;
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${filepath}${download ? "?download=true" : ""}`;
}

export function urlOfPdfArtifactPreview({
  filepath,
  threadId,
  isMock = false,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  if (isStaticWebsiteOnly() || isStaticDemoThreadId(threadId)) {
    const staticPreviewPath = filepath.replace(
      /\.preview\.pdf$/i,
      ".preview.html",
    );
    return urlOfArtifact({
      filepath: staticPreviewPath,
      threadId,
      isMock,
    });
  }
  if (isMock) {
    return urlOfArtifact({ filepath, threadId, isMock });
  }
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifact-previews/pdf${filepath}`;
}

export function findPreviewPdfPath(
  filepath: string,
  files: string[],
): string | null {
  return (
    guessPreviewPdfPaths(filepath).find((path) => files.includes(path)) ?? null
  );
}

export function guessPreviewPdfPath(filepath: string): string | null {
  return guessPreviewPdfPaths(filepath)[0] ?? null;
}

export function guessPreviewPdfPaths(filepath: string): string[] {
  const lowerFilepath = filepath.toLowerCase();
  const extension = OFFICE_PREVIEW_EXTENSIONS.find((candidate) =>
    lowerFilepath.endsWith(candidate),
  );
  if (extension) {
    const stem = filepath.slice(0, -extension.length);
    return [`${stem}.preview.pdf`, `${stem}.pdf`];
  }
  return [];
}

export function findOfficeDocumentForPreviewArtifact(
  filepath: string,
  files: readonly string[],
): string | null {
  const normalizedFilepath = filepath.toLowerCase();
  return (
    files.find((candidate) =>
      guessPreviewPdfPaths(candidate).some(
        (previewPath) => previewPath.toLowerCase() === normalizedFilepath,
      ),
    ) ?? null
  );
}

/** @deprecated Use findOfficeDocumentForPreviewArtifact. */
export const findPowerPointForPreviewArtifact =
  findOfficeDocumentForPreviewArtifact;

/**
 * Keep generated Office preview PDFs as implementation details. They stay in
 * the artifact collection so the viewer can load them, but users select and
 * download the owning document rather than seeing duplicate deliverables.
 */
export function filterUserVisibleArtifacts(
  files: readonly string[],
  relatedFiles: readonly string[] = files,
): string[] {
  return files.filter(
    (filepath) =>
      findOfficeDocumentForPreviewArtifact(filepath, relatedFiles) === null,
  );
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
  const previewHashPrefix = "#artifact-preview=";
  if (parsed.hash.startsWith(previewHashPrefix)) {
    try {
      const artifactPath = decodeURIComponent(
        parsed.hash.slice(previewHashPrefix.length),
      );
      return artifactPath.startsWith("/mnt/") ? artifactPath : null;
    } catch {
      return null;
    }
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

export function artifactPreviewHref(filepath: string): string {
  return `#artifact-preview=${encodeURIComponent(filepath)}`;
}

export function rewriteArtifactLinksForPreview(
  markdown: string,
  threadId: string,
): string {
  // Rewrite ordinary inline links before Streamdown sees the Markdown. Its
  // block memoization keys on `children` and intentionally ignores renderer
  // and plugin props, so changing only a custom anchor component or rehype
  // plugin can leave a previously rendered raw artifact URL in the DOM.
  return markdown.replace(
    /(?<!!)(\[[^\]\r\n]*\]\()([^\s)]+)([^)\r\n]*\))/g,
    (match, opening: string, href: string, closing: string) => {
      const artifactPath = artifactPathFromHref(href, threadId);
      if (!artifactPath) {
        return match;
      }
      return `${opening}${artifactPreviewHref(artifactPath)}${closing}`;
    },
  );
}

export function extractArtifactsFromThread(thread: {
  values: Pick<AgentThreadState, "artifacts">;
}) {
  return thread.values.artifacts ?? EMPTY_ARTIFACT_PATHS;
}

export function extractArtifactsFromStateSnapshot(snapshot: unknown): string[] {
  if (typeof snapshot !== "object" || snapshot === null) {
    return [];
  }
  const values = Reflect.get(snapshot, "values");
  if (typeof values !== "object" || values === null) {
    return [];
  }
  const artifacts = Reflect.get(values, "artifacts");
  return Array.isArray(artifacts)
    ? artifacts.filter((path): path is string => typeof path === "string")
    : [];
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  if (isStaticWebsiteOnly() || isStaticDemoThreadId(threadId)) {
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

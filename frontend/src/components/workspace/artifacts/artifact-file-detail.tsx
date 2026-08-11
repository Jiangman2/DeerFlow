import {
  Code2Icon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  LoaderIcon,
  PackageIcon,
  SquareArrowOutUpRightIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact";
import { Button } from "@/components/ui/button";
import { Select, SelectItem } from "@/components/ui/select";
import {
  SelectContent,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { CodeEditor } from "@/components/workspace/code-editor";
import { useArtifactContent } from "@/core/artifacts/hooks";
import {
  appendHtmlPreviewBaseHref,
  appendHtmlPreviewScrollRestoration,
  createHtmlPreviewScrollKey,
  getArtifactViewState,
  HTML_PREVIEW_SCROLL_MESSAGE_SOURCE,
} from "@/core/artifacts/preview";
import {
  filterUserVisibleArtifacts,
  findPreviewPdfPath,
  guessPreviewPdfPaths,
  urlOfArtifact,
  urlOfPdfArtifactPreview,
} from "@/core/artifacts/utils";
import { useAuth } from "@/core/auth/AuthProvider";
import { extractCitationSources } from "@/core/citations/sources";
import { writeTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import { findToolCallResult } from "@/core/messages/utils";
import { installSkill, SkillRequestError } from "@/core/skills/api";
import { SafeStreamdown } from "@/core/streamdown/components";
import {
  canBrowserPreviewFile,
  checkCodeFile,
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
} from "@/core/utils/files";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { ArtifactLink } from "../citations/artifact-link";
import { CitationSourcesPanel } from "../citations/citation-sources-panel";
import { useThread } from "../messages/context";
import { Tooltip } from "../tooltip";

import { useArtifacts } from "./context";
import { artifactMarkdownPlugins } from "./markdown-preview-plugins";

const WRITE_FILE_PREVIEW_REFRESH_INTERVAL_MS = 3000;
const PRESENTATION_PREVIEW_PROBE_DELAYS_MS = [0, 300, 900] as const;

export function ArtifactFileDetail({
  className,
  filepath: filepathFromProps,
  threadId,
}: {
  className?: string;
  filepath: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.system_role === "admin";
  const { artifacts, open, setOpen, select } = useArtifacts();
  const { thread, isMock } = useThread();
  const isWriteFile = useMemo(() => {
    return filepathFromProps.startsWith("write-file:");
  }, [filepathFromProps]);
  const filepath = useMemo(() => {
    if (isWriteFile) {
      const url = new URL(filepathFromProps);
      return decodeURIComponent(url.pathname);
    }
    return filepathFromProps;
  }, [filepathFromProps, isWriteFile]);
  // Keep these local because ChatBox replaces context artifacts with thread state.
  const [openedPresentedFilepaths, setOpenedPresentedFilepaths] = useState<
    string[]
  >(() => {
    if (isWriteFile || artifacts.includes(filepath)) {
      return [];
    }
    return [filepath];
  });
  useEffect(() => {
    if (isWriteFile || artifacts.includes(filepath)) {
      return;
    }
    setOpenedPresentedFilepaths((current) => {
      if (current.includes(filepath)) {
        return current;
      }
      return [...current, filepath];
    });
  }, [artifacts, filepath, isWriteFile]);
  const artifactOptions = useMemo(() => {
    const visibleArtifacts = filterUserVisibleArtifacts(artifacts);
    if (isWriteFile) {
      return visibleArtifacts;
    }
    const currentIsPresented = !visibleArtifacts.includes(filepath);
    const presentedFilepaths =
      currentIsPresented && !openedPresentedFilepaths.includes(filepath)
        ? [...openedPresentedFilepaths, filepath]
        : openedPresentedFilepaths;
    const presentedSet = new Set(presentedFilepaths);
    return [
      ...presentedFilepaths,
      ...visibleArtifacts.filter((artifact) => !presentedSet.has(artifact)),
    ];
  }, [artifacts, filepath, isWriteFile, openedPresentedFilepaths]);
  const isSkillFile = useMemo(() => {
    return filepath.endsWith(".skill");
  }, [filepath]);
  const { isCodeFile, language } = useMemo(() => {
    if (isWriteFile) {
      let language = checkCodeFile(filepath).language;
      language ??= "text";
      return { isCodeFile: true, language };
    }
    // Treat .skill files as markdown (they contain SKILL.md)
    if (isSkillFile) {
      return { isCodeFile: true, language: "markdown" };
    }
    return checkCodeFile(filepath);
  }, [filepath, isWriteFile, isSkillFile]);
  const isSupportPreview = useMemo(() => {
    return language === "html" || language === "markdown";
  }, [language]);
  const toolResult = (() => {
    if (!isWriteFile) {
      return undefined;
    }
    const url = new URL(filepathFromProps);
    const toolCallId = url.searchParams.get("tool_call_id");
    if (!toolCallId) {
      return undefined;
    }
    return findToolCallResult(toolCallId, thread.messages);
  })();
  const artifactViewState = getArtifactViewState({
    filepath: filepathFromProps,
    isSupportPreview,
    toolResult,
  });
  const { content, url } = useArtifactContent({
    threadId,
    filepath: filepathFromProps,
    enabled: isCodeFile && !isWriteFile,
  });
  const previewPathFromList = useMemo(() => {
    return findPreviewPdfPath(filepath, artifacts);
  }, [artifacts, filepath]);
  const guessedPreviewPaths = useMemo(() => {
    return guessPreviewPdfPaths(filepath);
  }, [filepath]);
  const [resolvedPreviewPath, setResolvedPreviewPath] = useState<string | null>(
    null,
  );
  const [isResolvingPreview, setIsResolvingPreview] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }

    setResolvedPreviewPath(null);
    if (isCodeFile || isWriteFile) {
      setIsResolvingPreview(false);
      return;
    }

    const previewCandidates = Array.from(
      new Set(
        [previewPathFromList, ...guessedPreviewPaths].filter(
          (candidate): candidate is string => Boolean(candidate),
        ),
      ),
    );
    if (previewCandidates.length === 0) {
      setIsResolvingPreview(false);
      return;
    }

    const controller = new AbortController();
    setIsResolvingPreview(true);
    void (async () => {
      for (const delayMs of PRESENTATION_PREVIEW_PROBE_DELAYS_MS) {
        if (delayMs > 0) {
          await new Promise((resolve) => window.setTimeout(resolve, delayMs));
          if (controller.signal.aborted) {
            return;
          }
        }

        for (const candidate of previewCandidates) {
          try {
            const response = await fetch(
              urlOfArtifact({
                filepath: candidate,
                threadId,
                isMock,
              }),
              {
                method: "HEAD",
                cache: "no-store",
                credentials: "same-origin",
                signal: controller.signal,
              },
            );
            if (response.ok) {
              setResolvedPreviewPath(candidate);
              setIsResolvingPreview(false);
              return;
            }
          } catch {
            if (controller.signal.aborted) {
              return;
            }
          }
        }
      }

      // Keep the PPTX download fallback when no sibling preview exists.
      setIsResolvingPreview(false);
    })();

    return () => controller.abort();
  }, [
    guessedPreviewPaths,
    isCodeFile,
    isMock,
    isWriteFile,
    open,
    filepath,
    previewPathFromList,
    threadId,
  ]);

  const browserPreviewPath = resolvedPreviewPath ?? filepath;
  const browserPreviewUrl = useMemo(() => {
    if (browserPreviewPath.toLowerCase().endsWith(".pdf")) {
      return urlOfPdfArtifactPreview({
        filepath: browserPreviewPath,
        threadId,
        isMock,
      });
    }
    return urlOfArtifact({
      filepath: browserPreviewPath,
      threadId,
      isMock,
    });
  }, [browserPreviewPath, isMock, threadId]);
  const canPreviewInBrowser = useMemo(() => {
    return canBrowserPreviewFile(browserPreviewPath);
  }, [browserPreviewPath]);

  const displayContent = content ?? "";
  const isWritingFile = isWriteFile && toolResult === undefined;
  const visibleContent = useThrottledValue(
    displayContent,
    isWritingFile ? WRITE_FILE_PREVIEW_REFRESH_INTERVAL_MS : 0,
    filepathFromProps,
  );

  const [viewMode, setViewMode] = useState<"code" | "preview">(
    artifactViewState.initialViewMode,
  );
  const [isInstalling, setIsInstalling] = useState(false);
  useEffect(() => {
    setViewMode(artifactViewState.initialViewMode);
  }, [artifactViewState.initialViewMode]);

  const handleInstallSkill = useCallback(async () => {
    if (isInstalling) return;

    setIsInstalling(true);
    try {
      const result = await installSkill({
        thread_id: threadId,
        path: filepath,
      });
      if (result.success) {
        toast.success(result.message);
      } else {
        toast.error(result.message ?? "Failed to install skill");
      }
    } catch (error) {
      console.error("Failed to install skill:", error);
      if (error instanceof SkillRequestError && error.isAdminRequired) {
        toast.error(t.settings.skills.installAdminRequired);
      } else {
        toast.error("Failed to install skill");
      }
    } finally {
      setIsInstalling(false);
    }
  }, [threadId, filepath, isInstalling, t]);
  return (
    <Artifact className={cn(className)}>
      <ArtifactHeader className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_auto] gap-2 px-2">
        <div className="flex min-w-0 items-center gap-2 overflow-hidden">
          <ArtifactTitle className="min-w-0 overflow-hidden">
            {isWriteFile ? (
              <div className="truncate px-2" title={getFileName(filepath)}>
                {getFileName(filepath)}
              </div>
            ) : (
              <Select value={filepath} onValueChange={select}>
                <SelectTrigger
                  className="w-full max-w-full min-w-0 border-none bg-transparent! shadow-none select-none focus:outline-0 active:outline-0"
                  title={getFileName(filepath)}
                >
                  <SelectValue placeholder="Select a file" />
                </SelectTrigger>
                <SelectContent className="select-none">
                  <SelectGroup>
                    {artifactOptions.map((option) => (
                      <SelectItem key={option} value={option}>
                        {getFileName(option)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            )}
          </ArtifactTitle>
        </div>
        <div className="flex shrink-0 items-center justify-center">
          {artifactViewState.canPreview && (
            <ToggleGroup
              className="mx-auto"
              type="single"
              variant="outline"
              size="sm"
              value={viewMode}
              onValueChange={(value) => {
                if (value) {
                  setViewMode(value as "code" | "preview");
                }
              }}
            >
              <ToggleGroupItem value="code">
                <Code2Icon />
              </ToggleGroupItem>
              <ToggleGroupItem value="preview">
                <EyeIcon />
              </ToggleGroupItem>
            </ToggleGroup>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ArtifactActions className="shrink-0">
            {!isWriteFile && filepath.endsWith(".skill") && isAdmin && (
              <Tooltip content={t.toolCalls.skillInstallTooltip}>
                <ArtifactAction
                  icon={isInstalling ? LoaderIcon : PackageIcon}
                  label={t.common.install}
                  tooltip={t.common.install}
                  disabled={
                    isInstalling ||
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
                  }
                  onClick={handleInstallSkill}
                />
              </Tooltip>
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={SquareArrowOutUpRightIcon}
                label={t.common.openInNewWindow}
                tooltip={t.common.openInNewWindow}
                onClick={() => {
                  const w = window.open(
                    browserPreviewUrl,
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            {isCodeFile && (
              <ArtifactAction
                icon={CopyIcon}
                label={t.clipboard.copyToClipboard}
                disabled={!content}
                onClick={() => {
                  void (async () => {
                    const didCopy = await writeTextToClipboard(
                      visibleContent ?? "",
                    );
                    if (!didCopy) {
                      toast.error(t.clipboard.failedToCopyToClipboard);
                      return;
                    }

                    toast.success(t.clipboard.copiedToClipboard);
                  })().catch(() => {
                    toast.error(t.clipboard.failedToCopyToClipboard);
                  });
                }}
                tooltip={t.clipboard.copyToClipboard}
              />
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={DownloadIcon}
                label={t.common.download}
                tooltip={t.common.download}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({
                      filepath,
                      threadId,
                      download: true,
                      isMock,
                    }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            <ArtifactAction
              icon={XIcon}
              label={t.common.close}
              onClick={() => setOpen(false)}
              tooltip={t.common.close}
            />
          </ArtifactActions>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="p-0">
        {artifactViewState.canPreview &&
          viewMode === "preview" &&
          (language === "markdown" || language === "html") && (
            <ArtifactFilePreview
              content={visibleContent}
              language={language ?? "text"}
              scrollKey={filepathFromProps}
              url={url}
            />
          )}
        {isCodeFile && viewMode === "code" && (
          <CodeEditor
            className="size-full resize-none rounded-none border-none"
            value={visibleContent ?? ""}
            readonly
          />
        )}
        {!isCodeFile && isResolvingPreview && (
          <div
            className="text-muted-foreground flex size-full items-center justify-center gap-2 text-sm"
            role="status"
          >
            <LoaderIcon className="size-4 animate-spin" />
            正在准备预览…
          </div>
        )}
        {!isCodeFile && !isResolvingPreview && canPreviewInBrowser && (
          <iframe
            className="size-full"
            src={browserPreviewUrl}
            referrerPolicy="no-referrer"
            sandbox={
              browserPreviewPath.toLocaleLowerCase().endsWith(".pdf")
                ? ""
                : undefined
            }
            title="Artifact preview"
          />
        )}
        {!isCodeFile && !isResolvingPreview && !canPreviewInBrowser && (
          <ArtifactDownloadFallback
            filepath={filepath}
            threadId={threadId}
            isMock={isMock}
          />
        )}
      </ArtifactContent>
    </Artifact>
  );
}

function ArtifactDownloadFallback({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  const filename = getFileName(filepath);
  const fileType = getFileExtensionDisplayName(filepath);

  return (
    <div className="flex size-full items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <div className="text-muted-foreground">
          {getFileIcon(filepath, "size-12")}
        </div>
        <div className="space-y-1">
          <div className="font-medium break-all">{filename}</div>
          <div className="text-muted-foreground text-sm">{fileType} file</div>
        </div>
        <p className="text-muted-foreground text-sm">
          This file type cannot be previewed in the browser.
        </p>
        <Button asChild>
          <a
            href={urlOfArtifact({
              filepath,
              threadId,
              download: true,
              isMock,
            })}
            target="_blank"
            rel="noopener noreferrer"
          >
            <DownloadIcon className="size-4" />
            Download
          </a>
        </Button>
      </div>
    </div>
  );
}

export function ArtifactFilePreview({
  content,
  language,
  scrollKey,
  url,
}: {
  content: string;
  language: string;
  scrollKey: string;
  url?: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const scrollPositionRef = useRef({ x: 0, y: 0 });
  const scrollMessageKey = useMemo(
    () => createHtmlPreviewScrollKey(scrollKey),
    [scrollKey],
  );
  const [htmlPreviewUrl, setHtmlPreviewUrl] = useState<string>();
  const citationSources = useMemo(
    () =>
      language === "markdown" ? extractCitationSources(content ?? "") : [],
    [content, language],
  );

  useEffect(() => {
    scrollPositionRef.current = { x: 0, y: 0 };
  }, [scrollMessageKey]);

  useEffect(() => {
    if (language !== "html") {
      return;
    }

    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }
      if (!isArtifactScrollMessage(event.data, scrollMessageKey)) {
        return;
      }

      if (event.data.type === "save") {
        const x = scrollCoordinate(event.data.x);
        const y = scrollCoordinate(event.data.y);
        if (x !== undefined && y !== undefined) {
          scrollPositionRef.current = { x, y };
        }
        return;
      }

      iframeRef.current?.contentWindow?.postMessage(
        {
          source: HTML_PREVIEW_SCROLL_MESSAGE_SOURCE,
          key: scrollMessageKey,
          type: "restore",
          ...scrollPositionRef.current,
        },
        "*",
      );
    };

    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [language, scrollMessageKey]);

  useEffect(() => {
    if (language !== "html") {
      setHtmlPreviewUrl(undefined);
      return;
    }

    const previewContent = appendHtmlPreviewScrollRestoration(
      appendHtmlPreviewBaseHref(content ?? "", url),
      scrollKey,
    );
    const blob = new Blob([previewContent], {
      type: "text/html;charset=utf-8",
    });
    const objectUrl = URL.createObjectURL(blob);
    setHtmlPreviewUrl(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [content, language, scrollKey, url]);

  if (language === "markdown") {
    return (
      <div className="size-full overflow-auto px-4 py-3">
        <SafeStreamdown
          className="min-w-0"
          {...artifactMarkdownPlugins}
          components={{ a: ArtifactLink }}
        >
          {content ?? ""}
        </SafeStreamdown>
        <CitationSourcesPanel sources={citationSources} className="mb-4" />
      </div>
    );
  }
  if (language === "html") {
    return (
      <iframe
        ref={iframeRef}
        className="size-full"
        title="Artifact preview"
        sandbox="allow-scripts allow-forms"
        src={htmlPreviewUrl}
      />
    );
  }
  return null;
}

function isArtifactScrollMessage(
  data: unknown,
  key: string,
): data is {
  type: "save" | "restore-request";
  x?: unknown;
  y?: unknown;
} {
  return (
    typeof data === "object" &&
    data !== null &&
    "source" in data &&
    data.source === HTML_PREVIEW_SCROLL_MESSAGE_SOURCE &&
    "key" in data &&
    data.key === key &&
    "type" in data &&
    (data.type === "save" || data.type === "restore-request")
  );
}

function scrollCoordinate(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function useThrottledValue(
  value: string,
  intervalMs: number,
  resetKey: string,
) {
  const [throttledValue, setThrottledValue] = useState(value);
  const latestValueRef = useRef(value);
  const lastFlushAtRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetKeyRef = useRef(resetKey);

  useEffect(() => {
    latestValueRef.current = value;

    if (resetKeyRef.current !== resetKey) {
      resetKeyRef.current = resetKey;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = Date.now();
      setThrottledValue(value);
      return;
    }

    if (intervalMs <= 0) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = Date.now();
      setThrottledValue(value);
      return;
    }

    const now = Date.now();
    const elapsed = now - lastFlushAtRef.current;
    if (lastFlushAtRef.current === 0 || elapsed >= intervalMs) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = now;
      setThrottledValue(value);
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      lastFlushAtRef.current = Date.now();
      setThrottledValue(latestValueRef.current);
    }, intervalMs - elapsed);
  }, [intervalMs, resetKey, value]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return intervalMs <= 0 || resetKeyRef.current !== resetKey
    ? value
    : throttledValue;
}

import { FilesIcon, XIcon } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { getAPIClient } from "@/core/api/api-client";
import { extractArtifactsFromStateSnapshot } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { env } from "@/env";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";

import {
  ArtifactFileDetail,
  ArtifactFileList,
  useArtifacts,
} from "../artifacts";
import { BrowserViewPanel, useMaybeBrowserView } from "../browser-view";
import { useThread } from "../messages/context";
import { SidecarPanel, useMaybeSidecar } from "../sidecar";

const RIGHT_PANEL_ANIMATION_MS = 280;

type RightPanelKind = "sidecar" | "artifacts" | "browser";

type ChatBoxProps = {
  children: React.ReactNode;
  threadId: string;
  canHydratePersistedArtifacts?: boolean;
  browserEnabled?: boolean;
};

const ChatBox: React.FC<ChatBoxProps> = ({
  children,
  threadId,
  canHydratePersistedArtifacts = true,
  browserEnabled = true,
}) => {
  const { thread } = useThread();
  const isMobile = useIsMobile();
  const pathname = usePathname();
  const isNewChatPath = pathname.endsWith("/new");
  const { t } = useI18n();
  const threadIdRef = useRef(threadId);

  const {
    artifacts,
    open: artifactsOpen,
    setOpen: setArtifactsOpen,
    setArtifacts,
    deselect,
    selectedArtifact,
  } = useArtifacts();
  const sidecar = useMaybeSidecar();
  const sidecarOpen = sidecar?.open ?? false;
  const browserView = useMaybeBrowserView();
  const browserViewOpen = browserEnabled && (browserView?.open ?? false);
  const artifactHydrationThreadIdRef = useRef<string | null>(null);

  // The dynamic chat route reuses this provider while its thread id changes
  // from the welcome id to the authoritative runtime id (and when navigating
  // between conversations). Clear an opened file surface before the browser
  // paints the new thread. Doing this in the asynchronous hydration effect
  // leaves one stale frame in which the old drawer covers the new transcript.
  useLayoutEffect(() => {
    if (threadIdRef.current === threadId) {
      return;
    }
    threadIdRef.current = threadId;
    artifactHydrationThreadIdRef.current = null;
    deselect();
    setArtifacts([]);
  }, [deselect, setArtifacts, threadId]);

  useEffect(() => {
    const threadArtifacts = Array.isArray(thread.values.artifacts)
      ? thread.values.artifacts
      : undefined;

    if (threadArtifacts?.length) {
      setArtifacts(threadArtifacts);
    }
  }, [setArtifacts, thread.values.artifacts]);

  useEffect(() => {
    // `/chats/new` owns a client-generated UUID before the first run creates
    // the thread. Never probe that provisional id: the canonical response is
    // necessarily 404 and was polluting the browser console on every refresh.
    // For a persisted thread, make at most one fallback read per mount/thread;
    // normal stream updates above remain authoritative afterwards.
    if (
      !canHydratePersistedArtifacts ||
      isNewChatPath ||
      !threadId ||
      artifactHydrationThreadIdRef.current === threadId ||
      (Array.isArray(thread.values.artifacts) &&
        thread.values.artifacts.length > 0)
    ) {
      return;
    }

    artifactHydrationThreadIdRef.current = threadId;
    let disposed = false;
    void getAPIClient(undefined, threadId)
      .threads.getState(threadId)
      .then((state) => {
        const persistedArtifacts = extractArtifactsFromStateSnapshot(state);
        if (!disposed && persistedArtifacts.length > 0) {
          setArtifacts(persistedArtifacts);
        }
      })
      .catch(() => {
        // The normal stream remains authoritative; a transient state read
        // must not break the conversation surface.
      });

    return () => {
      disposed = true;
    };
  }, [
    canHydratePersistedArtifacts,
    isNewChatPath,
    setArtifacts,
    thread.values.artifacts,
    threadId,
  ]);

  const artifactPanelOpen = useMemo(() => {
    if (sidecarOpen) {
      return false;
    }
    if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
      return artifactsOpen && artifacts?.length > 0;
    }
    return artifactsOpen;
  }, [artifactsOpen, artifacts, sidecarOpen]);

  const activeRightPanel: RightPanelKind | null = sidecarOpen
    ? "sidecar"
    : browserViewOpen
      ? "browser"
      : artifactPanelOpen
        ? "artifacts"
        : null;
  const rightPanelOpen = activeRightPanel !== null;
  const artifactsOverlayOpen = activeRightPanel === "artifacts";
  const [renderedRightPanel, setRenderedRightPanel] =
    useState<RightPanelKind | null>(activeRightPanel);

  const resizableIdBase = useMemo(() => {
    return pathname.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  }, [pathname]);

  useEffect(() => {
    if (activeRightPanel) {
      setRenderedRightPanel(activeRightPanel);
      return;
    }

    const timeout = window.setTimeout(() => {
      setRenderedRightPanel(null);
    }, RIGHT_PANEL_ANIMATION_MS);

    return () => {
      window.clearTimeout(timeout);
    };
  }, [activeRightPanel]);

  useEffect(() => {
    if (sidecarOpen && artifactsOpen) {
      setArtifactsOpen(false);
    }
  }, [artifactsOpen, setArtifactsOpen, sidecarOpen]);

  useEffect(() => {
    if (!browserEnabled && browserView?.open) {
      browserView.close();
    }
  }, [browserEnabled, browserView]);

  const rightPanelContent = useMemo(() => {
    if (renderedRightPanel === "browser") {
      return <BrowserViewPanel className="size-full" threadId={threadId} />;
    }
    if (renderedRightPanel === "sidecar") {
      return <SidecarPanel />;
    }
    if (renderedRightPanel === "artifacts" && selectedArtifact) {
      return (
        <ArtifactFileDetail
          className="size-full"
          filepath={selectedArtifact}
          threadId={threadId}
        />
      );
    }
    if (renderedRightPanel === "artifacts") {
      return (
        <div className="relative flex size-full justify-center">
          <div className="absolute top-1 right-1 z-30">
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() => {
                setArtifactsOpen(false);
              }}
            >
              <XIcon />
            </Button>
          </div>
          {artifacts.length === 0 ? (
            <ConversationEmptyState
              icon={<FilesIcon />}
              title={t.common.noArtifactSelected}
              description={t.common.selectArtifactToView}
            />
          ) : (
            <div className="flex size-full max-w-(--container-width-sm) flex-col justify-center p-4 pt-8">
              <header className="shrink-0">
                <h2 className="text-lg font-medium">{t.common.artifacts}</h2>
              </header>
              <main className="min-h-0 grow overflow-y-auto">
                <ArtifactFileList
                  className="max-w-(--container-width-sm) p-4 pt-12"
                  files={artifacts}
                  threadId={threadId}
                />
              </main>
            </div>
          )}
        </div>
      );
    }
    return null;
  }, [
    renderedRightPanel,
    selectedArtifact,
    threadId,
    artifacts,
    setArtifactsOpen,
    t.common.artifacts,
    t.common.noArtifactSelected,
    t.common.selectArtifactToView,
  ]);

  if (isMobile) {
    return (
      <>
        <div className="cnooc-chat-box relative size-full min-w-0">
          {children}
        </div>
        <Sheet
          open={rightPanelOpen}
          onOpenChange={(open) => {
            if (open) {
              return;
            }
            if (sidecarOpen) {
              sidecar?.close();
            }
            if (artifactsOpen) {
              setArtifactsOpen(false);
            }
            if (browserView?.open) {
              browserView.close();
            }
          }}
        >
          <SheetContent
            className="mobile-fullscreen-surface app-safe-area w-screen max-w-none gap-0 p-0 sm:max-w-md [&>button]:hidden"
            side="right"
          >
            <SheetHeader className="sr-only">
              <SheetTitle>
                {renderedRightPanel === "sidecar"
                  ? "Sidecar"
                  : renderedRightPanel === "browser"
                    ? t.browser.title
                    : t.common.artifacts}
              </SheetTitle>
              <SheetDescription>
                {t.browser.sidePanelDescription}
              </SheetDescription>
            </SheetHeader>
            <div className="app-scroll-region min-h-0 flex-1 p-2 pt-10 sm:p-3 sm:pt-10">
              {rightPanelContent}
            </div>
          </SheetContent>
        </Sheet>
      </>
    );
  }

  return (
    <div
      id={`${resizableIdBase}-panels`}
      className={cn(
        "cnooc-chat-box [container-type:inline-size] size-full min-h-0",
        artifactsOverlayOpen
          ? "relative"
          : "grid transition-[grid-template-columns] duration-[280ms] ease-out motion-reduce:transition-none",
        !artifactsOverlayOpen &&
          (rightPanelOpen
            ? "grid-cols-[minmax(0,1fr)_1px_minmax(0,40%)]"
            : "grid-cols-[minmax(0,1fr)_0px_0px]"),
      )}
    >
      <div className="relative min-h-0 min-w-0" id="chat">
        {children}
      </div>
      {!artifactsOverlayOpen && (
        <>
          <div
            id={`${resizableIdBase}-separator`}
            aria-hidden="true"
            className={cn(
              "bg-border opacity-33 transition-opacity duration-200 ease-out motion-reduce:transition-none",
              !rightPanelOpen && "pointer-events-none opacity-0",
            )}
          />
          <aside
            aria-hidden={!rightPanelOpen}
            className={cn(
              "min-h-0 min-w-0 overflow-hidden transition-opacity duration-[280ms] ease-out motion-reduce:transition-none",
              !rightPanelOpen && "pointer-events-none opacity-0",
            )}
            id="artifacts"
          >
            <div
              className={cn(
                "h-full w-full transition-opacity duration-[280ms] ease-out motion-reduce:transition-none",
                renderedRightPanel === "sidecar" ? "p-0" : "p-4",
                rightPanelOpen ? "opacity-100" : "opacity-0",
              )}
            >
              {rightPanelContent}
            </div>
          </aside>
        </>
      )}
      {artifactsOverlayOpen && (
        <aside
          aria-label={t.common.artifacts}
          className="bg-background absolute top-12 right-0 bottom-0 z-50 w-[min(560px,calc(100%-1rem))] min-w-0 overflow-hidden border-l p-3 shadow-2xl transition-transform duration-[280ms] ease-out motion-reduce:transition-none"
          data-testid="artifact-overlay-panel"
          id="artifacts"
        >
          {rightPanelContent}
        </aside>
      )}
    </div>
  );
};

export { ChatBox };

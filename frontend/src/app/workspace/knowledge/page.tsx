"use client";

import { PanelLeftIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useSidebar } from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { WEKNORA_EMBED_PATH } from "@/core/weknora/embed";
import {
  initializeWeKnoraMobileNavigation,
  observeWeKnoraModal,
} from "@/core/weknora/mobile-embed";

export default function KnowledgePage() {
  const { locale } = useI18n();
  const [nativeModalOpen, setNativeModalOpen] = useState(false);
  const cleanupFrame = useRef<(() => void) | undefined>(undefined);
  const initializeFrame = useCallback((frame: HTMLIFrameElement) => {
    cleanupFrame.current?.();
    const cleanupNavigation = initializeWeKnoraMobileNavigation(frame);
    const cleanupModal = observeWeKnoraModal(frame, setNativeModalOpen);
    cleanupFrame.current = () => {
      cleanupNavigation();
      cleanupModal();
    };
  }, []);
  const { setOpen, setOpenMobile } = useSidebar();
  const didCollapseSidebar = useRef(false);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [sidebarInitialized, setSidebarInitialized] = useState(false);

  useEffect(() => {
    if (!didCollapseSidebar.current) {
      didCollapseSidebar.current = true;
      setOpen(false);
      setOpenMobile(false);
      setSidebarInitialized(true);
    }
    // A cached native frame can finish loading before React hydrates onLoad.
    if (frameRef.current) initializeFrame(frameRef.current);
    return () => cleanupFrame.current?.();
  }, [setOpen, setOpenMobile, initializeFrame]);

  return (
    <main className="cnooc-knowledge-page app-scroll-region relative h-full min-h-0 min-w-0 overflow-hidden bg-white">
      {sidebarInitialized && !nativeModalOpen && (
        <Button
          type="button"
          variant="outline"
          onClick={() => setOpenMobile(true)}
          className="cnooc-knowledge-navigation absolute right-3 bottom-3 z-30 h-11 gap-2 rounded-full border bg-white px-4 opacity-100 shadow-md md:hidden"
        >
          <PanelLeftIcon className="size-4" aria-hidden="true" />
          {locale.startsWith("zh") ? "平台导航" : "Platform menu"}
        </Button>
      )}
      <iframe
        ref={frameRef}
        data-testid="weknora-workspace-frame"
        title="企业知识工作台"
        src={WEKNORA_EMBED_PATH}
        onLoad={(event) => initializeFrame(event.currentTarget)}
        className="block h-full min-h-0 w-full min-w-0 border-0 bg-white"
        allow="clipboard-read; clipboard-write; microphone"
        referrerPolicy="same-origin"
      />
    </main>
  );
}

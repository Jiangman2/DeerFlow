"use client";

import { useEffect, useRef, useState } from "react";

import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { WEKNORA_EMBED_PATH } from "@/core/weknora/embed";

export default function KnowledgePage() {
  const { setOpen, setOpenMobile } = useSidebar();
  const didCollapseSidebar = useRef(false);
  const [sidebarInitialized, setSidebarInitialized] = useState(false);

  useEffect(() => {
    if (didCollapseSidebar.current) {
      return;
    }
    didCollapseSidebar.current = true;
    setOpen(false);
    setOpenMobile(false);
    setSidebarInitialized(true);
  }, [setOpen, setOpenMobile]);

  return (
    <main className="app-scroll-region relative h-full min-h-0 min-w-0 overflow-hidden bg-white">
      {sidebarInitialized && (
        <SidebarTrigger className="absolute top-2 right-2 z-30 size-10 border bg-white/95 opacity-100 shadow-md backdrop-blur md:hidden" />
      )}
      <iframe
        data-testid="weknora-workspace-frame"
        title="企业知识工作台"
        src={WEKNORA_EMBED_PATH}
        className="block h-full min-h-0 w-full min-w-0 border-0 bg-white"
        allow="clipboard-read; clipboard-write; microphone"
        referrerPolicy="same-origin"
      />
    </main>
  );
}

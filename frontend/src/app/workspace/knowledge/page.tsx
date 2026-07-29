"use client";

import { useEffect } from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { WEKNORA_EMBED_PATH } from "@/core/weknora/embed";

export default function KnowledgePage() {
  const { setOpen, setOpenMobile } = useSidebar();

  useEffect(() => {
    setOpen(false);
    setOpenMobile(false);
  }, [setOpen, setOpenMobile]);

  return (
    <main className="h-full min-h-0 bg-white">
      <iframe
        data-testid="weknora-workspace-frame"
        title="企业知识工作台"
        src={WEKNORA_EMBED_PATH}
        className="block h-full w-full border-0 bg-white"
        allow="clipboard-read; clipboard-write; microphone"
        referrerPolicy="same-origin"
      />
    </main>
  );
}

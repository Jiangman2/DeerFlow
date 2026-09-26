"use client";

import { LoaderCircle } from "lucide-react";
import { Suspense } from "react";

import { ChatProviders } from "@/components/workspace/chats/chat-providers";
import { useI18n } from "@/core/i18n/hooks";

export default function AgentChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { t } = useI18n();
  return (
    <ChatProviders>
      <Suspense
        fallback={
          <div
            role="status"
            aria-label={t.common.loading}
            className="flex size-full items-center justify-center"
          >
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          </div>
        }
      >
        {children}
      </Suspense>
    </ChatProviders>
  );
}

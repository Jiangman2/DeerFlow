import { ChatProviders } from "@/components/workspace/chats/chat-providers";
import { NonConfidentialNotice } from "@/components/workspace/non-confidential-notice";
import { isStaticWebsiteOnly } from "@/core/static-mode";
import { DEMO_THREAD_IDS } from "@/core/threads/static-demo";

export function generateStaticParams() {
  if (!isStaticWebsiteOnly()) {
    return [];
  }
  return DEMO_THREAD_IDS.map((thread_id) => ({ thread_id }));
}

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ChatProviders>
      <div className="flex min-h-0 flex-1 flex-col">
        <NonConfidentialNotice className="hidden md:block" />
        <div className="relative min-h-0 flex-1">{children}</div>
      </div>
    </ChatProviders>
  );
}

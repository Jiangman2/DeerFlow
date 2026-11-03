import type { Metadata } from "next";

import { getI18n } from "@/core/i18n/server";

import ChatHistoryPage from "./history-page";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getI18n();
  return { title: `${t.pages.chats} - ${t.pages.appName}` };
}

export default function ChatsPage() {
  return <ChatHistoryPage />;
}

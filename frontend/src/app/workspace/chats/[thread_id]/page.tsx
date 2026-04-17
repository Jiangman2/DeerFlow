"use client";

import dynamic from "next/dynamic";

import { useThreadChat } from "@/components/workspace/chats";

const ChatPage = dynamic(() => import("./page-client"), { ssr: false });

export default function ChatPageRoute() {
  const { threadId } = useThreadChat();
  return <ChatPage key={threadId} />;
}

"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";

import { useThreadChat } from "@/components/workspace/chats";

const AgentChatPage = dynamic(() => import("./page-client"), { ssr: false });

export default function AgentChatPageRoute() {
  const { agent_name } = useParams<{ agent_name: string }>();
  const { threadId } = useThreadChat();
  return <AgentChatPage key={`${agent_name}/${threadId}`} />;
}

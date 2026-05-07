"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";

import { useThreadChat } from "@/components/workspace/chats";

const AgentChatPage = dynamic(() => import("./page-client"), { ssr: false });

export default function AgentChatPageRoute() {
  const { agent_name } = useParams<{ agent_name: string }>();
  const decodedAgentName = (() => {
    try {
      return decodeURIComponent(agent_name);
    } catch {
      return agent_name;
    }
  })();
  const { threadId } = useThreadChat();
  return <AgentChatPage key={`${decodedAgentName}/${threadId}`} />;
}

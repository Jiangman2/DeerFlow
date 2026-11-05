"use client";

import { type Agent } from "@/core/agents";
import { cn } from "@/lib/utils";

import { DigitalEmployeeAvatar } from "./agents/digital-employee-avatar";

export function AgentWelcome({
  className,
  agent,
  agentName,
}: {
  className?: string;
  agent: Agent | null | undefined;
  agentName: string;
}) {
  const configuredName = agent?.display_name?.trim();
  const displayName = configuredName?.length
    ? configuredName
    : (agent?.name ?? agentName);
  const description = agent?.description;

  return (
    <div
      className={cn(
        "cnooc-agent-welcome mx-auto flex w-full flex-col items-center justify-center gap-1 px-5 py-2 text-center sm:gap-2 sm:px-8 sm:py-4",
        className,
      )}
    >
      <DigitalEmployeeAvatar
        agent={agent ?? { name: agentName }}
        className="cnooc-agent-welcome-icon size-16 sm:size-20"
      />
      <h1 className="cnooc-agent-welcome-title max-w-full text-[1.35rem] leading-tight font-semibold sm:text-2xl">
        {displayName}
      </h1>
      {description && (
        <p className="cnooc-agent-welcome-description text-muted-foreground line-clamp-1 max-w-[min(22rem,85vw)] text-xs sm:line-clamp-2 sm:max-w-sm sm:text-sm">
          {description}
        </p>
      )}
    </div>
  );
}

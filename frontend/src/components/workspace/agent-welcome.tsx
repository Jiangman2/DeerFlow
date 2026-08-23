"use client";

import { type Agent } from "@/core/agents";
import { cn } from "@/lib/utils";

import { AgentNameIcon } from "./agents/agent-name-icon";

export function AgentWelcome({
  className,
  agent,
  agentName,
}: {
  className?: string;
  agent: Agent | null | undefined;
  agentName: string;
}) {
  const displayName = agent?.name ?? agentName;
  const description = agent?.description;

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center gap-1 px-5 py-2 text-center sm:gap-2 sm:px-8 sm:py-4",
        className,
      )}
    >
      <AgentNameIcon
        className="h-9 w-9 sm:h-12 sm:w-12"
        iconClassName="h-5 w-5 sm:h-6 sm:w-6"
      />
      <div className="max-w-full truncate text-[1.35rem] leading-tight font-bold sm:text-2xl">
        {displayName}
      </div>
      {description && (
        <p className="text-muted-foreground line-clamp-1 max-w-[min(22rem,85vw)] text-xs sm:line-clamp-2 sm:max-w-sm sm:text-sm">
          {description}
        </p>
      )}
    </div>
  );
}

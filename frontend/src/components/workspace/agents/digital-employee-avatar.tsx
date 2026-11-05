"use client";

import { BotIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  getDefaultExpertAvatar,
  getExpertAvatarById,
  getStoredExpertAvatarId,
} from "@/core/agents/expert-avatars";
import type { Agent } from "@/core/agents/types";
import { cn } from "@/lib/utils";

type EmployeeIdentity = Pick<
  Agent,
  "name" | "display_name" | "avatar_id" | "avatar_url" | "external_url"
>;

/** One portrait contract for the directory, welcome and conversation header. */
export function DigitalEmployeeAvatar({
  agent,
  className,
}: {
  agent: EmployeeIdentity;
  className?: string;
}) {
  const [legacy, setLegacy] = useState<{ name: string; id?: string }>();
  useEffect(() => {
    setLegacy({ name: agent.name, id: getStoredExpertAvatarId(agent.name) });
  }, [agent.name]);
  const preset =
    getExpertAvatarById(
      agent.avatar_id ?? (legacy?.name === agent.name ? legacy.id : undefined),
    ) ?? getDefaultExpertAvatar(agent);
  const configuredName = agent.display_name?.trim();
  const displayName = configuredName?.length ? configuredName : agent.name;

  return (
    <Avatar
      className={cn(
        "cnooc-employee-avatar shrink-0 rounded-full border-2 border-white bg-blue-50 shadow-sm ring-1 ring-blue-100 dark:border-slate-800 dark:bg-slate-800 dark:ring-slate-700",
        className,
      )}
    >
      <AvatarImage
        src={agent.avatar_url ?? preset.src}
        alt={displayName}
        className="rounded-full object-cover"
      />
      <AvatarFallback className="rounded-full bg-blue-50 text-blue-700 dark:bg-slate-800 dark:text-blue-200">
        <BotIcon aria-hidden="true" className="size-1/2" />
      </AvatarFallback>
    </Avatar>
  );
}

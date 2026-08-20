import { BotIcon } from "lucide-react";

import { cn } from "@/lib/utils";

type AgentNameIconProps = {
  className?: string;
  iconClassName?: string;
};

export function AgentNameIcon({
  className,
  iconClassName,
}: AgentNameIconProps) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "relative mx-auto flex h-14 w-14 items-center justify-center overflow-hidden rounded-full",
        className,
      )}
    >
      <span className="bg-primary absolute inset-0 opacity-10" />
      <BotIcon
        className={cn("text-primary relative z-10 h-7 w-7", iconClassName)}
      />
    </div>
  );
}

import { BotIcon } from "lucide-react";

export function AgentNameIcon() {
  return (
    <div
      aria-hidden="true"
      className="relative mx-auto flex h-14 w-14 items-center justify-center overflow-hidden rounded-full"
    >
      <span className="bg-primary absolute inset-0 opacity-10" />
      <BotIcon className="text-primary relative z-10 h-7 w-7" />
    </div>
  );
}

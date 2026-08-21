"use client";

import Image from "next/image";

import { cn } from "@/lib/utils";

export function CnoocAssistantAvatar({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("cnooc-assistant-avatar relative sm:hidden", className)}
      data-testid="cnooc-assistant-avatar"
    >
      <Image
        alt=""
        className="cnooc-assistant-avatar-image size-full object-contain"
        height={140}
        priority
        src="/images/cnooc-assistant-avatar.png"
        unoptimized
        width={140}
      />
    </div>
  );
}

"use client";

import { useSearchParams } from "next/navigation";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { CnoocAssistantAvatar } from "./cnooc-assistant-avatar";

function WelcomeDescription({ children }: { children: string }) {
  return (
    <p className="max-w-full text-wrap break-words whitespace-pre-line">
      {children}
    </p>
  );
}

export function Welcome({
  className,
}: {
  className?: string;
  mode?: "ultra" | "pro" | "thinking" | "flash";
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const isSkillMode = searchParams.get("mode") === "skill";

  if (isSkillMode) {
    return (
      <section
        className={cn(
          "cnooc-welcome-skill mx-auto flex w-full max-w-full flex-col items-center justify-center gap-2 px-4 py-4 text-center sm:px-8",
          className,
        )}
        data-testid="welcome-panel"
      >
        <h1 className="max-w-full text-2xl font-bold">
          {t.welcome.createYourOwnSkill}
        </h1>
        <div className="text-muted-foreground max-w-full text-sm">
          <WelcomeDescription>
            {t.welcome.createYourOwnSkillDescription}
          </WelcomeDescription>
        </div>
      </section>
    );
  }

  return (
    <section
      className={cn("cnooc-welcome-panel", className)}
      data-testid="welcome-panel"
    >
      <div className="cnooc-desktop-welcome-copy">
        <h1>海能通用智能体</h1>
        <p>您好！我是您的智能助手，有什么可以帮助您？</p>
      </div>

      <div className="cnooc-mobile-welcome-card">
        <CnoocAssistantAvatar className="cnooc-welcome-mascot" />
        <div className="cnooc-mobile-welcome-copy">
          <p className="cnooc-mobile-welcome-eyebrow">hi,您好呀~</p>
          <h1>
            你好，我是您的
            <strong>海油智能助手</strong>
          </h1>
          <p className="cnooc-mobile-welcome-description">
            很高兴遇到你，我将时刻为您答疑解惑，助力高效工作。
          </p>
        </div>
        <span aria-hidden="true" className="cnooc-mobile-welcome-wave" />
      </div>
    </section>
  );
}

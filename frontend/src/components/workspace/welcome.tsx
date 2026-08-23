"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { AuroraText } from "../ui/aurora-text";

import { CnoocAssistantAvatar } from "./cnooc-assistant-avatar";

let waved = false;

function WelcomeDescription({ children }: { children: string }) {
  return (
    <p className="max-w-full break-words whitespace-pre-line sm:whitespace-pre">
      {children}
    </p>
  );
}

export function Welcome({
  className,
  mode,
}: {
  className?: string;
  mode?: "ultra" | "pro" | "thinking" | "flash";
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const isUltra = useMemo(() => mode === "ultra", [mode]);
  const colors = useMemo(() => {
    if (isUltra) {
      return ["#c9e8f8", "#70bce1", "#1f70c1"];
    }
    return ["var(--color-foreground)"];
  }, [isUltra]);
  useEffect(() => {
    waved = true;
  }, []);
  return (
    <div
      className={cn(
        "mx-auto flex w-full max-w-full flex-col items-center justify-center gap-1 px-5 py-2 text-center sm:w-[min(56rem,calc(100vw-6rem))] sm:max-w-none sm:gap-2 sm:px-8 sm:py-4",
        className,
      )}
      data-testid="welcome-panel"
    >
      {searchParams.get("mode") !== "skill" && (
        <CnoocAssistantAvatar className="mb-1 size-[4.75rem]" />
      )}
      <div className="max-w-full text-[1.35rem] leading-tight font-bold sm:text-2xl">
        {searchParams.get("mode") === "skill" ? (
          `✨ ${t.welcome.createYourOwnSkill} ✨`
        ) : (
          <div className="flex max-w-full flex-wrap items-center justify-center gap-2">
            <div
              className={cn(
                "hidden sm:inline-block",
                !waved ? "animate-wave" : "",
              )}
            >
              {isUltra ? "🚀" : "👋"}
            </div>
            <AuroraText colors={colors}>{t.welcome.greeting}</AuroraText>
          </div>
        )}
      </div>
      {searchParams.get("mode") === "skill" ? (
        <div className="text-muted-foreground max-w-full text-sm">
          <WelcomeDescription>
            {t.welcome.createYourOwnSkillDescription}
          </WelcomeDescription>
        </div>
      ) : (
        <>
          <div className="text-muted-foreground max-w-full text-sm sm:hidden">
            <WelcomeDescription>
              {t.welcome.mobileDescription}
            </WelcomeDescription>
          </div>
          <div className="text-muted-foreground hidden max-w-full text-sm sm:block">
            <WelcomeDescription>{t.welcome.description}</WelcomeDescription>
          </div>
        </>
      )}
    </div>
  );
}

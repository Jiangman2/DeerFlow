"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { AuroraText } from "../ui/aurora-text";

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
      return ["#005bac", "#00a3e0", "#f2b705"];
    }
    return ["var(--color-foreground)"];
  }, [isUltra]);

  const skillMode = searchParams.get("mode") === "skill";
  const description = skillMode
    ? t.welcome.createYourOwnSkillDescription
    : t.welcome.description;

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center gap-2 px-4 py-4 text-center sm:px-8",
        className,
      )}
    >
      <div className="text-2xl font-bold">
        {skillMode ? (
          <span>{t.welcome.createYourOwnSkill}</span>
        ) : (
          <div className="flex items-center">
            <AuroraText colors={colors}>{t.welcome.greeting}</AuroraText>
          </div>
        )}
      </div>
      <div className="text-muted-foreground max-w-3xl text-sm leading-6">
        {description.includes("\n") ? (
          <pre className="font-sans whitespace-pre-wrap">{description}</pre>
        ) : (
          <p>{description}</p>
        )}
      </div>
    </div>
  );
}

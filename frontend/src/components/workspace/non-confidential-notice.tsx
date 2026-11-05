"use client";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function NonConfidentialNotice({ className }: { className?: string }) {
  const { t } = useI18n();

  return (
    <p
      className={cn(
        "m-0 shrink-0 px-4 py-2 text-center text-[13px] leading-5 font-semibold break-words text-[#d60000] sm:text-sm sm:leading-6 lg:text-base dark:text-[#ff6b6b]",
        className,
      )}
      data-testid="non-confidential-notice"
    >
      {t.common.nonConfidentialNotice}
    </p>
  );
}

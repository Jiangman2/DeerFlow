"use client";

import { useId } from "react";

import { Toggle } from "@/components/ui/toggle";
import { pptConfirmationMessages } from "@/core/i18n/ppt-confirmation";

export function PptConfirmationOptIn({
  locale,
  checked,
  disabled,
  hasQuotes,
  onCheckedChange,
}: {
  locale: string;
  checked: boolean;
  disabled: boolean;
  hasQuotes: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  const descriptionId = useId();
  const copy = pptConfirmationMessages(locale);
  return (
    <div
      className="flex min-w-0 flex-col items-start gap-2 px-3 py-2"
      data-testid="ppt-confirmation-opt-in"
    >
      <Toggle
        type="button"
        variant="outline"
        size="sm"
        pressed={checked}
        disabled={disabled || (hasQuotes && !checked)}
        onPressedChange={onCheckedChange}
        aria-describedby={descriptionId}
        className="h-auto max-w-full py-2 text-left whitespace-normal"
      >
        {copy.enable}
      </Toggle>
      <p
        id={descriptionId}
        className="text-muted-foreground text-xs leading-relaxed"
      >
        {hasQuotes ? copy.quoted : checked ? copy.automatic : copy.manual}
      </p>
    </div>
  );
}

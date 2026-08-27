"use client";

import {
  CheckIcon,
  ChevronDownIcon,
  GraduationCapIcon,
  LightbulbIcon,
  RocketIcon,
  ZapIcon,
} from "lucide-react";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { type AgentMode, ModeHoverGuide } from "./mode-hover-guide";

const MODES: AgentMode[] = ["flash", "thinking", "pro", "ultra"];

export function resolveAgentMode(
  mode: AgentMode | undefined,
  supportsThinking: boolean,
): AgentMode {
  if (!supportsThinking && mode !== "flash") {
    return "flash";
  }
  if (mode) {
    return mode;
  }
  return supportsThinking ? "pro" : "flash";
}

export function reasoningEffortForMode(mode: AgentMode) {
  switch (mode) {
    case "ultra":
      return "high" as const;
    case "pro":
      return "medium" as const;
    case "thinking":
      return "low" as const;
    case "flash":
      return "minimal" as const;
  }
}

function ModeIcon({
  mode,
  className,
}: {
  mode: AgentMode;
  className?: string;
}) {
  const iconClassName = cn("size-4", className);
  switch (mode) {
    case "flash":
      return <ZapIcon className={iconClassName} />;
    case "thinking":
      return <LightbulbIcon className={iconClassName} />;
    case "pro":
      return <GraduationCapIcon className={iconClassName} />;
    case "ultra":
      return <RocketIcon className={cn(iconClassName, "text-[#dabb5e]")} />;
  }
}

export function ModeSelector({
  mode,
  supportsThinking,
  disabled = false,
  variant = "composer",
  className,
  onSelect,
}: {
  mode: AgentMode | undefined;
  supportsThinking: boolean;
  disabled?: boolean;
  variant?: "composer" | "mobile-header";
  className?: string;
  onSelect: (mode: AgentMode) => void;
}) {
  const { t } = useI18n();
  const selectedMode = resolveAgentMode(mode, supportsThinking);
  const labels = {
    flash: t.inputBox.flashMode,
    thinking: t.inputBox.reasoningMode,
    pro: t.inputBox.proMode,
    ultra: t.inputBox.ultraMode,
  };
  const descriptions = {
    flash: t.inputBox.flashModeDescription,
    thinking: t.inputBox.reasoningModeDescription,
    pro: t.inputBox.proModeDescription,
    ultra: t.inputBox.ultraModeDescription,
  };
  const availableModes = supportsThinking
    ? MODES
    : MODES.filter((item) => item === "flash");

  const trigger =
    variant === "mobile-header" ? (
      <Button
        aria-label={`${t.inputBox.mode}: ${labels[selectedMode]}`}
        className={cn(
          "bg-background hover:bg-muted h-9 max-w-[9.5rem] gap-1.5 rounded-full border px-3 text-sm font-medium shadow-sm backdrop-blur md:hidden",
          className,
        )}
        data-testid="mobile-mode-selector"
        disabled={disabled}
        size="sm"
        variant="ghost"
      >
        <span
          className={cn("truncate", selectedMode === "ultra" && "golden-text")}
        >
          {labels[selectedMode]}
        </span>
        <ChevronDownIcon className="text-muted-foreground size-3.5 shrink-0" />
      </Button>
    ) : (
      <PromptInputButton
        className={cn("max-w-28 gap-1! px-2! sm:max-w-none", className)}
        disabled={disabled}
      >
        <ModeIcon mode={selectedMode} className="size-3" />
        <span
          className={cn(
            "truncate text-xs font-normal",
            selectedMode === "ultra" && "golden-text",
          )}
        >
          {labels[selectedMode]}
        </span>
        <ChevronDownIcon className="cnooc-mode-chevron text-muted-foreground size-3.5 shrink-0" />
      </PromptInputButton>
    );

  const dropdownTrigger = (
    <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
  );

  return (
    <DropdownMenu>
      {variant === "mobile-header" ? (
        dropdownTrigger
      ) : (
        <ModeHoverGuide mode={selectedMode}>{dropdownTrigger}</ModeHoverGuide>
      )}
      <DropdownMenuContent
        align="start"
        className={cn(
          "w-80",
          variant === "mobile-header" &&
            "max-w-[calc(100vw-2rem)] rounded-3xl p-2 shadow-xl",
        )}
        sideOffset={variant === "mobile-header" ? 10 : 4}
      >
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-muted-foreground text-xs">
            {t.inputBox.mode}
          </DropdownMenuLabel>
          {availableModes.map((item) => (
            <DropdownMenuItem
              className={cn(
                "items-start gap-3 rounded-2xl py-3",
                selectedMode === item
                  ? "text-accent-foreground bg-accent"
                  : "text-muted-foreground",
              )}
              key={item}
              onSelect={() => onSelect(item)}
            >
              <ModeIcon
                className={cn(
                  "mt-0.5 shrink-0",
                  selectedMode === item && "text-accent-foreground",
                )}
                mode={item}
              />
              <span className="min-w-0 flex-1">
                <span
                  className={cn(
                    "block font-semibold",
                    item === "ultra" && selectedMode === item && "golden-text",
                  )}
                >
                  {labels[item]}
                </span>
                <span className="mt-1 block text-xs leading-5 opacity-80">
                  {descriptions[item]}
                </span>
              </span>
              <span className="mt-0.5 size-4 shrink-0">
                {selectedMode === item && <CheckIcon className="size-4" />}
              </span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

"use client";

import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { DigitalEmployeeCategory } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { BusinessDomainBadge } from "./business-domain-badge";

interface BusinessDomainPickerProps {
  categories: DigitalEmployeeCategory[];
  value: string;
  onValueChange: (value: string) => void;
  className?: string;
}

export function BusinessDomainPicker({
  categories,
  value,
  onValueChange,
  className,
}: BusinessDomainPickerProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const selectedCategory = categories.find((category) => category.id === value);

  function selectCategory(nextValue: string) {
    onValueChange(nextValue);
    setOpen(false);
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          data-testid="business-domain-picker-trigger"
          aria-label={t.agents.category}
          aria-expanded={open}
          className={cn(
            "h-9 w-full justify-between gap-2 px-3 font-normal",
            className,
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate">
              {selectedCategory?.name ?? t.agents.category}
            </span>
            {selectedCategory ? (
              <BusinessDomainBadge
                domainGroup={selectedCategory.domain_group}
              />
            ) : null}
          </span>
          <ChevronDownIcon
            aria-hidden="true"
            className={cn(
              "text-muted-foreground size-4 shrink-0 transition-transform",
              open && "rotate-180",
            )}
          />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        data-testid="business-domain-picker-content"
        align="start"
        sideOffset={6}
        collisionPadding={16}
        className="max-h-none w-[min(24rem,calc(100vw-2rem))] overflow-hidden p-0 shadow-lg"
      >
        <ScrollArea
          data-testid="business-domain-picker-list"
          className="h-[min(18rem,calc(100dvh-14rem))] min-h-32"
        >
          <DropdownMenuRadioGroup
            value={value}
            onValueChange={selectCategory}
            className="grid gap-0.5 p-1.5"
          >
            {categories.map((category) => (
              <DropdownMenuRadioItem
                key={category.id}
                value={category.id}
                title={category.description}
                data-testid="business-domain-option"
                className={cn(
                  "min-h-9 min-w-0 gap-2 py-1.5 pr-2 pl-7 text-xs sm:text-sm",
                  "data-[state=checked]:bg-accent data-[state=checked]:text-accent-foreground data-[state=checked]:font-medium",
                )}
              >
                <span className="min-w-0 flex-1 truncate">{category.name}</span>
                <BusinessDomainBadge domainGroup={category.domain_group} />
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

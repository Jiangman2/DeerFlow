"use client";

import { CheckIcon, SearchIcon } from "lucide-react";
import { useRef, useState, type KeyboardEvent, type ReactElement } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import type { Model } from "@/core/models";

export function ChatModelPicker({
  children,
  models,
  selectedName,
  open,
  onOpenChange,
  onSelect,
}: {
  children: ReactElement;
  models: Model[];
  selectedName?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (name: string) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const selectedModel = models.find((model) => model.name === selectedName);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredModels = models.filter((model) =>
    [model.display_name, model.name, model.model].some((value) =>
      value?.toLocaleLowerCase().includes(normalizedQuery),
    ),
  );

  const handleOpenChange = (nextOpen: boolean) => {
    setQuery("");
    onOpenChange(nextOpen);
  };
  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") return;
    // Keep Radix typeahead from consuming search input. Arrow/Tab moves from
    // the search field into the native, keyboard-navigable menu item group.
    event.stopPropagation();
    if (["ArrowDown", "ArrowUp", "Tab"].includes(event.key)) {
      event.preventDefault();
      if (event.key === "Tab" && event.shiftKey) {
        handleOpenChange(false);
        return;
      }
      const items = listRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitemradio"]',
      );
      const index = event.key === "ArrowUp" ? (items?.length ?? 0) - 1 : 0;
      items?.[index]?.focus();
    }
  };

  return (
    <DropdownMenu modal={false} open={open} onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        aria-label={t.inputBox.modelPickerTitle}
        className="cnooc-model-menu"
        collisionPadding={12}
        data-testid="chat-model-menu"
        side="top"
        sideOffset={8}
      >
        <div className="cnooc-model-menu-search">
          <SearchIcon aria-hidden="true" />
          <Input
            aria-label={t.inputBox.searchModels}
            autoComplete="off"
            placeholder={t.inputBox.searchModels}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
        </div>
        {selectedModel && (
          <DropdownMenuLabel className="cnooc-model-menu-current">
            {t.inputBox.currentModel} / {selectedModel.display_name}
          </DropdownMenuLabel>
        )}
        <div className="cnooc-model-menu-list" ref={listRef}>
          <DropdownMenuGroup>
            {filteredModels.map((model) => (
              <DropdownMenuItem
                aria-checked={model.name === selectedName}
                className="cnooc-model-menu-item"
                data-current={model.name === selectedName}
                key={model.name}
                role="menuitemradio"
                textValue={model.display_name}
                title={model.display_name}
                onSelect={() => {
                  onSelect(model.name);
                  handleOpenChange(false);
                }}
              >
                <span className="min-w-0 flex-1 truncate">
                  {model.display_name}
                </span>
                {model.name === selectedName && (
                  <CheckIcon aria-hidden="true" />
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
          {filteredModels.length === 0 && (
            <p className="cnooc-model-menu-empty" role="status">
              {t.inputBox.noMatchingModels}
            </p>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

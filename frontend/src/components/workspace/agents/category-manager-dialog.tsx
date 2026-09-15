"use client";

import { ArrowDownIcon, ArrowUpIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useReplaceDigitalEmployeeCategories } from "@/core/agents";
import type { DigitalEmployeeCategory } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

interface CategoryManagerDialogProps {
  categories: DigitalEmployeeCategory[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function categoryId() {
  return `category-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 6)}`;
}

export function CategoryManagerDialog({
  categories,
  open,
  onOpenChange,
}: CategoryManagerDialogProps) {
  const { t } = useI18n();
  const replaceCategories = useReplaceDigitalEmployeeCategories();
  const [items, setItems] = useState<DigitalEmployeeCategory[]>([]);

  useEffect(() => {
    if (open) setItems(categories.map((item) => ({ ...item })));
  }, [categories, open]);

  function updateItem(id: string, patch: Partial<DigitalEmployeeCategory>) {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  }

  function move(index: number, direction: -1 | 1) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= items.length) return;
    setItems((current) => {
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex]!, next[index]!];
      return next;
    });
  }

  async function save() {
    const normalized = items
      .map((item, index) => ({
        ...item,
        name: item.name.trim(),
        description: item.description.trim(),
        sort_order: index * 10,
      }))
      .filter((item) => item.name);
    if (normalized.length === 0) {
      toast.error(t.agents.categoryRequired);
      return;
    }
    try {
      await replaceCategories.mutateAsync(normalized);
      toast.success(t.agents.categoriesSaved);
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="app-scroll-region max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t.agents.manageCategories}</DialogTitle>
          <DialogDescription>{t.agents.manageCategoriesHint}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          {items.map((item, index) => (
            <div
              key={item.id}
              className="bg-muted/25 grid grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)_auto] items-center gap-2 rounded-lg border p-2"
            >
              <Input
                aria-label={t.agents.categoryName}
                maxLength={32}
                value={item.name}
                onChange={(event) =>
                  updateItem(item.id, { name: event.target.value })
                }
              />
              <Input
                aria-label={t.agents.categoryDescription}
                maxLength={160}
                value={item.description}
                onChange={(event) =>
                  updateItem(item.id, { description: event.target.value })
                }
              />
              <div className="flex items-center">
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t.agents.moveUp}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUpIcon />
                </Button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t.agents.moveDown}
                  disabled={index === items.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDownIcon />
                </Button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="text-destructive"
                  aria-label={t.common.delete}
                  onClick={() =>
                    setItems((current) =>
                      current.filter((candidate) => candidate.id !== item.id),
                    )
                  }
                >
                  <Trash2Icon />
                </Button>
              </div>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            className="mt-1 w-full"
            onClick={() =>
              setItems((current) => [
                ...current,
                {
                  id: categoryId(),
                  name: t.agents.newCategoryName,
                  description: "",
                  sort_order: current.length * 10,
                },
              ])
            }
          >
            <PlusIcon />
            {t.agents.addCategory}
          </Button>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button
            onClick={() => void save()}
            disabled={replaceCategories.isPending}
          >
            {replaceCategories.isPending ? t.common.loading : t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

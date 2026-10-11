"use client";

import { useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import { squareKey, useUpdateSquareSettings } from "@/core/agents";
import type { Agent, DigitalEmployeeCategory } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

import { BusinessDomainPicker } from "./business-domain-picker";
import { TagEditor } from "./tag-editor";

export function SquareSettingsDialog({
  agent,
  categories,
  mode,
  onClose,
}: {
  agent: Agent;
  categories: DigitalEmployeeCategory[];
  mode: "edit" | "sort";
  onClose: () => void;
}) {
  const { t } = useI18n();
  const update = useUpdateSquareSettings();
  const [name, setName] = useState(agent.display_name ?? agent.name);
  const [description, setDescription] = useState(agent.description);
  const [category, setCategory] = useState(
    agent.category_id ?? "comprehensive-management",
  );
  const [tags, setTags] = useState(agent.tags ?? []);
  const [url, setUrl] = useState(agent.external_url ?? "");
  const [order, setOrder] = useState(
    agent.square_sort_order == null ? "" : String(agent.square_sort_order),
  );

  async function save() {
    try {
      await update.mutateAsync({
        key: squareKey(agent),
        version: agent.square_version ?? 0,
        ...(mode === "sort"
          ? { sort_order: order === "" ? null : Number(order) }
          : {
              display_name: name.trim(),
              description,
              category_id: category,
              tags,
              ...(agent.external_url ? { external_url: url.trim() } : {}),
            }),
      });
      toast.success(t.agents.updateSuccess);
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !update.isPending) onClose();
      }}
    >
      <DialogContent className="max-h-[85dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {mode === "sort" ? t.agents.squareSort : t.agents.squareEdit}
          </DialogTitle>
          <DialogDescription>
            {mode === "sort"
              ? t.agents.squareSortHint
              : t.agents.squareEditHint}
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
          className="grid gap-4"
        >
          {mode === "sort" ? (
            <label className="grid gap-2 text-sm">
              {t.agents.squareSortValue}
              <Input
                type="number"
                min={0}
                max={1_000_000}
                step={1}
                value={order}
                onChange={(event) => setOrder(event.target.value)}
              />
            </label>
          ) : (
            <>
              <label className="grid gap-2 text-sm">
                {t.agents.displayName}
                <Input
                  value={name}
                  maxLength={80}
                  required
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label className="grid gap-2 text-sm">
                {t.agents.descriptionLabel}
                <Textarea
                  value={description}
                  maxLength={500}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <BusinessDomainPicker
                categories={categories}
                value={category}
                onValueChange={setCategory}
              />
              <TagEditor
                value={tags}
                onChange={setTags}
                suggestionContext={{
                  name: agent.name,
                  displayName: name,
                  description,
                  categoryId: category,
                }}
              />
              {agent.external_url ? (
                <label className="grid gap-2 text-sm">
                  {t.agents.squareExternalUrl}
                  <Input
                    type="url"
                    required
                    value={url}
                    onChange={(event) => setUrl(event.target.value)}
                  />
                </label>
              ) : null}
            </>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={update.isPending}
              onClick={onClose}
            >
              {t.common.cancel}
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? t.agents.saving : t.agents.save}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

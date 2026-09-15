"use client";

import { CameraIcon, CheckCircleIcon, UploadIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  EXPERT_AVATAR_OPTIONS,
  getDefaultExpertAvatar,
  getExpertAvatarById,
  useDeleteAgentAvatar,
  useUpdateAgent,
  useUploadAgentAvatar,
} from "@/core/agents";
import type { Agent, DigitalEmployeeCategory } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { TagEditor } from "./tag-editor";

interface DigitalEmployeeSettingsDialogProps {
  agent: Agent | null;
  categories: DigitalEmployeeCategory[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const UNCATEGORIZED_VALUE = "__uncategorized__";

export function DigitalEmployeeSettingsDialog({
  agent,
  categories,
  open,
  onOpenChange,
}: DigitalEmployeeSettingsDialogProps) {
  const { t } = useI18n();
  const updateAgent = useUpdateAgent();
  const uploadAvatar = useUploadAgentAvatar();
  const deleteAvatar = useDeleteAgentAvatar();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [categoryId, setCategoryId] = useState(UNCATEGORIZED_VALUE);
  const [tags, setTags] = useState<string[]>([]);
  const [avatarId, setAvatarId] = useState(EXPERT_AVATAR_OPTIONS[0]!.id);
  const [customFile, setCustomFile] = useState<File | null>(null);
  const [removeCustomAvatar, setRemoveCustomAvatar] = useState(false);

  useEffect(() => {
    if (!agent || !open) return;
    setDisplayName(agent.display_name ?? agent.name);
    setDescription(agent.description ?? "");
    setCategoryId(agent.category_id ?? UNCATEGORIZED_VALUE);
    setTags(agent.tags ?? []);
    setAvatarId(agent.avatar_id ?? getDefaultExpertAvatar(agent).id);
    setCustomFile(null);
    setRemoveCustomAvatar(false);
  }, [agent, open]);

  const customPreview = useMemo(
    () => (customFile ? URL.createObjectURL(customFile) : null),
    [customFile],
  );
  useEffect(
    () => () => {
      if (customPreview) URL.revokeObjectURL(customPreview);
    },
    [customPreview],
  );

  if (!agent) return null;
  const selectedPreset =
    getExpertAvatarById(avatarId) ?? getDefaultExpertAvatar(agent);
  const previewSrc =
    customPreview ??
    (!removeCustomAvatar ? agent.avatar_url : null) ??
    selectedPreset.src;
  const isSaving =
    updateAgent.isPending || uploadAvatar.isPending || deleteAvatar.isPending;

  async function handleSave() {
    if (!agent) return;
    try {
      await updateAgent.mutateAsync({
        name: agent.name,
        request: {
          display_name: displayName.trim() || agent.name,
          description: description.trim(),
          category_id: categoryId === UNCATEGORIZED_VALUE ? null : categoryId,
          tags,
          avatar_id: avatarId,
        },
      });
      if (customFile) {
        await uploadAvatar.mutateAsync({ name: agent.name, file: customFile });
      } else if (removeCustomAvatar && agent.avatar_url) {
        await deleteAvatar.mutateAsync(agent.name);
      }
      toast.success(t.agents.updateSuccess);
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  function handleCustomFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast.error(t.agents.avatarTooLarge);
      return;
    }
    setCustomFile(file);
    setRemoveCustomAvatar(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="app-scroll-region max-h-[92dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t.agents.settingsTitle}</DialogTitle>
          <DialogDescription>{t.agents.settingsDescription}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 py-2">
          <section className="grid gap-3">
            <div className="flex items-center gap-4">
              <Avatar className="size-20 rounded-full border-2 border-white shadow ring-1 ring-blue-100">
                <AvatarImage
                  src={previewSrc}
                  className="rounded-full object-cover"
                />
                <AvatarFallback className="rounded-full">
                  <CameraIcon />
                </AvatarFallback>
              </Avatar>
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  {t.agents.avatarPickerLabel}
                </p>
                <p className="text-muted-foreground mt-1 text-xs">
                  {t.agents.avatarUploadHint}
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="sr-only"
                  onChange={(event) =>
                    handleCustomFile(event.target.files?.[0])
                  }
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <UploadIcon />
                  {t.agents.uploadAvatar}
                </Button>
              </div>
            </div>
            <div className="grid grid-cols-6 gap-2 sm:grid-cols-12">
              {EXPERT_AVATAR_OPTIONS.map((option) => {
                const selected =
                  !customFile && removeCustomAvatar && avatarId === option.id;
                const configured =
                  !customFile && !agent.avatar_url && avatarId === option.id;
                return (
                  <button
                    type="button"
                    key={option.id}
                    title={option.label}
                    className={cn(
                      "relative aspect-square overflow-hidden rounded-full border-2 bg-blue-50 transition hover:-translate-y-0.5 hover:border-blue-300",
                      (selected || configured) &&
                        "border-primary ring-primary/20 ring-2",
                    )}
                    onClick={() => {
                      setAvatarId(option.id);
                      setCustomFile(null);
                      setRemoveCustomAvatar(true);
                    }}
                  >
                    <img
                      src={option.src}
                      alt={option.label}
                      className="size-full rounded-full object-cover"
                    />
                    {selected || configured ? (
                      <CheckCircleIcon className="text-primary-foreground fill-primary absolute right-0 bottom-0 size-4" />
                    ) : null}
                  </button>
                );
              })}
            </div>
          </section>

          <div className="grid gap-2">
            <label
              className="text-sm font-medium"
              htmlFor="employee-display-name"
            >
              {t.agents.displayName}
            </label>
            <Input
              id="employee-display-name"
              maxLength={80}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
            <div className="text-muted-foreground flex justify-between gap-3 text-xs">
              <span>
                {t.agents.stableName}: {agent.name}
              </span>
              <span>{displayName.length}/80</span>
            </div>
          </div>

          <div className="grid gap-2">
            <label
              className="text-sm font-medium"
              htmlFor="employee-description"
            >
              {t.agents.descriptionLabel}
            </label>
            <Textarea
              id="employee-description"
              maxLength={500}
              rows={3}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <p className="text-muted-foreground text-right text-xs">
              {description.length}/500
            </p>
          </div>

          <div className="grid gap-2">
            <label className="text-sm font-medium">{t.agents.category}</label>
            <Select value={categoryId} onValueChange={setCategoryId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t.agents.uncategorized} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNCATEGORIZED_VALUE}>
                  {t.agents.uncategorized}
                </SelectItem>
                {categories.map((category) => (
                  <SelectItem key={category.id} value={category.id}>
                    {category.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <label className="text-sm font-medium">{t.agents.tags}</label>
            <TagEditor
              value={tags}
              onChange={setTags}
              suggestionContext={{
                name: agent.name,
                displayName,
                description,
                categoryId:
                  categoryId === UNCATEGORIZED_VALUE ? null : categoryId,
              }}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button onClick={() => void handleSave()} disabled={isSaving}>
            {isSaving ? t.common.loading : t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

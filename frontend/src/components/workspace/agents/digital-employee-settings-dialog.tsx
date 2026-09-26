"use client";

import { CameraIcon, CheckCircleIcon, UploadIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Textarea } from "@/components/ui/textarea";
import {
  EXPERT_AVATAR_OPTIONS,
  AgentDraftError,
  getAgent,
  getDefaultExpertAvatar,
  getExpertAvatarById,
  useDeleteAgentAvatar,
  useUpdateAgent,
  useUploadAgentAvatar,
} from "@/core/agents";
import type { Agent, DigitalEmployeeCategory } from "@/core/agents";
import { invalidAgentKnowledgeSelection } from "@/core/agents/knowledge-selection";
import type { AgentKnowledgeScope } from "@/core/agents/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { AgentKnowledgeSelection } from "./agent-knowledge-selection";
import { AgentSkillSelection } from "./agent-skill-selection";
import { BusinessDomainPicker } from "./business-domain-picker";
import { TagEditor } from "./tag-editor";

interface DigitalEmployeeSettingsDialogProps {
  agent: Agent | null;
  categories: DigitalEmployeeCategory[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DEFAULT_CATEGORY_ID = "comprehensive-management";

export function DigitalEmployeeSettingsDialog(props: DigitalEmployeeSettingsDialogProps) {
  // A new edit session always reads the private detail endpoint, never the
  // directory-card cache. Unmount on close so delayed reads cannot seed a
  // later editing session or replace input on a background list refresh.
  if (!props.open || !props.agent) return null;
  return <EmployeeDraftLoader key={props.agent.name} {...props} agent={props.agent} />;
}

function EmployeeDraftLoader(props: DigitalEmployeeSettingsDialogProps & { agent: Agent }) {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState<Agent | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const name = props.agent.name;
  useEffect(() => {
    let current = true;
    setLoadError(false);
    void getAgent(name, "personal").then((detail) => {
      if (!current) return;
      if (!detail.draft_revision) {
        setLoadError(true);
        return;
      }
      setSnapshot(detail);
    }).catch(() => { if (current) setLoadError(true); });
    return () => { current = false; };
  }, [name, attempt]);
  if (snapshot) return <DigitalEmployeeEditor {...props} agent={snapshot} />;
  return <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t.agents.settingsTitle}</DialogTitle>
        <DialogDescription>{t.agents.settingsDescription}</DialogDescription>
      </DialogHeader>
      {loadError ? <Alert variant="destructive"><AlertDescription>{t.agents.draftLoadFailed}</AlertDescription></Alert> : <p role="status">{t.common.loading}</p>}
      <DialogFooter>
        <Button variant="outline" onClick={() => props.onOpenChange(false)}>{t.common.cancel}</Button>
        {loadError && <Button onClick={() => setAttempt((value) => value + 1)}>{t.agents.draftRetry}</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}

function DigitalEmployeeEditor({
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
  const [categoryId, setCategoryId] = useState(DEFAULT_CATEGORY_ID);
  const [tags, setTags] = useState<string[]>([]);
  const [avatarId, setAvatarId] = useState(EXPERT_AVATAR_OPTIONS[0]!.id);
  const [customFile, setCustomFile] = useState<File | null>(null);
  const [removeCustomAvatar, setRemoveCustomAvatar] = useState(false);
  const [selectedSkills, setSelectedSkills] = useState<string[] | null>(null);
  const [skillsChanged, setSkillsChanged] = useState(false);
  const [knowledgeScope, setKnowledgeScope] =
    useState<AgentKnowledgeScope | null>(null);
  const [knowledgeChanged, setKnowledgeChanged] = useState(false);
  const [draftRevision, setDraftRevision] = useState(agent?.draft_revision);
  const [conflict, setConflict] = useState(false);
  const [partialSave, setPartialSave] = useState(false);
  const saving = useRef(false);

  useEffect(() => {
    if (!agent || !open) return;
    setDisplayName(agent.display_name ?? agent.name);
    setDescription(agent.description ?? "");
    setCategoryId(agent.category_id ?? DEFAULT_CATEGORY_ID);
    setTags(agent.tags ?? []);
    setAvatarId(agent.avatar_id ?? getDefaultExpertAvatar(agent).id);
    setCustomFile(null);
    setRemoveCustomAvatar(false);
    setSelectedSkills(agent.skills ?? null);
    setSkillsChanged(false);
    setKnowledgeScope(agent.knowledge_scope ?? null);
    setKnowledgeChanged(false);
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
    if (!agent || !draftRevision || conflict || saving.current) return;
    if (invalidAgentKnowledgeSelection(knowledgeScope)) return;
    saving.current = true;
    let detailsSaved = false;
    try {
      const saved = await updateAgent.mutateAsync({
        name: agent.name,
        request: {
          expected_draft_revision: draftRevision,
          display_name: displayName.trim() || agent.name,
          description: description.trim(),
          category_id: categoryId,
          tags,
          avatar_id: avatarId,
          ...(skillsChanged ? { skills: selectedSkills } : {}),
          ...(knowledgeChanged ? { knowledge_scope: knowledgeScope } : {}),
        },
      });
      // If the separate avatar transfer fails, retry against the saved draft
      // instead of replaying a stale revision and losing the local form.
      setDraftRevision(saved.draft_revision);
      detailsSaved = true;
      if (customFile) {
        const withAvatar = await uploadAvatar.mutateAsync({ name: agent.name, file: customFile, expectedDraftRevision: saved.draft_revision ?? "" });
        setDraftRevision(withAvatar.draft_revision);
      } else if (removeCustomAvatar && agent.avatar_url) {
        const withoutAvatar = await deleteAvatar.mutateAsync({ name: agent.name, expectedDraftRevision: saved.draft_revision ?? "" });
        setDraftRevision(withoutAvatar.draft_revision);
      }
      toast.success(t.agents.updateSuccess);
      onOpenChange(false);
    } catch (error) {
      if (detailsSaved) setPartialSave(true);
      if (error instanceof AgentDraftError && [409, 428].includes(error.status)) {
        setConflict(true);
        return;
      }
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      saving.current = false;
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

        {conflict && <Alert variant="destructive"><AlertDescription>{t.agents.draftConflict}</AlertDescription></Alert>}
        {partialSave && <Alert><AlertDescription>{t.agents.avatarPartialSave}</AlertDescription></Alert>}
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
            <BusinessDomainPicker
              categories={categories}
              value={categoryId}
              onValueChange={setCategoryId}
            />
          </div>

          <AgentSkillSelection
            value={selectedSkills}
            enabled={open}
            disabled={isSaving}
            onChange={(selection) => {
              setSelectedSkills(selection);
              setSkillsChanged(true);
            }}
          />
          <AgentKnowledgeSelection
            value={knowledgeScope}
            disabled={isSaving}
            onChange={(selection) => {
              setKnowledgeScope(selection);
              setKnowledgeChanged(true);
            }}
          />

          <div className="grid gap-2">
            <label className="text-sm font-medium">{t.agents.tags}</label>
            <TagEditor
              value={tags}
              onChange={setTags}
              suggestionContext={{
                name: agent.name,
                displayName,
                description,
                categoryId,
              }}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={
              isSaving || conflict || !draftRevision || invalidAgentKnowledgeSelection(knowledgeScope)
            }
          >
            {isSaving ? t.common.loading : t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

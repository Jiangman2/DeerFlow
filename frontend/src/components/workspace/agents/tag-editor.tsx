"use client";

import {
  CheckIcon,
  PencilIcon,
  PlusIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  MAX_DIGITAL_EMPLOYEE_TAG_LENGTH,
  MAX_DIGITAL_EMPLOYEE_TAGS,
  digitalEmployeeTagLength,
  normalizeDigitalEmployeeTags,
  suggestDigitalEmployeeTags,
} from "@/core/agents/digital-employee-tags";
import { useI18n } from "@/core/i18n/hooks";

interface TagEditorProps {
  value: string[];
  onChange: (tags: string[]) => void;
  suggestionContext: {
    name?: string;
    displayName?: string;
    description?: string;
    categoryId?: string | null;
  };
}

export function TagEditor({
  value,
  onChange,
  suggestionContext,
}: TagEditorProps) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const atLimit =
    value.length >= MAX_DIGITAL_EMPLOYEE_TAGS && editingIndex === null;
  const suggested = useMemo(
    () => suggestDigitalEmployeeTags(suggestionContext),
    [suggestionContext],
  );

  function commit(raw = draft) {
    const tag = raw.trim();
    if (!tag) return;
    if (digitalEmployeeTagLength(tag) > MAX_DIGITAL_EMPLOYEE_TAG_LENGTH) {
      setError(t.agents.tagTooLong);
      return;
    }
    if (
      value.some(
        (existing, index) =>
          index !== editingIndex &&
          existing.toLocaleLowerCase() === tag.toLocaleLowerCase(),
      )
    ) {
      setError(t.agents.tagDuplicate);
      return;
    }
    const next = [...value];
    if (editingIndex === null) next.push(tag);
    else next[editingIndex] = tag;
    const normalized = normalizeDigitalEmployeeTags(next);
    onChange(normalized);
    setDraft("");
    setEditingIndex(null);
    setError("");
  }

  function addSuggested() {
    onChange(normalizeDigitalEmployeeTags([...value, ...suggested]));
    setEditingIndex(null);
    setDraft("");
    setError("");
  }

  return (
    <div className="grid gap-2" data-testid="digital-employee-tag-editor">
      <div className="flex flex-wrap gap-1.5">
        {value.map((tag, index) => (
          <span
            className="text-primary flex max-w-full items-center gap-0.5 rounded-full border border-blue-100 bg-blue-50 py-1 pr-1 pl-2.5 text-xs dark:border-slate-700 dark:bg-slate-800"
            key={`${tag}-${index}`}
          >
            <span className="max-w-40 truncate">{tag}</span>
            <button
              aria-label={`${t.agents.editTag} ${tag}`}
              className="rounded-full p-1 hover:bg-blue-100 dark:hover:bg-slate-700"
              type="button"
              onClick={() => {
                setDraft(tag);
                setEditingIndex(index);
                setError("");
              }}
            >
              <PencilIcon className="size-3" />
            </button>
            <button
              aria-label={`${t.agents.deleteTag} ${tag}`}
              className="rounded-full p-1 hover:bg-red-50 hover:text-red-700 dark:hover:bg-red-950"
              type="button"
              onClick={() =>
                onChange(value.filter((_, candidate) => candidate !== index))
              }
            >
              <XIcon className="size-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <Input
            aria-label={t.agents.tagInputLabel}
            disabled={atLimit}
            placeholder={
              atLimit ? t.agents.tagLimitReached : t.agents.tagsPlaceholder
            }
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              setError("");
            }}
            onPaste={(event) => {
              const pasted = event.clipboardData.getData("text");
              if (!/[,，\n]/.test(pasted)) return;
              event.preventDefault();
              const candidates = pasted
                .split(/[,，\n]/)
                .map((tag) => tag.trim())
                .filter(Boolean);
              if (
                candidates.some(
                  (tag) =>
                    digitalEmployeeTagLength(tag) >
                    MAX_DIGITAL_EMPLOYEE_TAG_LENGTH,
                )
              ) {
                setError(t.agents.tagTooLong);
                return;
              }
              onChange(normalizeDigitalEmployeeTags([...value, ...candidates]));
              setError("");
            }}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" ||
                event.key === "," ||
                event.key === "，"
              ) {
                event.preventDefault();
                commit();
              }
              if (event.key === "Escape" && editingIndex !== null) {
                setEditingIndex(null);
                setDraft("");
              }
            }}
          />
          <span className="text-muted-foreground absolute top-1/2 right-2 -translate-y-1/2 text-[10px]">
            {digitalEmployeeTagLength(draft)}/{MAX_DIGITAL_EMPLOYEE_TAG_LENGTH}
          </span>
        </div>
        <Button
          aria-label={
            editingIndex === null ? t.agents.addTag : t.agents.confirmTagEdit
          }
          disabled={!draft.trim() || atLimit}
          size="icon"
          type="button"
          variant="outline"
          onClick={() => commit()}
        >
          {editingIndex === null ? <PlusIcon /> : <CheckIcon />}
        </Button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p
          className={
            error ? "text-destructive text-xs" : "text-muted-foreground text-xs"
          }
        >
          {error || t.agents.tagCount.replace("{count}", String(value.length))}
        </p>
        <Button
          className="h-7 gap-1 px-2 text-xs"
          type="button"
          variant="ghost"
          onClick={addSuggested}
        >
          <SparklesIcon className="size-3.5" />
          {t.agents.autoTag}
        </Button>
      </div>
    </div>
  );
}

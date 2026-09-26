"use client";

import { useId } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  KnowledgeScopeChips,
  KnowledgeScopeSelector,
} from "@/components/workspace/knowledge-scope-selector";
import {
  agentKnowledgeContext,
  agentKnowledgeSelection,
  invalidAgentKnowledgeSelection,
} from "@/core/agents/knowledge-selection";
import type { AgentKnowledgeScope } from "@/core/agents/types";
import { useI18n } from "@/core/i18n/hooks";

export function AgentKnowledgeSelection({
  value,
  onChange,
  disabled = false,
}: {
  value: AgentKnowledgeScope | null;
  onChange: (value: AgentKnowledgeScope | null) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  const context = agentKnowledgeContext(value);
  return (
    <fieldset
      disabled={disabled}
      aria-describedby={`${id}-hint`}
      className="flex min-w-0 flex-col gap-3"
      data-testid="agent-knowledge-selection"
    >
      <legend className="mb-2 text-sm font-medium">
        {t.agents.knowledgeSelectionTitle}
      </legend>
      <p id={`${id}-hint`} className="text-muted-foreground text-xs">
        {t.agents.knowledgeSelectionHint}
      </p>
      <ToggleGroup
        type="single"
        value={value?.mode ?? "inherit"}
        variant="outline"
        size="sm"
        disabled={disabled}
        className="flex-wrap"
        aria-label={t.agents.knowledgeSelectionTitle}
        onValueChange={(mode) => {
          if (mode === "inherit") onChange(null);
          if (mode === "disabled") onChange({ version: 1, mode, items: [] });
          if (mode === "selected" && value?.mode !== "selected")
            onChange({ version: 1, mode, items: [] });
        }}
      >
        <ToggleGroupItem value="inherit">
          {t.agents.knowledgeInherit}
        </ToggleGroupItem>
        <ToggleGroupItem value="disabled">
          {t.agents.knowledgeDisabled}
        </ToggleGroupItem>
        <ToggleGroupItem value="selected">
          {t.agents.knowledgeSelected}
        </ToggleGroupItem>
      </ToggleGroup>
      {value?.mode === "selected" ? (
        <>
          <KnowledgeScopeSelector
            allowTags={false}
            context={context}
            disabled={disabled}
            onContextChange={(next) =>
              onChange(agentKnowledgeSelection(next.weknora_scope_items ?? []))
            }
          />
          <KnowledgeScopeChips
            context={context}
            disabled={disabled}
            onContextChange={(next) =>
              onChange(agentKnowledgeSelection(next.weknora_scope_items ?? []))
            }
          />
          {invalidAgentKnowledgeSelection(value) ? (
            <p role="status" className="text-destructive text-xs">
              {t.agents.knowledgeSelectionRequired}
            </p>
          ) : null}
        </>
      ) : null}
    </fieldset>
  );
}

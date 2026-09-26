"use client";

import { useId, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  agentSkillChoices,
  MAX_AGENT_SKILLS,
  toggleAgentSkill,
} from "@/core/agents/skill-selection";
import { useI18n } from "@/core/i18n/hooks";
import { useSkills } from "@/core/skills/hooks";

export function AgentSkillSelection({
  value,
  onChange,
  disabled = false,
  enabled = true,
}: {
  value: string[] | null;
  onChange: (value: string[] | null) => void;
  disabled?: boolean;
  enabled?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  const [search, setSearch] = useState("");
  const { skills, isLoading, error } = useSkills({ enabled });
  const choices = agentSkillChoices(skills, value);
  const query = search.trim().toLocaleLowerCase();
  const visible = choices.filter((item) =>
    `${item.name} ${item.description}`.toLocaleLowerCase().includes(query),
  );
  return (
    <fieldset
      className="flex min-w-0 flex-col gap-3"
      disabled={disabled}
      aria-describedby={`${id}-hint`}
      data-testid="agent-skill-selection"
    >
      <legend className="mb-2 text-sm font-medium">
        {t.agents.skillSelectionTitle}
      </legend>
      <p id={`${id}-hint`} className="text-muted-foreground text-xs">
        {t.agents.skillSelectionHint}
      </p>
      <ToggleGroup
        type="single"
        value={value === null ? "inherit" : "selected"}
        onValueChange={(mode) => {
          if (mode === "inherit") onChange(null);
          if (mode === "selected" && value === null) onChange([]);
        }}
        disabled={disabled}
        variant="outline"
        size="sm"
        aria-label={t.agents.skillSelectionTitle}
        className="flex-wrap"
      >
        <ToggleGroupItem value="inherit">
          {t.agents.skillsInherit}
        </ToggleGroupItem>
        <ToggleGroupItem value="selected">
          {t.agents.skillsExplicit}
        </ToggleGroupItem>
      </ToggleGroup>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{t.agents.skillsLoadError}</AlertDescription>
        </Alert>
      ) : null}
      {value !== null ? (
        <>
          <label className="sr-only" htmlFor={`${id}-search`}>
            {t.agents.skillsSearch}
          </label>
          <Input
            id={`${id}-search`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t.agents.skillsSearch}
          />
          <div
            className="flex max-h-48 min-w-0 flex-col gap-2 overflow-y-auto"
            aria-busy={isLoading}
          >
            {isLoading ? (
              <p role="status" className="text-muted-foreground text-sm">
                {t.common.loading}
              </p>
            ) : null}
            {!isLoading && !error && visible.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                {t.agents.skillsEmpty}
              </p>
            ) : null}
            {visible.map((item) => (
              <label
                key={item.name}
                className="flex min-w-0 items-start gap-2 text-sm"
              >
                <input
                  type="checkbox"
                  className="mt-1 shrink-0"
                  checked={item.selected}
                  disabled={
                    disabled ||
                    (!item.selected &&
                      (!item.available ||
                        !!error ||
                        value.length >= MAX_AGENT_SKILLS))
                  }
                  onChange={() => onChange(toggleAgentSkill(value, item.name))}
                />
                <span className="min-w-0 break-words">
                  {item.name}
                  <span className="text-muted-foreground block text-xs">
                    {item.available
                      ? item.description
                      : t.agents.skillsUnavailable}
                  </span>
                </span>
              </label>
            ))}
          </div>
          <div className="flex items-center justify-between gap-2 text-xs">
            <span>
              {value.length}/{MAX_AGENT_SKILLS} ·{" "}
              {t.agents.skillsEmptyMeansNone}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || value.length === 0}
              onClick={() => onChange([])}
            >
              {t.agents.skillsClear}
            </Button>
          </div>
        </>
      ) : null}
    </fieldset>
  );
}

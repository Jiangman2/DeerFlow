import type { Agent } from "./types";

export interface DigitalEmployeeFilter {
  hotOnly?: boolean;
  query?: string;
  categoryId?: string | null;
  categoryNames?: Record<string, string>;
  tags?: string[];
}

function normalizedSearchText(agent: Agent, categoryName?: string) {
  return [
    agent.name,
    agent.display_name,
    agent.description,
    agent.model,
    categoryName,
    ...(agent.tags ?? []),
    ...(agent.tool_groups ?? []),
    ...(agent.skills ?? []),
  ]
    .filter(Boolean)
    .join(" ")
    .normalize("NFKC")
    .toLocaleLowerCase();
}

export function filterDigitalEmployees(
  agents: Agent[],
  filter: DigitalEmployeeFilter,
) {
  const terms = (filter.query ?? "")
    .normalize("NFKC")
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const selectedTags = new Set(
    (filter.tags ?? []).map((tag) => tag.toLocaleLowerCase()),
  );

  return agents.filter((agent) => {
    if (filter.hotOnly && agent.is_hot !== true) return false;
    if (filter.categoryId && agent.category_id !== filter.categoryId) {
      return false;
    }
    const agentTags = new Set(
      (agent.tags ?? []).map((tag) => tag.toLocaleLowerCase()),
    );
    if ([...selectedTags].some((tag) => !agentTags.has(tag))) {
      return false;
    }
    const haystack = normalizedSearchText(
      agent,
      agent.category_id ? filter.categoryNames?.[agent.category_id] : undefined,
    );
    return terms.every((term) => haystack.includes(term));
  });
}

export function collectDigitalEmployeeTags(agents: Agent[]) {
  const counts = new Map<string, number>();
  for (const agent of agents) {
    for (const tag of new Set(agent.tags ?? [])) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
  }
  return [...counts]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
}

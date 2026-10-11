import type { Agent, SquareSettings } from "./types";

export function squareKey(agent: Agent): string {
  return (
    agent.square_key ??
    `${agent.external_url ? "external" : (agent.source ?? "team")}:${agent.name}`
  );
}

// Apply global ordering before searching/filtering/pagination. Never mutate seeds.
export function applySquareSettings(
  agents: Agent[],
  settings: SquareSettings[],
  includeHidden = false,
): Agent[] {
  const byKey = new Map(settings.map((item) => [item.key, item]));
  const externalCount = agents.filter((agent) => agent.external_url).length;
  return agents
    .map((agent, index) => {
      const key = squareKey(agent);
      const setting = byKey.get(key);
      const metadata = Object.fromEntries(
        Object.entries(setting?.metadata ?? {}).filter(
          ([, value]) => value != null,
        ),
      );
      return {
        ...agent,
        ...metadata,
        square_key: key,
        square_hidden: setting?.hidden ?? false,
        square_version: setting?.version ?? 0,
        square_sort_order: setting?.sort_order ?? null,
        originalIndex: index,
        defaultOrder:
          agent.square_base_order == null
            ? (index + 1) * 10
            : (externalCount + agent.square_base_order + 1) * 10,
      };
    })
    .filter((agent) => includeHidden || !agent.square_hidden)
    .sort(
      (a, b) =>
        (a.square_sort_order ?? a.defaultOrder) -
          (b.square_sort_order ?? b.defaultOrder) ||
        a.originalIndex - b.originalIndex,
    )
    .map(({ originalIndex: _index, defaultOrder: _order, ...agent }) => agent);
}

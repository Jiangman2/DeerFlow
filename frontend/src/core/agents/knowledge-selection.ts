import { buildWeKnoraScopeContext } from "@/core/weknora/scope";
import type { WeKnoraScopeItem } from "@/core/weknora/types";

import type { AgentKnowledgeScope } from "./types";

export function agentKnowledgeContext(
  scope?: AgentKnowledgeScope | null,
): Partial<ReturnType<typeof buildWeKnoraScopeContext>> {
  if (!scope || scope.mode === "inherit") return {};
  return buildWeKnoraScopeContext(scope.mode === "selected" ? scope.items : []);
}

export function agentKnowledgeSelection(
  items: WeKnoraScopeItem[],
): AgentKnowledgeScope {
  if (items.some((item) => item.type === "tag")) {
    throw new Error(
      "Employee knowledge binding requires explicit bases or documents",
    );
  }
  return {
    version: 1,
    mode: "selected",
    items: items.map((item) => ({
      type: item.type as "kb" | "file",
      id: item.id,
      name: item.name,
      ...(item.kb_id ? { kb_id: item.kb_id } : {}),
      ...(item.kb_name ? { kb_name: item.kb_name } : {}),
      ...(item.tenant_id ? { tenant_id: item.tenant_id } : {}),
    })),
  };
}

export function invalidAgentKnowledgeSelection(
  scope?: AgentKnowledgeScope | null,
) {
  return (
    scope?.mode === "selected" &&
    (scope.items.length === 0 || scope.items.length > 20)
  );
}

import type {
  WeKnoraKnowledgeBase,
  WeKnoraScopeItem,
  WeKnoraTagScope,
} from "./types";

type WeKnoraScopeContext = {
  weknora_knowledge_base_ids?: string[];
  weknora_scope_items?: WeKnoraScopeItem[];
};

export function weknoraScopeItemKey(item: WeKnoraScopeItem): string {
  return `${item.type}:${item.id}`;
}

export function normalizeWeKnoraScopeItems(value: unknown): WeKnoraScopeItem[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seen = new Set<string>();
  const items: WeKnoraScopeItem[] = [];
  for (const candidate of value) {
    if (!candidate || typeof candidate !== "object") {
      continue;
    }
    const row = candidate as Partial<WeKnoraScopeItem>;
    if (
      typeof row.id !== "string" ||
      !row.id.trim() ||
      typeof row.name !== "string" ||
      !row.name.trim() ||
      (row.type !== "kb" && row.type !== "file" && row.type !== "tag")
    ) {
      continue;
    }
    if (
      row.type !== "kb" &&
      (typeof row.kb_id !== "string" || !row.kb_id.trim())
    ) {
      continue;
    }
    const item: WeKnoraScopeItem = {
      id: row.id.trim(),
      name: row.name.trim(),
      type: row.type,
      ...(row.kb_id?.trim() ? { kb_id: row.kb_id.trim() } : {}),
      ...(row.kb_name?.trim() ? { kb_name: row.kb_name.trim() } : {}),
      ...(row.kb_type?.trim() ? { kb_type: row.kb_type.trim() } : {}),
      ...(row.tenant_id?.trim() ? { tenant_id: row.tenant_id.trim() } : {}),
    };
    const key = weknoraScopeItemKey(item);
    if (!seen.has(key)) {
      seen.add(key);
      items.push(item);
    }
  }
  return items;
}

export function weknoraScopeItemsFromContext(
  context: WeKnoraScopeContext,
  knowledgeBases: WeKnoraKnowledgeBase[],
): WeKnoraScopeItem[] {
  const current = normalizeWeKnoraScopeItems(context.weknora_scope_items);
  if (current.length > 0 || context.weknora_scope_items !== undefined) {
    return current;
  }
  const legacyIds = Array.isArray(context.weknora_knowledge_base_ids)
    ? new Set(context.weknora_knowledge_base_ids)
    : new Set<string>();
  return knowledgeBases
    .filter((kb) => legacyIds.has(kb.id))
    .map((kb) => ({
      id: kb.id,
      name: kb.name,
      type: "kb" as const,
      ...(kb.type ? { kb_type: kb.type } : {}),
      ...(kb.access_tenant_id !== undefined
        ? { tenant_id: String(kb.access_tenant_id) }
        : {}),
    }));
}

export function toggleWeKnoraScopeItem(
  current: WeKnoraScopeItem[],
  item: WeKnoraScopeItem,
): WeKnoraScopeItem[] {
  const normalized = normalizeWeKnoraScopeItems(current);
  const key = weknoraScopeItemKey(item);
  if (normalized.some((candidate) => weknoraScopeItemKey(candidate) === key)) {
    return normalized.filter(
      (candidate) => weknoraScopeItemKey(candidate) !== key,
    );
  }

  const next =
    item.type === "kb"
      ? normalized.filter(
          (candidate) =>
            candidate.id !== item.id && candidate.kb_id !== item.id,
        )
      : normalized.filter(
          (candidate) =>
            !(candidate.type === "kb" && candidate.id === item.kb_id),
        );
  return [...next, item];
}

export function buildWeKnoraScopeContext(items: WeKnoraScopeItem[]): {
  weknora_knowledge_base_ids: string[];
  weknora_knowledge_bases: { id: string; name: string }[];
  weknora_knowledge_ids: string[];
  weknora_tag_scopes: WeKnoraTagScope[];
  weknora_scope_items: WeKnoraScopeItem[];
} {
  const normalized = normalizeWeKnoraScopeItems(items);
  const knowledgeBases = normalized.filter((item) => item.type === "kb");
  const knowledgeIds = normalized
    .filter((item) => item.type === "file")
    .map((item) => item.id);
  const tagIdsByKnowledgeBase = new Map<string, string[]>();
  for (const item of normalized) {
    if (item.type !== "tag" || !item.kb_id) {
      continue;
    }
    const ids = tagIdsByKnowledgeBase.get(item.kb_id) ?? [];
    ids.push(item.id);
    tagIdsByKnowledgeBase.set(item.kb_id, ids);
  }
  return {
    weknora_knowledge_base_ids: knowledgeBases.map((item) => item.id),
    weknora_knowledge_bases: knowledgeBases.map((item) => ({
      id: item.id,
      name: item.name,
    })),
    weknora_knowledge_ids: knowledgeIds,
    weknora_tag_scopes: Array.from(
      tagIdsByKnowledgeBase,
      ([knowledge_base_id, tag_ids]) => ({
        knowledge_base_id,
        tag_ids: Array.from(new Set(tag_ids)),
      }),
    ),
    weknora_scope_items: normalized,
  };
}

export function isWeKnoraScopeItemSelected(
  items: WeKnoraScopeItem[],
  item: WeKnoraScopeItem,
): boolean {
  const key = weknoraScopeItemKey(item);
  return items.some((candidate) => weknoraScopeItemKey(candidate) === key);
}

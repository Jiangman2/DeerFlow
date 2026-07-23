import type { WeKnoraKnowledgeBase } from "./types";

export function isOwnedKnowledgeBase(kb: WeKnoraKnowledgeBase): boolean {
  return kb.is_owner === true || kb.access_scope === "owned";
}

export function isJoinedKnowledgeBase(kb: WeKnoraKnowledgeBase): boolean {
  if (typeof kb.is_joined === "boolean") return kb.is_joined;
  return [
    kb.access_scope === "joined",
    Boolean(kb.share_id?.trim()),
    Boolean(kb.organization_id?.trim()),
  ].some(Boolean);
}

export function isTeamOrPublicKnowledgeBase(kb: WeKnoraKnowledgeBase): boolean {
  if (typeof kb.is_team_or_public === "boolean") {
    return kb.is_team_or_public;
  }
  return [
    kb.access_scope === "team" ||
      kb.access_scope === "public" ||
      Boolean(kb.share_id?.trim()) ||
      Boolean(kb.organization_id?.trim()) ||
      (kb.share_count ?? 0) > 0,
  ].some(Boolean);
}

const KNOWLEDGE_SIDEBAR_COLLAPSED_KEY = "deerflow.knowledge.sidebar-collapsed";

type StorageLike = Pick<Storage, "getItem" | "setItem">;

export function readKnowledgeSidebarCollapsed(
  storage: StorageLike | undefined,
): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(KNOWLEDGE_SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeKnowledgeSidebarCollapsed(
  storage: StorageLike | undefined,
  collapsed: boolean,
): void {
  if (!storage) return;
  try {
    storage.setItem(
      KNOWLEDGE_SIDEBAR_COLLAPSED_KEY,
      collapsed ? "true" : "false",
    );
  } catch {
    // Storage can be unavailable in hardened enterprise browser profiles.
  }
}

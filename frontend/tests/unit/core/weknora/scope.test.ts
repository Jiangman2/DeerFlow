import { describe, expect, it } from "@rstest/core";

import {
  buildWeKnoraScopeContext,
  MAX_WEKNORA_SCOPE_ITEMS,
  normalizeWeKnoraScopeItems,
  toggleWeKnoraScopeItem,
  weknoraScopeItemsFromContext,
} from "@/core/weknora/scope";
import type { WeKnoraScopeItem } from "@/core/weknora/types";

const kb: WeKnoraScopeItem = {
  id: "kb-1",
  name: "制度库",
  type: "kb",
  kb_type: "document",
};
const file: WeKnoraScopeItem = {
  id: "doc-1",
  name: "报销制度.pdf",
  type: "file",
  kb_id: "kb-1",
  kb_name: "制度库",
};
const tag: WeKnoraScopeItem = {
  id: "tag-1",
  name: "财务",
  type: "tag",
  kb_id: "kb-1",
  kb_name: "制度库",
};

describe("WeKnora chat scope", () => {
  it("upgrades legacy whole-KB selections", () => {
    expect(
      weknoraScopeItemsFromContext({ weknora_knowledge_base_ids: ["kb-1"] }, [
        { id: "kb-1", name: "制度库", type: "document" },
      ]),
    ).toEqual([kb]);
  });

  it("selecting a file replaces its whole-KB selection", () => {
    expect(toggleWeKnoraScopeItem([kb], file)).toEqual([file]);
  });

  it("selecting a whole KB clears narrower items from that KB only", () => {
    const other: WeKnoraScopeItem = {
      id: "doc-2",
      name: "安全手册.pdf",
      type: "file",
      kb_id: "kb-2",
      kb_name: "安全库",
    };
    expect(toggleWeKnoraScopeItem([file, tag, other], kb)).toEqual([other, kb]);
  });

  it("builds native KB, file, tag, and mention-shaped context", () => {
    const result = buildWeKnoraScopeContext([file, tag]);
    expect(result.weknora_knowledge_base_ids).toEqual([]);
    expect(result.weknora_knowledge_ids).toEqual(["doc-1"]);
    expect(result.weknora_tag_scopes).toEqual([
      { knowledge_base_id: "kb-1", tag_ids: ["tag-1"] },
    ]);
    expect(result.weknora_scope_items).toEqual([file, tag]);
  });

  it("rejects file and tag selections without a parent KB", () => {
    expect(
      normalizeWeKnoraScopeItems([
        { id: "doc-orphan", name: "孤立文件", type: "file" },
        kb,
      ]),
    ).toEqual([kb]);
  });

  it("keeps same-id child selections from different knowledge bases distinct", () => {
    const sharedIdInAnotherKb: WeKnoraScopeItem = {
      id: file.id,
      name: "同编号安全手册.pdf",
      type: "file",
      kb_id: "kb-2",
      kb_name: "安全库",
    };

    expect(toggleWeKnoraScopeItem([file], sharedIdInAnotherKb)).toEqual([
      file,
      sharedIdInAnotherKb,
    ]);
  });

  it("caps one chat turn to a bounded number of selected scopes", () => {
    const manyFiles = Array.from(
      { length: MAX_WEKNORA_SCOPE_ITEMS + 5 },
      (_, index): WeKnoraScopeItem => ({
        id: `doc-${index}`,
        name: `文件 ${index}`,
        type: "file",
        kb_id: "kb-1",
        kb_name: "制度库",
      }),
    );

    expect(normalizeWeKnoraScopeItems(manyFiles)).toHaveLength(
      MAX_WEKNORA_SCOPE_ITEMS,
    );
    expect(
      buildWeKnoraScopeContext(manyFiles).weknora_scope_items,
    ).toHaveLength(MAX_WEKNORA_SCOPE_ITEMS);
  });
});

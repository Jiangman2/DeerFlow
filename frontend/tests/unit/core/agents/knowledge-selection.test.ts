import { describe, expect, it } from "@rstest/core";

import {
  agentKnowledgeContext,
  agentKnowledgeSelection,
  invalidAgentKnowledgeSelection,
} from "@/core/agents/knowledge-selection";

describe("employee knowledge selection", () => {
  it("does not override chat knowledge for inherited selection", () => {
    expect(agentKnowledgeContext(null)).toEqual({});
  });
  it("clears all five fields when disabled", () => {
    expect(
      agentKnowledgeContext({ version: 1, mode: "disabled", items: [] }),
    ).toEqual({
      weknora_knowledge_base_ids: [],
      weknora_knowledge_bases: [],
      weknora_knowledge_ids: [],
      weknora_tag_scopes: [],
      weknora_scope_items: [],
    });
  });
  it("keeps an exact document scope without enabling its parent", () => {
    const scope = agentKnowledgeSelection([
      {
        type: "file",
        id: "doc-1",
        kb_id: "kb-1",
        name: "Report",
        tenant_id: "tenant-1",
      },
    ]);
    expect(agentKnowledgeContext(scope).weknora_knowledge_base_ids).toEqual([]);
    expect(agentKnowledgeContext(scope).weknora_knowledge_ids).toEqual([
      "doc-1",
    ]);
    expect(scope.items[0]?.tenant_id).toBe("tenant-1");
  });
  it("does not persist extra display/provider fields", () => {
    const scope = agentKnowledgeSelection([
      { type: "kb", id: "kb-1", name: "Reports", kb_type: "document" },
    ]);
    expect(scope.items[0]).not.toHaveProperty("kb_type");
  });
  it("does not silently drop unsupported tags", () => {
    expect(() =>
      agentKnowledgeSelection([
        { type: "tag", id: "tag-1", kb_id: "kb-1", name: "Reports" },
      ]),
    ).toThrow();
  });
  it("distinguishes incomplete explicit selection from disabled knowledge", () => {
    expect(
      invalidAgentKnowledgeSelection({
        version: 1,
        mode: "selected",
        items: [],
      }),
    ).toBe(true);
    expect(
      invalidAgentKnowledgeSelection({
        version: 1,
        mode: "disabled",
        items: [],
      }),
    ).toBe(false);
  });
});

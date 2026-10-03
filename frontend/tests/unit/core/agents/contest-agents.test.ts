import { expect, test } from "@rstest/core";

import { CONTEST_EXTERNAL_AGENTS } from "@/core/agents/contest-agents";
import { filterDigitalEmployees } from "@/core/agents/digital-employee-filter";
import { getExpertAvatarById } from "@/core/agents/expert-avatars";
import { resolveExternalAgentLaunchHref } from "@/core/agents/external-launch";
import { SHARED_EXTERNAL_AGENTS } from "@/core/agents/shared-agents";

// Reviewed application URLs from Sheet1 F/M, rows 10,11,13,14,19,20,22-25,27,31.
const EXPECTED_ENTRIES = [
  ["储小亮", "https://10.141.11.158:8443/", "oil-gas-resource-development"],
  [
    "研究总院水驱可采储量计算专家",
    "http://10.57.4.101:8088",
    "oil-gas-resource-development",
  ],
  [
    "海油发展井控全周期智能管家",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/871c196a1ecb434fb9e6c722b251a9fe/W4ND",
    "production-operations",
  ],
  ["海油发展新能源生产运维智能助手", "https://xny.cnooc/", "new-energy"],
  [
    "数字化项目审查AI助手",
    "https://csp.cet.cnooc.com.cn/diep/hwc/coremate/",
    "digitalization",
  ],
  [
    "海油发展智能配方优选管家",
    "http://10.77.65.163:8000/",
    "science-technology-innovation",
  ],
  [
    "ERP风险预警专家",
    "https://s4dev01-erp.cnooc.com.cn/sap/bc/ui2/flp?saml2=disabled&spnego=disabled#Shell-home",
    "finance-capital",
  ],
  [
    "合同合规审核专家",
    "https://vl.cet.cnooc/toolset?tool=doc-review-workbench",
    "legal-affairs",
  ],
  [
    "科研智能伙伴",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/71f5e26f83c540319d892deaf0ff21a6/YTk7",
    "science-technology-innovation",
  ],
  ["业财AI助手", "https://aoqin.cnooc.com.cn/", "finance-capital"],
  [
    "物装小灵通",
    "https://aiagent.bid.cnooc.com.cn/chat",
    "materials-supply-chain",
  ],
  [
    "智绘新途",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/ff80a2d599d74badb4000efca239754d/Gs9Q",
    "materials-supply-chain",
  ],
];

test("imports exactly the 12 application entries using source names and complete URLs", () => {
  expect(CONTEST_EXTERNAL_AGENTS).toHaveLength(12);
  expect(
    CONTEST_EXTERNAL_AGENTS.map((agent) => [
      agent.name,
      agent.external_url,
      agent.category_id,
    ]),
  ).toEqual(EXPECTED_ENTRIES);
  expect(
    CONTEST_EXTERNAL_AGENTS.some((agent) => agent.name === "套保训练助手"),
  ).toBe(false);
});

test("publishes every contest entry once without replacing existing employees", () => {
  expect(new Set(SHARED_EXTERNAL_AGENTS.map((agent) => agent.name)).size).toBe(
    SHARED_EXTERNAL_AGENTS.length,
  );
  expect(SHARED_EXTERNAL_AGENTS.slice(-12)).toEqual(CONTEST_EXTERNAL_AGENTS);
  for (const name of [
    "专业文档解析专家",
    "地震数据提频专家",
    "专业报告校审专家",
    "自动数采专家",
    "前期研究报告编写专家",
  ]) {
    const existing = SHARED_EXTERNAL_AGENTS.find(
      (agent) => agent.name === name,
    );
    expect(existing?.external_url).toMatch(
      /^https:\/\/cnoocclaw\.cnooc\.com\.cn\//,
    );
  }
});

test("uses external jumps, existing portraits, and no more than three unique tags", () => {
  for (const agent of CONTEST_EXTERNAL_AGENTS) {
    expect(agent.source).toBe("team");
    expect(agent.model).toBeNull();
    expect(agent.skills).toBeNull();
    expect(agent.tool_groups).toBeNull();
    expect(agent.description.length).toBeGreaterThan(0);
    expect(agent.tags?.length).toBeGreaterThan(0);
    expect(agent.tags!.length).toBeLessThanOrEqual(3);
    expect(new Set(agent.tags).size).toBe(agent.tags!.length);
    expect(getExpertAvatarById(agent.avatar_id)).toBeDefined();
    expect(["http:", "https:"]).toContain(
      new URL(agent.external_url!).protocol,
    );
    expect(resolveExternalAgentLaunchHref(agent.external_url)).toBe(
      agent.external_url,
    );
  }
});

test("supports existing name, category and tag discovery for contest employees", () => {
  expect(
    filterDigitalEmployees(SHARED_EXTERNAL_AGENTS, { query: "储小亮" }),
  ).toEqual([CONTEST_EXTERNAL_AGENTS[0]]);
  expect(
    filterDigitalEmployees(SHARED_EXTERNAL_AGENTS, {
      categoryId: "finance-capital",
    }),
  ).toEqual([CONTEST_EXTERNAL_AGENTS[6], CONTEST_EXTERNAL_AGENTS[9]]);
  expect(
    filterDigitalEmployees(SHARED_EXTERNAL_AGENTS, { tags: ["危化品运输"] }),
  ).toEqual([CONTEST_EXTERNAL_AGENTS[11]]);
});

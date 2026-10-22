import { expect, test } from "@rstest/core";

import { CONTEST_EXTERNAL_AGENTS } from "@/core/agents/contest-agents";
import { filterDigitalEmployees } from "@/core/agents/digital-employee-filter";
import { getExpertAvatarById } from "@/core/agents/expert-avatars";
import { resolveExternalAgentLaunchHref } from "@/core/agents/external-launch";
import { SHARED_EXTERNAL_AGENTS } from "@/core/agents/shared-agents";

// Reviewed 1008 Sheet1 entries: Q overrides F for names; P11/P21 supply current URLs.
const EXPECTED_ENTRIES = [
  ["储小亮", "https://10.141.11.158:8443/", "oil-gas-resource-development"],
  ["智能穿透审计助手", "http://10.77.79.194:18080/", "audit-inspection"],
  [
    "海油发展井控全周期智能管家",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/871c196a1ecb434fb9e6c722b251a9fe/W4ND",
    "production-operations",
  ],
  ["海油发展新能源生产运维智能助手", "https://xny.cnooc/", "new-energy"],
  [
    "设备全周期健康管理专员",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/f675c6c885e34cbfbd0ae07bc0fdfb5f/l0di",
    "production-operations",
  ],
  [
    "工程建造项目承揽助手",
    "https://10.195.2.18/login",
    "engineering-technology",
  ],
  [
    "中联公司录井与完井报告审核助手",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/3f12fddf67ed4be8bb94d64d22c29a8d/GVA3",
    "engineering-technology",
  ],
  [
    "智安咨询硅基员工",
    "https://aqhb.cnooc.com.cn/web/zxai/webapp/",
    "safety-environment",
  ],
  [
    "数字化项目审查AI助手",
    "https://csp.cet.cnooc.com.cn/diep/hwc/coremate/",
    "digitalization",
  ],
  [
    "海油发展智能配方优选管家",
    "http://10.77.80.56:10086/login?redirect=/chat",
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
    "科研项目全周期管理助手",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/71f5e26f83c540319d892deaf0ff21a6/YTk7",
    "science-technology-innovation",
  ],
  ["业财一体化智能助手", "https://aoqin.cnooc.com.cn/", "finance-capital"],
  [
    "采购供应链智能助手",
    "https://aiagent.bid.cnooc.com.cn/chat",
    "materials-supply-chain",
  ],
  [
    "气电集团物流监管智能决策助手-e安达",
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/8cc46004a5f348abb80dde7c36344019/VcH3",
    "materials-supply-chain",
  ],
  [
    "物料拍图智能搜索助手",
    "https://ibdas.cet.cnooc.com.cn:8000/dwl-web/smart-image-search/",
    "materials-supply-chain",
  ],
  [
    "钻井安全智能决策助手",
    "https://dzgch.com/home.html",
    "oil-gas-resource-development",
  ],
];

test("imports exactly the 18 latest application entries using revised names and complete URLs", () => {
  expect(CONTEST_EXTERNAL_AGENTS).toHaveLength(18);
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
  expect(SHARED_EXTERNAL_AGENTS).toHaveLength(24);
  expect(SHARED_EXTERNAL_AGENTS.slice(-18)).toEqual(CONTEST_EXTERNAL_AGENTS);
  for (const [name, pathname] of [
    ["专业文档解析专家", "/zywdjxzj/"],
    ["地震数据提频专家", "/dzsjtpzj/"],
    ["专业报告校审专家", "/zjbgjszj/"],
    ["自动数采专家", "/zdsczj/"],
    ["前期研究报告编写专家", "/qqyjbgbxzj/"],
    ["邮件智能整理助手", "/yjzs/"],
  ] as const) {
    const existing = SHARED_EXTERNAL_AGENTS.filter(
      (agent) => agent.name === name,
    );
    expect(existing).toHaveLength(1);
    expect(existing[0]?.external_url).toBe(
      `https://cnoocclaw.cnooc.com.cn${pathname}`,
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
  ).toEqual(
    CONTEST_EXTERNAL_AGENTS.filter(
      (agent) => agent.category_id === "finance-capital",
    ),
  );
  expect(
    filterDigitalEmployees(SHARED_EXTERNAL_AGENTS, { tags: ["危化品运输"] }),
  ).toEqual([
    CONTEST_EXTERNAL_AGENTS.find(
      (agent) => agent.name === "气电集团物流监管智能决策助手-e安达",
    ),
  ]);
});

test("uses latest status-column URLs and removes superseded names without duplicating entries", () => {
  expect(new Set(CONTEST_EXTERNAL_AGENTS.map((agent) => agent.name)).size).toBe(
    18,
  );
  expect(
    new Set(CONTEST_EXTERNAL_AGENTS.map((agent) => agent.external_url)).size,
  ).toBe(18);
  for (const name of [
    "研究总院水驱可采储量计算专家",
    "科研智能伙伴",
    "业财AI助手",
    "物装小灵通",
    "智绘新途",
    "海油e安达",
    "物料拍图搜",
    "地智工程",
  ]) {
    expect(CONTEST_EXTERNAL_AGENTS.some((agent) => agent.name === name)).toBe(
      false,
    );
  }
  expect(
    CONTEST_EXTERNAL_AGENTS.find(
      (agent) => agent.name === "中联公司录井与完井报告审核助手",
    )?.external_url,
  ).toBe(
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/3f12fddf67ed4be8bb94d64d22c29a8d/GVA3",
  );
  expect(
    CONTEST_EXTERNAL_AGENTS.find(
      (agent) => agent.name === "气电集团物流监管智能决策助手-e安达",
    )?.external_url,
  ).toBe(
    "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/8cc46004a5f348abb80dde7c36344019/VcH3",
  );
  for (const agent of CONTEST_EXTERNAL_AGENTS) {
    expect(agent.name).toBe(agent.name.trim());
    expect(agent.external_url).toBe(agent.external_url!.trim());
    expect(agent.external_url).not.toMatch(/\s|链接更新/);
  }
});

test("does not add linkless scenarios or replace their existing fixed employees", () => {
  for (const name of [
    "采油工程前期方案设计注水压力计算",
    "海南公司低渗储层产能预测助手",
    "地震数据提频专家",
    "邮件智能整理助手",
    "套保训练助手",
  ]) {
    expect(CONTEST_EXTERNAL_AGENTS.some((agent) => agent.name === name)).toBe(
      false,
    );
  }
});

test("new employees expose the reviewed categories, tags and original descriptions", () => {
  const expected = [
    {
      name: "设备全周期健康管理专员",
      category_id: "production-operations",
      tags: ["设备健康", "故障诊断", "风险分析"],
      description:
        "依托海能AI平台，融合大小模型协同分析，构建设备知识库与全生命周期画像，可提供异常发现、故障诊断、处置决策、JHA风险分析及知识问答等一站式辅助，供运行检修人员使用。",
    },
    {
      name: "工程建造项目承揽助手",
      category_id: "engineering-technology",
      tags: ["工程投标", "报价组价", "文件编制"],
      description:
        "集团范围内投标人员可借助该助手完成招标文件研读、报价组价、文件编制全流程，提升投标效率与一致性。",
    },
    {
      name: "中联公司录井与完井报告审核助手",
      category_id: "engineering-technology",
      tags: ["录井研判", "完井报告", "智能审核"],
      description:
        "中联公司录井、地质及完井人员借助该智能体开展录井异常研判和完井报告审核，有效提升问题检出率与工作效率。",
    },
    {
      name: "智安咨询硅基员工",
      category_id: "safety-environment",
      tags: ["安环咨询", "研究分析", "报告审核"],
      description:
        "平台可完成资料获取、研究分析、报告撰写与审核，有效提升咨询工作效率，服务于集团内安全环保咨询业务人员。",
    },
    {
      name: "物料拍图智能搜索助手",
      category_id: "materials-supply-chain",
      tags: ["物料识别", "图像搜索", "编码匹配"],
      description:
        "为落实集团公司物资管理问题改进工作研讨会要求，推进物资管理“一物一码、一码到底”落地，解决现场物资识别效率低、编码匹配不精准、人工查询工作量大等痛点，特建设“物料拍图搜”智能工具。",
    },
    {
      name: "钻井安全智能决策助手",
      category_id: "oil-gas-resource-development",
      tags: ["钻井安全", "智能决策"],
      description:
        "它不只是一个OA流程，也不只是一个辅助决策工具，它是新一代的智能决策专家。",
    },
  ];
  for (const entry of expected) {
    expect(
      CONTEST_EXTERNAL_AGENTS.find((agent) => agent.name === entry.name),
    ).toMatchObject(entry);
    expect(
      filterDigitalEmployees(SHARED_EXTERNAL_AGENTS, { query: entry.name }),
    ).toEqual([
      CONTEST_EXTERNAL_AGENTS.find((agent) => agent.name === entry.name),
    ]);
  }
});

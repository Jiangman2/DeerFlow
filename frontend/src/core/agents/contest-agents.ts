import type { Agent } from "./types";

/**
 * 数字员工初筛_URL-20260928.xlsx / Sheet1, in source-row order.
 * Only explicit application URLs in column M are included. M29 says “暂无”;
 * its hidden GitHub repository hyperlink is not an application entry point.
 * Names/URLs are trimmed; descriptions, URL queries and fragments are preserved.
 */
export const CONTEST_EXTERNAL_AGENTS: Agent[] = [
  // Row 10: 勘探开发 / 油气资源开发
  {
    name: "储小亮",
    description:
      "上海公司储量评价人员借助该工具开展储量报告智能校阅，提升审核效率与准确性。",
    category_id: "oil-gas-resource-development",
    tags: ["储量评价", "报告校阅", "智能审核"],
    avatar_id: "reservoir-development",
    external_url: "https://10.141.11.158:8443/",
  },
  // Row 11: 勘探开发 / 油气资源开发
  {
    name: "研究总院水驱可采储量计算专家",
    description:
      "研究总院开发规划与油藏工程人员采用该方法自动计算水驱可采储量，实现可闭环、可验证的产量预测。",
    category_id: "oil-gas-resource-development",
    tags: ["水驱储量", "油藏工程", "产量预测"],
    avatar_id: "reservoir-development",
    external_url: "http://10.57.4.101:8088",
  },
  // Row 13: 生产运营 / 能源技术服务; specifically well-control operations.
  {
    name: "海油发展井控全周期智能管家",
    description:
      "海油发展井控与钻井作业人员可依托该系统实现井控标准查询、案例智能分析、溢流监测与自动压井，提升作业安全水平。",
    category_id: "production-operations",
    tags: ["井控管理", "溢流监测", "作业安全"],
    avatar_id: "drilling-completion",
    external_url:
      "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/871c196a1ecb434fb9e6c722b251a9fe/W4ND",
  },
  // Row 14: 生产运营 / 能源技术服务; specifically new-energy sites.
  {
    name: "海油发展新能源生产运维智能助手",
    description:
      "构建专家经验知识库与技术资料库，实现生产管理协同与安全风险管控，适用于海油发展清洁能源公司场站运维与生产管理人员。",
    category_id: "new-energy",
    tags: ["场站运维", "生产协同", "风险管控"],
    avatar_id: "renewables-engineer",
    external_url: "https://xny.cnooc/",
  },
  // Row 19: 数智化 / 能源技术服务
  {
    name: "数字化项目审查AI助手",
    description:
      "该数字员工可完成立项报告审查、算力评估、coding辅助及日常办公支持，供集团内数字化运营中心及项目管理人员使用。",
    category_id: "digitalization",
    tags: ["立项审查", "算力评估", "编程辅助"],
    avatar_id: "intelligent-oilfield-data",
    external_url: "https://csp.cet.cnooc.com.cn/diep/hwc/coremate/",
  },
  // Row 20: 科技创新 / 能源技术服务
  {
    name: "海油发展智能配方优选管家",
    description:
      "海油发展化工与材料研发人员可利用该智能体完成配方优选与决策，实现实验数据自动采集、配方推荐和智能问答。",
    category_id: "science-technology-innovation",
    tags: ["配方优选", "材料研发", "实验数据"],
    avatar_id: "ai-scientist",
    external_url: "http://10.77.65.163:8000/",
  },
  // Row 22: 财务资本; retain #Shell-home from displayed URL, not hyperlink.
  {
    name: "ERP风险预警专家",
    description:
      "为适配集团ERP深化建设，落实国资委两金压降、往来专项审计监管要求，针对现有往来管理依靠人工稽核、数据更新滞后、缺少可视化及分级权限、往来业务多项合规隐患等痛点。本项目依托SAP‑BW开展业财数据治理，搭建穿透式往来智能分析体系，以十大往来场景落地T+0数据更新、分级权限、智能问数、可视化看板与前置风险预警。项目融合可控智能体和大模型，依托Harness机制规避模型幻觉；采用模块化开发，可复用至其他业务板块，前移风控关口、缩减人工工作量，打造ERP智能化可复制标杆。",
    category_id: "finance-capital",
    tags: ["ERP", "往来风控", "风险预警"],
    avatar_id: "finance-analyst",
    external_url:
      "https://s4dev01-erp.cnooc.com.cn/sap/bc/ui2/flp?saml2=disabled&spnego=disabled#Shell-home",
  },
  // Row 23: 法务
  {
    name: "合同合规审核专家",
    description:
      '基于海能平台与DeepSeek-V4-Flash大模型，构建"文档解析→逐风险点审核→结果复核"三阶段协同的合同合规智能审核系统，数分钟完成全文审核，自动输出风险等级、原文定位与审查建议，效率提升10倍以上，准确度超90%，已在法律支持中心试用。',
    category_id: "legal-affairs",
    tags: ["合同审核", "法律合规", "风险识别"],
    avatar_id: "procurement-specialist",
    external_url: "https://vl.cet.cnooc/toolset?tool=doc-review-workbench",
  },
  // Row 24: 科技创新
  {
    name: "科研智能伙伴",
    description:
      '基于"海能"平台开发AI智能体，实现科研项目立项重复性检查、任务书信息自动填报、信息智询等功能，与科研项目全周期流程深度融合，支撑精细化管控，降低科研人员事务性工作耗时，解放科研精力，同时盘活项目知识资产，实现成果高效复用，提升科研创新能力。',
    category_id: "science-technology-innovation",
    tags: ["科研管理", "立项查重", "任务书填报"],
    avatar_id: "report-research",
    external_url:
      "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/71f5e26f83c540319d892deaf0ff21a6/YTk7",
  },
  // Row 25: 财务资本
  {
    name: "业财AI助手",
    description:
      "本项目依托企业数软AI智能体，围绕差旅报销、科研经费、单井成本等经营管理场景，打造业财AI智能应用体系，实现业务办理、数据查询、报表生成和经营分析的流程优化，推动业务数据高效协同，由人工处理向智能辅助转变，提升管理效能与风险管控，助力企业数字化转型。",
    category_id: "finance-capital",
    tags: ["业财协同", "差旅报销", "经营分析"],
    avatar_id: "finance-analyst",
    external_url: "https://aoqin.cnooc.com.cn/",
  },
  // Row 27: 物资供应链
  {
    name: "物装小灵通",
    description:
      '以物资供应链高质量数据集为基础，基于"海能"人工智能平台，运用大语言模型、知识图谱、集成学习等方法，搭建"物装小灵通"智能体集群，赋能工厂扩展、物资申码、采购时效预测等场景，实现与ERP、MDM数据互通，并已在海油内部14家单位应用，经营管理效率显著提升。',
    category_id: "materials-supply-chain",
    tags: ["物资申码", "采购预测", "供应链"],
    avatar_id: "procurement-specialist",
    external_url: "https://aiagent.bid.cnooc.com.cn/chat",
  },
  // Row 31: 物资供应链 / 天然气与发电; retain the source's full description.
  {
    name: "智绘新途",
    description:
      "效率，前置拦截高风险运力，筑牢贸易规模扩张的合规技术屏障。\n在途监管阶段，基于深度学习与全模型图谱训练技术，结合200万笔运单数据与近10年行业违规案例训练专属识别模型，通过多路感知融合与YOLO目标检测精准捕捉危险驾驶行为；同步创新构建保供需求-历史违规-管理规则多维决策引擎，统筹输出处置方案，既闭环整改违规隐患，又保障能源供应连续稳定。\n决策端依托组件化SaaS架构，采用时空聚类算法挖掘全国槽车轨迹大数据，构建区域供需热度模型，识别潜在用气终端、预判市场需求趋势，推动贸易经营从经验判断向智能决策升级。\n项目已覆盖集团8家分公司，累计监管超百万车次，为能源行业危化品贸易数智化升级提供可复制的技术实践方案。",
    category_id: "materials-supply-chain",
    tags: ["物流监管", "危化品运输", "供需预测"],
    avatar_id: "helicopter-logistics",
    external_url:
      "https://agents.cnooc.com.cn/agent/agents-share-web/#/assistant/share/ff80a2d599d74badb4000efca239754d/Gs9Q",
  },
].map((entry) => ({
  ...entry,
  model: null,
  tool_groups: null,
  skills: null,
  source: "team",
}));

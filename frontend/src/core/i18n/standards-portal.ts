import type { Locale } from "./locale";

const zhCN = {
  title: "中海油 SeaStandard",
  description: "中海油标准智能服务门户",
  brandSlogan: "智启海油 · 赋能未来",
  platformName: "海能 AI",
  backToPlatform: "返回智能体平台",
  eyebrow: "标准智能服务门户",
  capabilitiesTitle: "选择您需要的能力",
  entryHint: "选择标准服务，进入对应的数字员工。",
  entryAction: "进入使用",
  unconfiguredMessage: "尚未配置对应数字员工，请联系管理员。",
  closeDialog: "我知道了",
  features: {
    qa: {
      title: "标准知识问答",
      description: "围绕标准原文提问，查找有依据的解答",
    },
    search: {
      title: "标准信息检索",
      description: "按编号、名称、领域和状态查找标准",
    },
    policy: {
      title: "政策研究解读",
      description: "检索政策、解读条款，追踪变化动态",
    },
    comparison: {
      title: "标准对比分析",
      description: "对比两项标准或新旧版本的主要差异",
    },
    applicability: {
      title: "标准适用性分析",
      description: "输入技术场景，查找并评估适用标准",
    },
    compliance: {
      title: "合规自检与预警",
      description: "核对项目用标情况，识别时效风险",
    },
    authoring: {
      title: "标准编审辅助",
      description: "在线编制与草案审校",
    },
  },
  footer: "中海油 SeaStandard · 标准智能服务门户",
  copyright: "© 2026–2027 中国海洋石油集团有限公司",
};

const enUS: typeof zhCN = {
  title: "CNOOC SeaStandard",
  description: "CNOOC intelligent standards portal",
  brandSlogan: "Intelligence for CNOOC · Empowering the future",
  platformName: "Haineng AI",
  backToPlatform: "Back to agent platform",
  eyebrow: "INTELLIGENT STANDARDS SERVICES",
  capabilitiesTitle: "Choose a capability",
  entryHint: "Choose a standards service to open its digital employee.",
  entryAction: "Open service",
  unconfiguredMessage:
    "No digital employee is configured for this service. Please contact an administrator.",
  closeDialog: "Got it",
  features: {
    qa: {
      title: "Knowledge Q&A",
      description: "Ask about source standards and find evidence-based answers",
    },
    search: {
      title: "Standards search",
      description: "Find standards by number, title, domain, and status",
    },
    policy: {
      title: "Policy research",
      description: "Explore policies, interpret provisions, and track changes",
    },
    comparison: {
      title: "Standards comparison",
      description: "Compare standards or review changes between editions",
    },
    applicability: {
      title: "Applicability analysis",
      description: "Explore relevant standards for a technical scenario",
    },
    compliance: {
      title: "Compliance review",
      description: "Review project standards and identify validity risks",
    },
    authoring: {
      title: "Standards authoring assistance",
      description: "Drafting and document review",
    },
  },
  footer: "CNOOC SeaStandard · Intelligent standards services",
  copyright: "© 2026–2027 China National Offshore Oil Corporation",
};

export function standardsPortalCopy(locale: Locale) {
  return locale === "en-US" ? enUS : zhCN;
}

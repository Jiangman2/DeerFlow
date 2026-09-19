import type { Agent } from "./types";

export type ExpertAvatarOption = {
  id: string;
  label: string;
  src: string;
};

export const EXPERT_AVATAR_OPTIONS: ExpertAvatarOption[] = [
  {
    id: "exploration-geologist",
    label: "勘探研究",
    src: "/images/digital-employees/cartoon/01-exploration.webp",
  },
  {
    id: "seismic-processing",
    label: "地震解释",
    src: "/images/digital-employees/cartoon/02-seismic.webp",
  },
  {
    id: "reservoir-development",
    label: "油藏开发",
    src: "/images/digital-employees/cartoon/03-reservoir.webp",
  },
  {
    id: "drilling-completion",
    label: "钻完井",
    src: "/images/digital-employees/cartoon/04-drilling.webp",
  },
  {
    id: "offshore-engineering",
    label: "海洋工程",
    src: "/images/digital-employees/cartoon/05-offshore.webp",
  },
  {
    id: "production-operations",
    label: "生产运维",
    src: "/images/digital-employees/cartoon/06-production.webp",
  },
  {
    id: "hse",
    label: "安全环保",
    src: "/images/digital-employees/cartoon/07-hse.webp",
  },
  {
    id: "intelligent-oilfield-data",
    label: "智能油田",
    src: "/images/digital-employees/cartoon/08-digital.webp",
  },
  {
    id: "report-research",
    label: "研究报告",
    src: "/images/digital-employees/cartoon/09-research.webp",
  },
  {
    id: "knowledge-service",
    label: "知识服务",
    src: "/images/digital-employees/cartoon/10-knowledge.webp",
  },
  {
    id: "equipment-maintenance",
    label: "设备维护",
    src: "/images/digital-employees/cartoon/11-maintenance.webp",
  },
  {
    id: "emergency-response",
    label: "应急协调",
    src: "/images/digital-employees/cartoon/12-emergency.webp",
  },
  {
    id: "senior-geologist",
    label: "资深地质",
    src: "/images/digital-employees/cartoon/13-senior-geologist.webp",
  },
  {
    id: "rov-pilot",
    label: "水下机器人",
    src: "/images/digital-employees/cartoon/14-rov-pilot.webp",
  },
  {
    id: "offshore-medic",
    label: "海上医疗",
    src: "/images/digital-employees/cartoon/15-offshore-medic.webp",
  },
  {
    id: "environmental-analyst",
    label: "环境分析",
    src: "/images/digital-employees/cartoon/16-environmental.webp",
  },
  {
    id: "lng-engineer",
    label: "LNG 工艺",
    src: "/images/digital-employees/cartoon/17-lng-engineer.webp",
  },
  {
    id: "ai-scientist",
    label: "人工智能",
    src: "/images/digital-employees/cartoon/18-ai-scientist.webp",
  },
  {
    id: "procurement-specialist",
    label: "合同采购",
    src: "/images/digital-employees/cartoon/19-procurement.webp",
  },
  {
    id: "helicopter-logistics",
    label: "航空物流",
    src: "/images/digital-employees/cartoon/20-helicopter-logistics.webp",
  },
  {
    id: "platform-manager",
    label: "平台管理",
    src: "/images/digital-employees/cartoon/21-platform-manager.webp",
  },
  {
    id: "subsea-inspector",
    label: "海管巡检",
    src: "/images/digital-employees/cartoon/22-subsea-inspector.webp",
  },
  {
    id: "finance-analyst",
    label: "财务分析",
    src: "/images/digital-employees/cartoon/23-finance-analyst.webp",
  },
  {
    id: "renewables-engineer",
    label: "海上新能源",
    src: "/images/digital-employees/cartoon/24-renewables.webp",
  },
];

const EXPERT_AVATAR_STORAGE_KEY = "platform.agent-expert-avatar-map.v1";
const avatarById = new Map(
  EXPERT_AVATAR_OPTIONS.map((avatar) => [avatar.id, avatar]),
);

const externalAvatarIds: Record<string, string> = {
  "https://datalake.cnooc/zywdjxzj": "exploration-geologist",
  "https://datalake.cnooc/dzsjtpzj": "seismic-processing",
  "https://datalake.cnooc/zjbgjszj/": "reservoir-development",
  "https://datalake.cnooc/zdsczj": "production-operations",
  "https://datalake.cnooc/qqyjbgbxzj": "intelligent-oilfield-data",
  "https://cnoocclaw.cnooc.com.cn/zywdjxzj/": "exploration-geologist",
  "https://cnoocclaw.cnooc.com.cn/dzsjtpzj/": "seismic-processing",
  "https://cnoocclaw.cnooc.com.cn/zjbgjszj/": "reservoir-development",
  "https://cnoocclaw.cnooc.com.cn/zdsczj/": "production-operations",
  "https://cnoocclaw.cnooc.com.cn/qqyjbgbxzj/": "intelligent-oilfield-data",
};

const keywordAvatarIds: Array<[RegExp, string]> = [
  [/地震|SEGY/i, "seismic-processing"],
  [/油藏|开发方案/i, "reservoir-development"],
  [/钻井|完井/i, "drilling-completion"],
  [/海洋工程|平台|海管/i, "offshore-engineering"],
  [/生产|运维|采油/i, "production-operations"],
  [/HSE|安全|环保/i, "hse"],
  [/应急|响应|协调/i, "emergency-response"],
  [/设备|维修|维护/i, "equipment-maintenance"],
  [/知识|客服|服务/i, "knowledge-service"],
  [/研究|报告|文档/i, "report-research"],
  [/数据|数采|智能油田/i, "intelligent-oilfield-data"],
  [/地质|勘探/i, "exploration-geologist"],
  [/水下机器人|ROV/i, "rov-pilot"],
  [/医疗|医务|医生/i, "offshore-medic"],
  [/LNG|液化天然气/i, "lng-engineer"],
  [/人工智能|算法|AI/i, "ai-scientist"],
  [/采购|合同|招标/i, "procurement-specialist"],
  [/航空|直升机|物流/i, "helicopter-logistics"],
  [/海管|水下巡检/i, "subsea-inspector"],
  [/财务|预算|成本/i, "finance-analyst"],
  [/新能源|风电|光伏/i, "renewables-engineer"],
];

function hashName(name: string) {
  let hash = 0;
  for (const character of name) {
    hash = (hash * 31 + (character.codePointAt(0) ?? 0)) >>> 0;
  }
  return hash;
}

export function getExpertAvatarById(id: string | null | undefined) {
  return id ? avatarById.get(id) : undefined;
}

export function getDefaultExpertAvatar(
  agent: Pick<Agent, "name" | "external_url" | "avatar_id">,
) {
  const configured = getExpertAvatarById(agent.avatar_id);
  if (configured) return configured;
  const externalId = agent.external_url
    ? externalAvatarIds[agent.external_url.trim()]
    : undefined;
  if (externalId) {
    return avatarById.get(externalId) ?? EXPERT_AVATAR_OPTIONS[0]!;
  }

  const keywordMatch = keywordAvatarIds.find(([pattern]) =>
    pattern.test(agent.name),
  );
  if (keywordMatch) {
    return avatarById.get(keywordMatch[1]) ?? EXPERT_AVATAR_OPTIONS[0]!;
  }

  return EXPERT_AVATAR_OPTIONS[
    hashName(agent.name) % EXPERT_AVATAR_OPTIONS.length
  ]!;
}

export function getStoredExpertAvatarId(agentName: string) {
  if (typeof window === "undefined") return undefined;

  try {
    const raw = window.localStorage.getItem(EXPERT_AVATAR_STORAGE_KEY);
    if (!raw) return undefined;
    const stored = JSON.parse(raw) as Record<string, unknown>;
    const id = stored[agentName];
    return typeof id === "string" && avatarById.has(id) ? id : undefined;
  } catch {
    return undefined;
  }
}

export function saveExpertAvatarSelection(agentName: string, avatarId: string) {
  if (typeof window === "undefined" || !avatarById.has(avatarId)) return;

  try {
    const raw = window.localStorage.getItem(EXPERT_AVATAR_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    window.localStorage.setItem(
      EXPERT_AVATAR_STORAGE_KEY,
      JSON.stringify({ ...parsed, [agentName]: avatarId }),
    );
  } catch {
    // Avatar selection is a presentation enhancement and must not block setup.
  }
}

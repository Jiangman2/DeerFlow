export const MAX_DIGITAL_EMPLOYEE_TAGS = 8;
export const MAX_DIGITAL_EMPLOYEE_TAG_LENGTH = 20;

export function digitalEmployeeTagLength(value: string): number {
  return Array.from(value).length;
}

export function normalizeDigitalEmployeeTags(tags: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const raw of tags) {
    const tag = raw.trim();
    const key = tag.toLocaleLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    normalized.push(tag);
    if (normalized.length >= MAX_DIGITAL_EMPLOYEE_TAGS) break;
  }
  return normalized;
}

const RULES: Array<{ keywords: string[]; tags: string[] }> = [
  {
    keywords: ["地质", "勘探", "油藏", "测井", "seismic", "geology"],
    tags: ["勘探开发", "地质研究"],
  },
  { keywords: ["钻井", "完井", "井控", "drill"], tags: ["钻完井", "工程作业"] },
  { keywords: ["安全", "应急", "hse", "风险"], tags: ["安全环保", "风险管控"] },
  {
    keywords: ["环保", "碳", "新能源", "风电", "environment"],
    tags: ["绿色低碳", "安全环保"],
  },
  {
    keywords: ["合同", "采购", "招标", "供应链"],
    tags: ["经营管理", "合同采购"],
  },
  {
    keywords: ["财务", "预算", "成本", "finance"],
    tags: ["经营管理", "数据分析"],
  },
  {
    keywords: ["报告", "文档", "公文", "ppt", "汇报"],
    tags: ["文档生成", "内容校审"],
  },
  {
    keywords: ["数据", "分析", "报表", "sql", "数字化"],
    tags: ["数据分析", "数字化"],
  },
  {
    keywords: ["检索", "知识", "问答", "research"],
    tags: ["知识检索", "研究分析"],
  },
  {
    keywords: ["运维", "设备", "管线", "平台", "maintenance"],
    tags: ["生产运维", "设备管理"],
  },
  {
    keywords: ["翻译", "英文", "语言", "translate"],
    tags: ["翻译", "内容处理"],
  },
];

const CATEGORY_DEFAULTS: Record<string, string[]> = {
  exploration: ["勘探研究"],
  development: ["开发生产"],
  engineering: ["工程建设"],
  safety: ["安全环保"],
  digital: ["数智技术"],
  research: ["研究与报告"],
  general: ["通用协作"],
};

export function suggestDigitalEmployeeTags(input: {
  name?: string;
  displayName?: string;
  description?: string;
  categoryId?: string | null;
}): string[] {
  const haystack =
    `${input.name ?? ""} ${input.displayName ?? ""} ${input.description ?? ""}`.toLocaleLowerCase();
  const suggestions = [
    ...(input.categoryId ? (CATEGORY_DEFAULTS[input.categoryId] ?? []) : []),
    ...RULES.filter((rule) =>
      rule.keywords.some((keyword) => haystack.includes(keyword)),
    ).flatMap((rule) => rule.tags),
  ];
  return normalizeDigitalEmployeeTags(
    suggestions.length ? suggestions : ["通用协作"],
  );
}

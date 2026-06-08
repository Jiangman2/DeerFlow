export type ShopCartMockFile = {
  id: string;
  filename: string;
  mediaType: string;
  url: string;
  description: string;
  scenario: "neighbor-well" | "production-decline" | "project-ppt";
};

export type CreateMockShopCartFilesResult = {
  files: File[];
  unknownIds: string[];
  failedIds: string[];
};

export const SHOP_CART_MOCK_FILES: ShopCartMockFile[] = [
  {
    id: "pl19-plan-001",
    scenario: "neighbor-well",
    filename: "PL19-A7H调整井部署方案摘录.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    url: "/mock/shop-cart-files/pl19-a7h-adjustment-well-plan.docx",
    description:
      "拟部署调整井目标、设计要点、邻井依据和希望智能体重点研究的问题。",
  },
  {
    id: "pl19-neighbor-002",
    scenario: "neighbor-well",
    filename: "PL19邻井相似度与生产动态数据.xlsx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    url: "/mock/shop-cart-files/pl19-neighbor-well-deepresearch-data.xlsx",
    description:
      "邻井相似度评分、生产递减曲线、层位物性和类比价值说明。",
  },
  {
    id: "pl19-trajectory-003",
    scenario: "neighbor-well",
    filename: "PL19井轨迹与层位对比表.xlsx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    url: "/mock/shop-cart-files/pl19-trajectory-strata-comparison.xlsx",
    description:
      "拟部署井轨迹设计、入窗点、水平段控制点及邻井轨迹对比。",
  },
  {
    id: "pl19-drilling-004",
    scenario: "neighbor-well",
    filename: "PL19邻井钻井日报与完井报告摘编.pdf",
    mediaType: "application/pdf",
    url: "/mock/shop-cart-files/pl19-drilling-completion-daily-summary.pdf",
    description:
      "钻井日报、完井参数、投产事实和对 A7H 的施工参考意义。",
  },
  {
    id: "pl19-incident-005",
    scenario: "neighbor-well",
    filename: "PL19邻井复杂情况记录与复盘.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    url: "/mock/shop-cart-files/pl19-neighbor-well-incident-review.docx",
    description:
      "井漏、托压、气测异常、泥岩垮塌等复杂情况及对拟部署井的启示。",
  },
  {
    id: "prod-decline-001",
    scenario: "production-decline",
    filename: "BZ-19区块单井生产动态日报.pdf",
    mediaType: "application/pdf",
    url: "/mock/shop-cart-files/bz19-well-production-daily.pdf",
    description:
      "区块最新生产日报，包含单井液量、油量、含水和生产提示。",
  },
  {
    id: "prod-decline-002",
    scenario: "production-decline",
    filename: "BZ-19井组递减分析基础数据.xlsx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    url: "/mock/shop-cart-files/bz19-decline-analysis-data.xlsx",
    description:
      "月度生产动态、开停井及措施节点、历史分析成果，用于递减拟合和产量预测。",
  },
  {
    id: "prod-decline-003",
    scenario: "production-decline",
    filename: "BZ-19区块井史与动态分析成果.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    url: "/mock/shop-cart-files/bz19-well-history-analysis.docx",
    description:
      "井史、措施效果、异常点识别口径和区块生产动态认识。",
  },
  {
    id: "scene3-ai-project-report-001",
    scenario: "project-ppt",
    filename: "立项报告_最终修改版.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    url: "/mock/shop-cart-files/scene3-ai-project-report.docx",
    description:
      "用于演示根据计划外人工智能项目立项报告生成高级汇报PPT的场景三资料。",
  },
];

export async function createMockShopCartFiles(
  ids: string[],
): Promise<CreateMockShopCartFilesResult> {
  const filesById = new Map(SHOP_CART_MOCK_FILES.map((file) => [file.id, file]));
  const files: File[] = [];
  const unknownIds: string[] = [];
  const failedIds: string[] = [];

  for (const id of ids) {
    const mockFile = filesById.get(id);
    if (!mockFile) {
      unknownIds.push(id);
      continue;
    }

    try {
      const response = await fetch(mockFile.url);
      if (!response.ok) {
        failedIds.push(id);
        continue;
      }
      const blob = await response.blob();
      files.push(
        new File([blob], mockFile.filename, {
          type: mockFile.mediaType,
        }),
      );
    } catch {
      failedIds.push(id);
    }
  }

  return { files, unknownIds, failedIds };
}

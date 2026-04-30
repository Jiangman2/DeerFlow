export type ShopCartMockFile = {
  id: string;
  filename: string;
  mediaType: string;
  content: string;
};

export type CreateMockShopCartFilesResult = {
  files: File[];
  unknownIds: string[];
};

export const SHOP_CART_MOCK_FILES: ShopCartMockFile[] = [
  {
    id: "file-001",
    filename: "生产日报.pdf",
    mediaType: "application/pdf",
    content: "Mock PDF content for 生产日报.pdf\n",
  },
  {
    id: "file-002",
    filename: "勘探数据.xlsx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    content: "区域,井号,日产量\n渤海,A-01,1280\n南海,B-08,940\n",
  },
  {
    id: "file-003",
    filename: "会议纪要.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    content: "Mock document content for 会议纪要.docx\n",
  },
];

export function createMockShopCartFiles(
  ids: string[],
): CreateMockShopCartFilesResult {
  const filesById = new Map(SHOP_CART_MOCK_FILES.map((file) => [file.id, file]));
  const files: File[] = [];
  const unknownIds: string[] = [];

  for (const id of ids) {
    const mockFile = filesById.get(id);
    if (!mockFile) {
      unknownIds.push(id);
      continue;
    }
    files.push(
      new File([mockFile.content], mockFile.filename, {
        type: mockFile.mediaType,
      }),
    );
  }

  return { files, unknownIds };
}

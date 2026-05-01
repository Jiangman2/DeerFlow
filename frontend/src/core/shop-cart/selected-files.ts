export type ShopCartSelectedFile = {
  fileId: string;
  fileName?: string;
};

export type ParseShopCartSelectedFilesResult =
  | { ok: true; files: ShopCartSelectedFile[] }
  | { ok: false; reason: "invalid_payload" };

function cleanString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

export function parseShopCartSelectedFiles(
  payload: unknown,
): ParseShopCartSelectedFilesResult {
  if (!Array.isArray(payload)) {
    return { ok: false, reason: "invalid_payload" };
  }

  if (payload.every((item) => typeof item === "string")) {
    return {
      ok: true,
      files: payload
        .map((fileId) => cleanString(fileId))
        .filter((fileId): fileId is string => Boolean(fileId))
        .map((fileId) => ({ fileId })),
    };
  }

  const files: ShopCartSelectedFile[] = [];
  for (const item of payload) {
    if (!item || typeof item !== "object") {
      return { ok: false, reason: "invalid_payload" };
    }
    const record = item as Record<string, unknown>;
    const fileId = cleanString(record.fileId) ?? cleanString(record.id);
    if (!fileId) {
      return { ok: false, reason: "invalid_payload" };
    }
    const fileName = cleanString(record.fileName) ?? cleanString(record.name);
    files.push(fileName ? { fileId, fileName } : { fileId });
  }

  return { ok: true, files };
}

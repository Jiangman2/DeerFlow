export type NewShopCartFile = {
  id: string;
  fileId: string | null;
  userId: string;
  isComplete: number;
  createDate: string;
  fileName: string;
  type: number;
  ptCode: string;
  isStore: number;
  dacCode: string | null;
  num: number | null;
  remark: string | null;
  tipsInfo: string | null;
  extra: string | null;
  errorInfo: string | null;
  shopType: number;
};

export type DownloadableNewShopCartFile = NewShopCartFile & {
  fileId: string;
  isComplete: 1;
  num: number;
};

type ParseNewShopCartListResponseResult =
  | { ok: true; files: NewShopCartFile[] }
  | { ok: false; reason: "invalid_payload" | "upstream_error" };

type ParseNewShopCartFileResult =
  | { ok: true; file: DownloadableNewShopCartFile }
  | { ok: false; reason: "invalid_payload" };

function isNewShopCartFile(value: unknown): value is NewShopCartFile {
  if (!value || typeof value !== "object") {
    return false;
  }

  const record = value as Record<string, unknown>;
  const hasDownloadFields =
    typeof record.fileId === "string" && typeof record.num === "number";
  const hasPendingDownloadFields =
    record.fileId === null && record.num === null;
  const hasValidDownloadState =
    record.isComplete === 1 ? hasDownloadFields : hasPendingDownloadFields;

  return (
    typeof record.id === "string" &&
    typeof record.userId === "string" &&
    (record.isComplete === 0 || record.isComplete === 1) &&
    hasValidDownloadState &&
    typeof record.createDate === "string" &&
    typeof record.fileName === "string" &&
    typeof record.type === "number" &&
    typeof record.ptCode === "string" &&
    typeof record.isStore === "number" &&
    (typeof record.extra === "string" || record.extra === null) &&
    typeof record.shopType === "number"
  );
}

export function isDownloadableNewShopCartFile(
  value: NewShopCartFile,
): value is DownloadableNewShopCartFile {
  return (
    value.isComplete === 1 &&
    typeof value.fileId === "string" &&
    typeof value.num === "number"
  );
}

export function parseNewShopCartListResponse(
  payload: unknown,
): ParseNewShopCartListResponseResult {
  if (!payload || typeof payload !== "object") {
    return { ok: false, reason: "invalid_payload" };
  }

  const record = payload as Record<string, unknown>;
  if (record.code !== 0) {
    return { ok: false, reason: "upstream_error" };
  }

  if (!Array.isArray(record.data) || !record.data.every(isNewShopCartFile)) {
    return { ok: false, reason: "invalid_payload" };
  }

  return { ok: true, files: record.data };
}

export function parseNewShopCartFile(
  payload: unknown,
): ParseNewShopCartFileResult {
  if (!isNewShopCartFile(payload) || !isDownloadableNewShopCartFile(payload)) {
    return { ok: false, reason: "invalid_payload" };
  }

  return { ok: true, file: payload };
}

const FILE_TYPE_BY_EXTENSION: Record<string, string> = {
  csv: "text/csv",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  pdf: "application/pdf",
  png: "image/png",
  txt: "text/plain",
  xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  zip: "application/zip",
};

export function getFileTypeFromName(fileName: string) {
  const extension = fileName.trim().split(".").pop()?.toLowerCase();
  if (!extension) {
    return "application/octet-stream";
  }

  return FILE_TYPE_BY_EXTENSION[extension] ?? "application/octet-stream";
}

export function createNewShopCartFile(
  blob: Blob,
  {
    fileName,
  }: {
    fileId: string;
    fileName: string;
  },
) {
  return new File([blob], fileName, {
    type: getFileTypeFromName(fileName),
  });
}

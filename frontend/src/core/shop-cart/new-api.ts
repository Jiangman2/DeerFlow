import {
  createNewShopCartFile,
  type DownloadableNewShopCartFile,
  type NewShopCartFile,
} from "./api-files";

export type FetchNewShopCartFilesResult =
  | { ok: true; files: NewShopCartFile[] }
  | {
      ok: false;
      status?: number;
      code?: "data_lake_login_required";
      message: string;
    };

export type DownloadNewShopCartFileResult =
  | { ok: true; file: File }
  | { ok: false; fileId: string; status?: number; message: string };

export async function fetchNewShopCartFiles(): Promise<FetchNewShopCartFilesResult> {
  try {
    const response = await fetch("/api/shop-cart/new/list");
    if (!response.ok) {
      const payload: unknown = await response.json().catch(() => null);
      if (
        payload !== null &&
        typeof payload === "object" &&
        "code" in payload &&
        payload.code === "data_lake_login_required" &&
        "detail" in payload &&
        typeof payload.detail === "string"
      ) {
        return {
          ok: false,
          status: response.status,
          code: "data_lake_login_required",
          message: payload.detail,
        };
      }
      return {
        ok: false,
        status: response.status,
        message: `List failed with status ${response.status}`,
      };
    }

    const payload: unknown = await response.json();
    if (
      !payload ||
      typeof payload !== "object" ||
      !Array.isArray((payload as { files?: unknown }).files)
    ) {
      return { ok: false, message: "List returned invalid data" };
    }

    return {
      ok: true,
      files: (payload as { files: NewShopCartFile[] }).files,
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "List failed",
    };
  }
}

export async function downloadNewShopCartFile(
  shopCartFile: DownloadableNewShopCartFile,
): Promise<DownloadNewShopCartFileResult> {
  try {
    const response = await fetch("/api/shop-cart/new/download", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(shopCartFile),
    });
    if (!response.ok) {
      return {
        ok: false,
        fileId: shopCartFile.fileId,
        status: response.status,
        message: `Download failed with status ${response.status}`,
      };
    }

    const blob = await response.blob();
    return {
      ok: true,
      file: createNewShopCartFile(blob, shopCartFile),
    };
  } catch (error) {
    return {
      ok: false,
      fileId: shopCartFile.fileId,
      message: error instanceof Error ? error.message : "Download failed",
    };
  }
}

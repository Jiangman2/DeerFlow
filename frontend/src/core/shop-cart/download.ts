import { chooseShopCartFilename } from "./filenames";
import type { ShopCartSelectedFile } from "./selected-files";
import { convertShopCartBlobToXlsxFile } from "./xlsx";

export type DownloadShopCartFileResult =
  | { ok: true; file: File }
  | { ok: false; fileId: string; status?: number; message: string };

export async function downloadShopCartFile(
  selectedFile: ShopCartSelectedFile,
): Promise<DownloadShopCartFileResult> {
  try {
    const response = await fetch(
      `/api/shop-cart/download?fileId=${encodeURIComponent(selectedFile.fileId)}`,
    );
    if (!response.ok) {
      return {
        ok: false,
        fileId: selectedFile.fileId,
        status: response.status,
        message: `Download failed with status ${response.status}`,
      };
    }

    const blob = await response.blob();
    const xlsxFile = await convertShopCartBlobToXlsxFile(blob);
    if (xlsxFile) {
      return {
        ok: true,
        file: xlsxFile,
      };
    }

    const filename = chooseShopCartFilename({
      contentDisposition: response.headers.get("content-disposition"),
      fileId: selectedFile.fileId,
      iframeFileName: selectedFile.fileName,
    });

    return {
      ok: true,
      file: new File([blob], filename, {
        type: response.headers.get("content-type") ?? blob.type,
      }),
    };
  } catch (error) {
    return {
      ok: false,
      fileId: selectedFile.fileId,
      message: error instanceof Error ? error.message : "Download failed",
    };
  }
}

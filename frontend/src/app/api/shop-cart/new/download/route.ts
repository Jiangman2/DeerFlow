import { NextResponse, type NextRequest } from "next/server";

import { parseNewShopCartFile } from "@/core/shop-cart/api-files";
import { env } from "@/env";
import {
  CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
  resolveCnoocPlatformToken,
} from "@/server/cnooc-auth/platform-token";
import {
  getNewShopCartApiBaseUrl,
  requestDataLakeBinary,
} from "@/server/shop-cart/new-api";

const NEW_SHOP_CART_DOWNLOAD_PATH =
  "/jretrieve/bd_search/external-download/batch/download";

export async function POST(request: NextRequest) {
  const payload: unknown = await request.json().catch(() => null);
  const result = parseNewShopCartFile(payload);
  if (!result.ok) {
    return NextResponse.json(
      { detail: "A valid shop cart file is required" },
      { status: 400 },
    );
  }

  const url = new URL(
    `${getNewShopCartApiBaseUrl()}${NEW_SHOP_CART_DOWNLOAD_PATH}`,
  );
  const token = resolveCnoocPlatformToken(
    request.cookies.get(CNOOC_PLATFORM_TOKEN_COOKIE_NAME)?.value,
    env.SHOP_CART_DOWNLOAD_TOKEN,
  );

  let response: Response;
  try {
    response = await requestDataLakeBinary(url, {
      body: [result.file],
      token,
    });
  } catch (error) {
    console.error("Failed to reach new shop cart download service", {
      error,
      fileId: result.file.fileId,
      url: url.toString(),
    });
    return NextResponse.json(
      { detail: "Failed to reach new shop cart download service" },
      { status: 502 },
    );
  }

  if (!response.ok) {
    return NextResponse.json(
      { detail: "New shop cart download failed" },
      { status: response.status },
    );
  }

  const headers = new Headers();
  const contentType = response.headers.get("content-type");
  const contentDisposition = response.headers.get("content-disposition");
  if (contentType) headers.set("content-type", contentType);
  if (contentDisposition) {
    headers.set("content-disposition", contentDisposition);
  }

  return new Response(response.body, {
    status: response.status,
    headers,
  });
}

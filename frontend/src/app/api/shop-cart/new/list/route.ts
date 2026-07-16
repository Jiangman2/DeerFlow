import { NextResponse, type NextRequest } from "next/server";

import { parseNewShopCartListResponse } from "@/core/shop-cart/api-files";
import { env } from "@/env";
import {
  CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
  resolveCnoocPlatformToken,
} from "@/server/cnooc-auth/platform-token";
import {
  getNewShopCartApiBaseUrl,
  requestDataLakeJson,
} from "@/server/shop-cart/new-api";

const NEW_SHOP_CART_LIST_PATH = "/jretrieve/bd_search/result-inquiry/list";

export async function GET(request: NextRequest) {
  const url = new URL(`${getNewShopCartApiBaseUrl()}${NEW_SHOP_CART_LIST_PATH}`);
  const token = resolveCnoocPlatformToken(
    request.cookies.get(CNOOC_PLATFORM_TOKEN_COOKIE_NAME)?.value,
    env.SHOP_CART_DOWNLOAD_TOKEN,
  );

  let response: Response;
  try {
    response = await requestDataLakeJson(url, token);
  } catch (error) {
    console.error("Failed to reach new shop cart list service", {
      error,
      url: url.toString(),
    });
    return NextResponse.json(
      { detail: "Failed to reach new shop cart list service" },
      { status: 502 },
    );
  }

  if (!response.ok) {
    return NextResponse.json(
      { detail: "New shop cart list failed" },
      { status: response.status },
    );
  }

  const payload: unknown = await response.json();
  const result = parseNewShopCartListResponse(payload);
  if (!result.ok) {
    return NextResponse.json(
      { detail: "New shop cart list returned invalid data" },
      { status: 502 },
    );
  }

  return NextResponse.json({ files: result.files });
}

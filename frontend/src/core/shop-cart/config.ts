import { env } from "@/env";

import { resolveShopCartMode } from "./mode";

export function getShopCartMode() {
  return resolveShopCartMode(env.NEXT_PUBLIC_SHOP_CART_MODE);
}

export function getShopCartFrameConfig(currentOrigin: string) {
  const iframeUrl = env.NEXT_PUBLIC_SHOP_CART_IFRAME_URL?.trim();
  const allowedOrigin = env.NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN?.trim();

  if (!iframeUrl) return null;

  return {
    iframeUrl,
    allowedOrigin: allowedOrigin ?? currentOrigin,
    isMockMode: false,
  };
}

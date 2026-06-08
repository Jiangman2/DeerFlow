import { env } from "@/env";

export const SHOP_CART_MOCK_IFRAME_URL = "/mock/shop-cart-iframe";

export function getShopCartFrameConfig(currentOrigin: string) {
  const iframeUrl = env.NEXT_PUBLIC_SHOP_CART_IFRAME_URL?.trim();
  const allowedOrigin = env.NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN?.trim();

  return {
    iframeUrl: iframeUrl ?? SHOP_CART_MOCK_IFRAME_URL,
    allowedOrigin: allowedOrigin ?? currentOrigin,
    isMockMode: !iframeUrl,
  };
}

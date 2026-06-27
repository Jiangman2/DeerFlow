export type ShopCartMode = "legacy" | "api";

export function resolveShopCartMode(value: string | undefined): ShopCartMode {
  return value?.trim() === "api" ? "api" : "legacy";
}

export type ShopCartMode = "legacy" | "api";

export function resolveShopCartMode(value: string | undefined): ShopCartMode {
  return value?.trim() === "legacy" ? "legacy" : "api";
}

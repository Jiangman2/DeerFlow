import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { resolveShopCartMode } from "@/core/shop-cart/mode";

void test("uses api mode when configured explicitly", () => {
  assert.equal(resolveShopCartMode("api"), "api");
  assert.equal(resolveShopCartMode(" api "), "api");
});

void test("uses legacy mode only when configured explicitly", () => {
  assert.equal(resolveShopCartMode("legacy"), "legacy");
  assert.equal(resolveShopCartMode(" legacy "), "legacy");
});

void test("falls back to api mode for empty or unknown values", () => {
  assert.equal(resolveShopCartMode(undefined), "api");
  assert.equal(resolveShopCartMode(""), "api");
  assert.equal(resolveShopCartMode("iframe"), "api");
});

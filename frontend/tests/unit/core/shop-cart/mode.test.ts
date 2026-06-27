import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { resolveShopCartMode } from "@/core/shop-cart/mode";

void test("uses api mode when configured explicitly", () => {
  assert.equal(resolveShopCartMode("api"), "api");
  assert.equal(resolveShopCartMode(" api "), "api");
});

void test("falls back to legacy mode for empty or unknown values", () => {
  assert.equal(resolveShopCartMode(undefined), "legacy");
  assert.equal(resolveShopCartMode(""), "legacy");
  assert.equal(resolveShopCartMode("iframe"), "legacy");
});

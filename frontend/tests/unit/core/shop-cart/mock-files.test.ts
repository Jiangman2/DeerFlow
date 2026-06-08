import assert from "node:assert/strict";
import { test } from "vitest";

const { createMockShopCartFiles, SHOP_CART_MOCK_FILES } = await import(
  new URL("../../../../src/core/shop-cart/mock-files.ts", import.meta.url).href
);

void test("creates File objects for known shop cart IDs and reports unknown IDs", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async () => new Response(new Blob(["mock file body"]));

  try {
    const result = await createMockShopCartFiles([
      "pl19-plan-001",
      "missing-id",
      "pl19-neighbor-002",
    ]);

    assert.deepEqual(
      result.files.map((file: File) => file.name),
      [
        "PL19-A7H调整井部署方案摘录.docx",
        "PL19邻井相似度与生产动态数据.xlsx",
      ],
    );
    assert.deepEqual(result.unknownIds, ["missing-id"]);
    assert.deepEqual(result.failedIds, []);
    assert.equal(
      result.files[0]?.type,
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
    assert.equal(
      result.files[1]?.type,
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

void test("keeps demonstration scenarios in the mock shop cart", () => {
  const scenarios = new Set(SHOP_CART_MOCK_FILES.map((file) => file.scenario));

  assert.deepEqual(
    [...scenarios].sort(),
    ["neighbor-well", "production-decline", "project-ppt"],
  );
});

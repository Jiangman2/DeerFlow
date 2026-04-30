import assert from "node:assert/strict";
import test from "node:test";

const { createMockShopCartFiles } = await import(
  new URL("./mock-files.ts", import.meta.url).href
);

void test("creates File objects for known shop cart IDs and reports unknown IDs", () => {
  const result = createMockShopCartFiles(["file-001", "missing-id", "file-002"]);

  assert.deepEqual(
    result.files.map((file: File) => file.name),
    ["生产日报.pdf", "勘探数据.xlsx"],
  );
  assert.deepEqual(result.unknownIds, ["missing-id"]);
  assert.equal(result.files[0]?.type, "application/pdf");
  assert.equal(
    result.files[1]?.type,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
});

import assert from "node:assert/strict";
import { describe, it } from "vitest";

const { mockCheckCnoocToken } = await import(
  new URL("../../../../src/server/cnooc-auth/mock-token-check.ts", import.meta.url)
    .href
);

describe("mockCheckCnoocToken", () => {
  it("rejects blank tokens", async () => {
    await assert.rejects(() => mockCheckCnoocToken("   "), /token is required/);
  });

  it("returns a stable identity for the same non-empty token", async () => {
    const first = await mockCheckCnoocToken("platform-token-a");
    const second = await mockCheckCnoocToken("platform-token-a");

    assert.deepEqual(second.user, first.user);
    assert.match(first.user.deerflowUserId, /^cnooc_/);
    assert.match(first.user.email, /@mock\.cnooc\.local$/);
    assert.ok(first.roles.length > 0);
    assert.ok(first.department.name);
  });

  it("returns different users for different tokens", async () => {
    const first = await mockCheckCnoocToken("platform-token-a");
    const second = await mockCheckCnoocToken("platform-token-b");

    assert.notEqual(second.user.deerflowUserId, first.user.deerflowUserId);
    assert.notEqual(second.user.email, first.user.email);
  });
});

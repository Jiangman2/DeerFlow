import assert from "node:assert/strict";
import { describe, it } from "node:test";

const { buildCnoocLoginRedirectUrl } = await import(
  new URL("./route-helpers.ts", import.meta.url).href
);

describe("buildCnoocLoginRedirectUrl", () => {
  it("prefers BETTER_AUTH_URL over the dev server bind host", () => {
    const previous = process.env.BETTER_AUTH_URL;
    process.env.BETTER_AUTH_URL = "http://10.77.78.224:3000";
    try {
      const redirectUrl = buildCnoocLoginRedirectUrl(
        "http://0.0.0.0:3000/oauth/cnooc/login?token=t",
      );

      assert.equal(
        redirectUrl.toString(),
        "http://10.77.78.224:3000/workspace/chats/new",
      );
    } finally {
      if (previous === undefined) {
        delete process.env.BETTER_AUTH_URL;
      } else {
        process.env.BETTER_AUTH_URL = previous;
      }
    }
  });
});

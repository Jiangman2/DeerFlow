import { afterEach, expect, rs, test } from "@rstest/core";

import { fetchPptConfirmationEnabled } from "@/core/features/api";

afterEach(() => {
  rs.unstubAllGlobals();
});

test.each([
  [undefined, false],
  [{ enabled: true }, false],
  [{ enabled: false, protocol: "ppt-confirmation-v1" }, false],
  [{ enabled: "true", protocol: "ppt-confirmation-v1" }, false],
  [{ enabled: true, protocol: "future" }, false],
  [{ enabled: true, protocol: "ppt-confirmation-v1" }, true],
])(
  "availability requires an exact server protocol: %j",
  async (feature, expected) => {
    rs.stubGlobal(
      "fetch",
      rs.fn(
        async () => new Response(JSON.stringify({ ppt_confirmation: feature })),
      ),
    );
    expect(await fetchPptConfirmationEnabled()).toBe(expected);
  },
);

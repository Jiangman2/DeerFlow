import { describe, expect, it } from "@rstest/core";

import { LEAD_AGENT_RECURSION_LIMIT } from "@/core/threads/run-config";

describe("lead-agent run config", () => {
  it("provides enough graph super-steps for progressing multi-stage workflows", () => {
    expect(LEAD_AGENT_RECURSION_LIMIT).toBe(2000);
  });
});

import { describe, expect, test } from "@rstest/core";

import {
  buildCnoocExternalAgentTargetUrl,
  CNOOC_EXTERNAL_AGENT_TARGETS,
  isCnoocExternalAgentKey,
} from "@/server/cnooc-auth/external-agent-launch";

describe("CNOOC external-agent launch", () => {
  test("keeps the launch target on the fixed data-lake allowlist", () => {
    expect(CNOOC_EXTERNAL_AGENT_TARGETS).toEqual({
      zywdjxzj: "https://datalake.cnooc/zywdjxzj",
      qqyjbgbxzj: "https://datalake.cnooc/qqyjbgbxzj",
      zjbgjszj: "https://datalake.cnooc/zjbgjszj/",
      zdsczj: "https://datalake.cnooc/zdsczj",
      dzsjtpzj: "https://datalake.cnooc/dzsjtpzj",
    });
    expect(isCnoocExternalAgentKey("zywdjxzj")).toBe(true);
    expect(isCnoocExternalAgentKey("unknown-agent")).toBe(false);
  });

  test("adds the current platform token only while building the server redirect", () => {
    const token =
      "platform-example!token-that-must-never-be-committed-to-card-source";
    const target = buildCnoocExternalAgentTargetUrl("zjbgjszj", token);

    expect(target.origin).toBe("https://datalake.cnooc");
    expect(target.pathname).toBe("/zjbgjszj/");
    expect(target.searchParams.get("token")).toBe(token);
  });

  test("rejects empty or control-character tokens", () => {
    expect(() => buildCnoocExternalAgentTargetUrl("zywdjxzj", "   ")).toThrow(
      "platform token is missing",
    );
    expect(() =>
      buildCnoocExternalAgentTargetUrl("zywdjxzj", "bad\ntoken"),
    ).toThrow("platform token is invalid");
  });
});

/**
 * Tests for the error-classification behaviour of `checkAgentName`.
 *
 * Issue #3041: when the backend returns a non-200 response (e.g. a 500 with
 * a database error, a 422 from misbehaving routing, or any other 4xx/5xx
 * not in the 502/503/504 set), the UI used to swallow the backend detail
 * into a generic "Could not verify name availability" fallback because the
 * page-level catch block only handled `reason === "backend_unreachable"`.
 *
 * The fix carries the raw backend detail as `AgentNameCheckError.detail`
 * (distinct from `message`, which always has a non-empty value because
 * `checkAgentName` substitutes a generated fallback when the backend sent
 * no detail). The UI uses `detail` to decide whether to surface a real
 * backend string or fall back to the localised "could not verify" copy.
 *
 * These tests pin both halves of the contract so a future refactor doesn't
 * silently drop the detail or leak the generated fallback into the UI.
 */
import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import {
  AgentLoadError,
  AgentsApiDisabledError,
  checkAgentName,
  deleteAgent,
  deleteAgentAvatar,
  getAgent,
  publishAgent,
  recordExternalAgentClick,
  shouldRetryAgentLoad,
  unpublishTeamAgent,
  updateAgent,
  uploadAgentAvatar,
} from "@/core/agents/api";
import { fetch as fetcher } from "@/core/api/fetcher";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

test("employee deletion requires the reviewed revision and does not retry conflicts", async () => {
  await expect(deleteAgent("reports", "")).rejects.toMatchObject({ status: 428 });
  expect(mockedFetch).not.toHaveBeenCalled();
  mockedFetch.mockResolvedValueOnce(jsonResponse(409, { detail: "Employee changed" }));
  await expect(deleteAgent("reports", "draft-reviewed")).rejects.toMatchObject({ status: 409 });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
  expect(mockedFetch).toHaveBeenCalledWith("/api/agents/reports?expected_draft_revision=draft-reviewed", { method: "DELETE" });
});

test("avatar writes bind to the saved draft and propagate conflicts without retry", async () => {
  const image = new File(["image"], "portrait.png", { type: "image/png" });
  mockedFetch.mockResolvedValueOnce(jsonResponse(409, { detail: "Draft changed" }));
  await expect(uploadAgentAvatar("reports", image, "draft-saved")).rejects.toMatchObject({ status: 409 });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
  expect(mockedFetch.mock.calls[0]?.[0]).toBe("/api/agents/reports/avatar?expected_draft_revision=draft-saved");
  mockedFetch.mockResolvedValueOnce(jsonResponse(200, { name: "reports", draft_revision: "draft-removed" }));
  await expect(deleteAgentAvatar("reports", "draft-saved")).resolves.toMatchObject({ draft_revision: "draft-removed" });
  expect(mockedFetch.mock.calls[1]?.[0]).toBe("/api/agents/reports/avatar?expected_draft_revision=draft-saved");
});

test("avatar requests cannot silently drop their revision", async () => {
  await expect(deleteAgentAvatar("reports", "")).rejects.toMatchObject({ status: 428 });
  await expect(uploadAgentAvatar("reports", new File(["a"], "a.png"), "")).rejects.toMatchObject({ status: 428 });
  expect(mockedFetch).not.toHaveBeenCalled();
});

test("draft save forwards the observed revision and exposes a conflict without retry", async () => {
  mockedFetch.mockResolvedValueOnce(jsonResponse(409, { detail: "Draft changed" }));
  await expect(updateAgent("reports", { description: "local", expected_draft_revision: "draft-observed" })).rejects.toMatchObject({ status: 409 });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
  expect(mockedFetch.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ description: "local", expected_draft_revision: "draft-observed" }));
});

test("employee reads explicitly identify the Square source", async () => {
  mockedFetch.mockResolvedValueOnce(
    jsonResponse(200, { name: "reports", source: "team" }),
  );
  await getAgent("reports", "team");
  expect(mockedFetch).toHaveBeenCalledWith("/api/agents/reports?source=team");
});

test("publication replacement sends the observed revision, not an unconditional overwrite", async () => {
  mockedFetch.mockResolvedValueOnce(jsonResponse(200, { name: "reports" }));
  await publishAgent("reports", "observed-version", "draft-reviewed");
  expect(mockedFetch).toHaveBeenCalledWith("/api/agents/reports/publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      overwrite: true,
      expected_revision: "observed-version",
      expected_draft_revision: "draft-reviewed",
      grant_ids: [],
    }),
  });
});

test("withdrawal is scoped to the exact observed revision", async () => {
  mockedFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await unpublishTeamAgent("reports", "observed-version");
  expect(mockedFetch).toHaveBeenCalledWith(
    "/api/team-agents/reports?expected_revision=observed-version",
    { method: "DELETE" },
  );
});

test("publication conflict surfaces without retry or silent overwrite", async () => {
  mockedFetch.mockResolvedValueOnce(
    jsonResponse(409, { detail: "Publication changed; reload" }),
  );
  await expect(publishAgent("reports", "stale-version")).rejects.toMatchObject({
    status: 409,
  });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
});

test.each([403, 404, 410])(
  "HTTP %i never silently falls back or retries the employee read",
  async (status) => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(status, {}));
    await expect(getAgent("reports", "team")).rejects.toMatchObject({ status });
    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(shouldRetryAgentLoad(0, new AgentLoadError(status))).toBe(false);
  },
);

test("transient employee fetch failures get only one bounded retry", () => {
  expect(shouldRetryAgentLoad(0, new AgentLoadError(503))).toBe(true);
  expect(shouldRetryAgentLoad(1, new AgentLoadError(503))).toBe(false);
});

describe("checkAgentName", () => {
  test("returns availability payload on 200", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { available: true, name: "dealagent" }),
    );
    const result = await checkAgentName("dealagent");
    expect(result).toEqual({ available: true, name: "dealagent" });
  });

  test("treats network-layer fetch rejection as backend_unreachable", async () => {
    mockedFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(checkAgentName("dealagent")).rejects.toMatchObject({
      name: "AgentNameCheckError",
      reason: "backend_unreachable",
    });
  });

  test.each([502, 503, 504])(
    "treats HTTP %i as backend_unreachable",
    async (status) => {
      mockedFetch.mockResolvedValueOnce(
        jsonResponse(status, { detail: "Bad Gateway" }),
      );
      await expect(checkAgentName("dealagent")).rejects.toMatchObject({
        name: "AgentNameCheckError",
        reason: "backend_unreachable",
      });
    },
  );

  test("recognises agents_api disabled detail and throws AgentsApiDisabledError", async () => {
    const detail =
      "Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP.";
    mockedFetch.mockResolvedValueOnce(jsonResponse(403, { detail }));
    await expect(checkAgentName("dealagent")).rejects.toBeInstanceOf(
      AgentsApiDisabledError,
    );
  });

  test("carries backend 422 detail through AgentNameCheckError.detail (issue #3041)", async () => {
    // This is the exact response shape produced by `_validate_agent_name`
    // when the user submits a name with disallowed characters — e.g. a
    // trailing space, a dot, a Chinese character, or invisible whitespace
    // pasted in from another window.
    const detail =
      "Invalid agent name 'deal agent'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).";
    mockedFetch.mockResolvedValueOnce(jsonResponse(422, { detail }));

    await expect(checkAgentName("deal agent")).rejects.toMatchObject({
      name: "AgentNameCheckError",
      reason: "request_failed",
      // The full detail is preserved on both `detail` (for the UI to
      // recognise "real backend detail vs generated fallback") and
      // `message` (for stack traces / logs).
      detail,
      message: detail,
    });
  });

  test("falls back to statusText in message but leaves detail null when backend returns no detail", async () => {
    // The fallback message must NOT mask the absence of a real backend
    // detail — the page-level catch relies on `detail === null` to choose
    // the localised generic fallback rather than rendering the bare
    // "Failed to check agent name: Internal Server Error" string.
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 500, statusText: "Internal Server Error" }),
    );
    await expect(checkAgentName("dealagent")).rejects.toMatchObject({
      name: "AgentNameCheckError",
      reason: "request_failed",
      detail: null,
      message: expect.stringContaining("Internal Server Error"),
    });
  });

  test("treats non-string detail as null (defence against future schema drift)", async () => {
    // If the backend ever returns `{detail: {code, message}}` (the shape
    // used by auth errors today) on this endpoint, we must not surface a
    // `[object Object]` string. `detail` should fall back to null so the
    // page uses its localised fallback.
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(500, { detail: { code: "x", message: "y" } }),
    );
    await expect(checkAgentName("dealagent")).rejects.toMatchObject({
      name: "AgentNameCheckError",
      reason: "request_failed",
      detail: null,
    });
  });

  test("does not misclassify a 422 with unrelated detail as agents_api disabled", async () => {
    // Defence-in-depth: the disabled detector matches on the substring
    // "agents_api.enabled", so a 422 whose detail accidentally contains
    // the same substring would be misclassified. The validation detail
    // produced by `_validate_agent_name` never contains it; this test
    // simply asserts that "Invalid agent name ..." stays in the
    // request_failed branch, which is where the page now surfaces it.
    const detail =
      "Invalid agent name 'deal.agent'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).";
    mockedFetch.mockResolvedValueOnce(jsonResponse(422, { detail }));
    await expect(checkAgentName("deal.agent")).rejects.not.toBeInstanceOf(
      AgentsApiDisabledError,
    );
  });
});

describe("recordExternalAgentClick", () => {
  test("uses the authenticated fetch wrapper for CSRF-protected click reporting", async () => {
    mockedFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(
      recordExternalAgentClick("专业文档解析专家"),
    ).resolves.toBeUndefined();

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/agent-analytics/external-click",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_name: "专业文档解析专家" }),
        keepalive: true,
      },
    );
  });

  test("surfaces a failed click report so the card can swallow only telemetry errors", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(403, { detail: "CSRF validation failed" }),
    );

    await expect(recordExternalAgentClick("专业文档解析专家")).rejects.toThrow(
      "Failed to record external agent click: 403",
    );
  });
});

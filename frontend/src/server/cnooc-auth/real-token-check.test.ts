import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";

const {
  CnoocAuthError,
  buildCnoocQueryPayload,
  buildCnoocUrl,
  createCnoocAuthClient,
  mapCnoocUserInfo,
  resetCnoocAppCodeCache,
} = await import(new URL("./real-token-check.ts", import.meta.url).href);

const config = {
  baseUrl: "http://10.77.78.223",
  appName: "DApi",
  appId: "880CADF172AC4ABD8864440804EE216F",
  tokenCheckApiToken: "token-check-secret",
  tokenCheckLoginUserCode: "dengxueliang",
  userInfoApiToken: "user-info-secret",
};

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

function successfulUserInfo(overrides: Record<string, unknown> = {}) {
  return {
    userid: "CNOOC-001",
    username: "chengzw",
    ryxm: "Cheng Ziwei",
    ...overrides,
  };
}

function isCnoocAuthError(
  error: unknown,
  status: number,
  messagePattern: RegExp,
) {
  if (!(error instanceof CnoocAuthError)) return false;
  const authError = error as { status: number; message: string };
  return authError.status === status && messagePattern.test(authError.message);
}

describe("CNOOC real token check helpers", () => {
  beforeEach(() => {
    resetCnoocAppCodeCache();
  });

  it("builds raw JSON query payloads for platform APIs", () => {
    assert.equal(
      buildCnoocQueryPayload("zhId", "token-value"),
      JSON.stringify({ jsonObj: { zhId: "token-value" } }),
    );
    assert.equal(
      buildCnoocQueryPayload("token", "token-value"),
      JSON.stringify({ jsonObj: { token: "token-value" } }),
    );
  });

  it("builds URLs with percent-encoded query JSON", () => {
    const url = buildCnoocUrl(config.baseUrl, "/apigateway/zhy/tokenCheck", {
      appCode: "app-code",
      query: buildCnoocQueryPayload("token", "platform-token"),
    });

    assert.equal(
      url.toString(),
      "http://10.77.78.223/apigateway/zhy/tokenCheck?appCode=app-code&query=%7B%22jsonObj%22%3A%7B%22token%22%3A%22platform-token%22%7D%7D",
    );
  });

  it("maps CNOOC user info to DeerFlow SSO user identity", () => {
    const result = mapCnoocUserInfo(successfulUserInfo());

    assert.equal(result.user.platformUserId, "CNOOC-001");
    assert.equal(result.user.deerflowUserId, "cnooc_CNOOC-001");
    assert.equal(result.user.email, "chengzw@cnooc.com.cn");
    assert.equal(result.user.name, "Cheng Ziwei");
    assert.equal(result.provider, "cnooc");
    assert.equal(result.ok, true);
  });

  it("rejects user info missing required identity fields", () => {
    assert.throws(
      () => mapCnoocUserInfo(successfulUserInfo({ ryxm: "" })),
      (error) => isCnoocAuthError(error, 502, /missing required user field/),
    );
  });
});

describe("createCnoocAuthClient", () => {
  beforeEach(() => {
    resetCnoocAppCodeCache();
  });

  it("checks token and fetches user info through CNOOC APIs", async () => {
    const calls: Array<{ url: URL; headers: Headers }> = [];
    const fetchImpl = async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input.toString());
      calls.push({ url, headers: new Headers(init?.headers) });

      if (url.pathname === "/apigateway/appauth/getappid") {
        assert.equal(url.searchParams.get("timeStamp"), "1700000000");
        return jsonResponse({ code: 200, msg: "success", data: "app-code-1" });
      }
      if (url.pathname === "/apigateway/kzxmw/zcjksjfw_9543") {
        assert.equal(url.searchParams.get("appCode"), "app-code-1");
        assert.equal(
          url.searchParams.get("query"),
          JSON.stringify({ jsonObj: { zhId: "platform-token" } }),
        );
        assert.equal(calls.at(-1)?.headers.get("apiToken"), "token-check-secret");
        assert.equal(calls.at(-1)?.headers.get("loginUserCode"), "dengxueliang");
        return jsonResponse({ code: 200, msg: "ok", bizCode: null, dataSize: 0, data: [] });
      }
      if (url.pathname === "/apigateway/zhy/tokenCheck") {
        assert.equal(url.searchParams.get("appCode"), "app-code-1");
        assert.equal(
          url.searchParams.get("query"),
          JSON.stringify({ jsonObj: { token: "platform-token" } }),
        );
        assert.equal(calls.at(-1)?.headers.get("apiToken"), "user-info-secret");
        return jsonResponse({
          code: 200,
          msg: "ok",
          bizCode: "ok",
          dataSize: 1,
          data: successfulUserInfo(),
        });
      }
      throw new Error(`unexpected URL ${url.toString()}`);
    };

    const client = createCnoocAuthClient({
      config,
      fetchImpl,
      now: () => 1_700_000_000_000,
    });
    const result = await client.checkCnoocToken("platform-token");

    assert.equal(result.user.email, "chengzw@cnooc.com.cn");
    assert.deepEqual(
      calls.map((call) => call.url.pathname),
      [
        "/apigateway/appauth/getappid",
        "/apigateway/kzxmw/zcjksjfw_9543",
        "/apigateway/zhy/tokenCheck",
      ],
    );
  });

  it("reuses cached appCode for subsequent checks", async () => {
    let appCodeCalls = 0;
    const fetchImpl = async (input: string | URL | Request) => {
      const url = new URL(input.toString());
      if (url.pathname === "/apigateway/appauth/getappid") {
        appCodeCalls += 1;
        return jsonResponse({ code: 200, msg: "success", data: "app-code-1" });
      }
      if (url.pathname === "/apigateway/kzxmw/zcjksjfw_9543") {
        return jsonResponse({ code: 200, msg: "ok", bizCode: null, dataSize: 0, data: [] });
      }
      if (url.pathname === "/apigateway/zhy/tokenCheck") {
        return jsonResponse({ code: 200, msg: "ok", bizCode: "ok", dataSize: 1, data: successfulUserInfo() });
      }
      throw new Error(`unexpected URL ${url.toString()}`);
    };

    const client = createCnoocAuthClient({ config, fetchImpl });
    await client.checkCnoocToken("token-a");
    await client.checkCnoocToken("token-b");

    assert.equal(appCodeCalls, 1);
  });

  it("refreshes cached appCode and retries once after upstream failure", async () => {
    let appCodeCalls = 0;
    let tokenCheckCalls = 0;
    const seenAppCodes: string[] = [];
    const fetchImpl = async (input: string | URL | Request) => {
      const url = new URL(input.toString());
      if (url.pathname === "/apigateway/appauth/getappid") {
        appCodeCalls += 1;
        return jsonResponse({
          code: 200,
          msg: "success",
          data: `app-code-${appCodeCalls}`,
        });
      }
      if (url.pathname === "/apigateway/kzxmw/zcjksjfw_9543") {
        tokenCheckCalls += 1;
        seenAppCodes.push(url.searchParams.get("appCode") ?? "");
        if (tokenCheckCalls === 2) {
          return jsonResponse({ code: 500, msg: "expired appCode", bizCode: null, dataSize: 0, data: [] });
        }
        return jsonResponse({ code: 200, msg: "ok", bizCode: null, dataSize: 0, data: [] });
      }
      if (url.pathname === "/apigateway/zhy/tokenCheck") {
        return jsonResponse({ code: 200, msg: "ok", bizCode: "ok", dataSize: 1, data: successfulUserInfo() });
      }
      throw new Error(`unexpected URL ${url.toString()}`);
    };

    const client = createCnoocAuthClient({ config, fetchImpl });
    await client.checkCnoocToken("token-a");
    await client.checkCnoocToken("token-b");

    assert.equal(appCodeCalls, 2);
    assert.equal(tokenCheckCalls, 3);
    assert.deepEqual(seenAppCodes, ["app-code-1", "app-code-1", "app-code-2"]);
  });

  it("classifies token verification failure as unauthorized", async () => {
    const fetchImpl = async (input: string | URL | Request) => {
      const url = new URL(input.toString());
      if (url.pathname === "/apigateway/appauth/getappid") {
        return jsonResponse({ code: 200, msg: "success", data: "app-code-1" });
      }
      if (url.pathname === "/apigateway/kzxmw/zcjksjfw_9543") {
        return jsonResponse({ code: 401, msg: "invalid token", bizCode: null, dataSize: 0, data: [] });
      }
      throw new Error(`unexpected URL ${url.toString()}`);
    };

    const client = createCnoocAuthClient({ config, fetchImpl });

    await assert.rejects(
      () => client.checkCnoocToken("platform-token"),
      (error) => isCnoocAuthError(error, 401, /token verification failed/),
    );
  });

  it("classifies upstream network failure as bad gateway", async () => {
    const fetchImpl = async () => {
      throw new Error("network down with sensitive details");
    };

    const client = createCnoocAuthClient({ config, fetchImpl });

    await assert.rejects(
      () => client.checkCnoocToken("platform-token"),
      (error) => isCnoocAuthError(error, 502, /CNOOC appCode request failed/),
    );
  });
});

import crypto from "node:crypto";

export type CnoocTokenCheckResult = {
  ok: true;
  provider: "cnooc";
  platformTokenHash: string;
  user: {
    platformUserId: string;
    deerflowUserId: string;
    name: string;
    email: string;
  };
  roles: Array<{ code: string; name: string }>;
  department: { code: string; name: string };
};

export type CnoocAuthConfig = {
  baseUrl: string;
  appName: string;
  appId: string;
  tokenCheckApiToken: string;
  tokenCheckLoginUserCode: string;
  userInfoApiToken: string;
};

export type CnoocAuthClientOptions = {
  config?: CnoocAuthConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
};

type CnoocApiResponse = {
  code?: unknown;
  msg?: unknown;
  data?: unknown;
};

const APP_CODE_TTL_MS = 110 * 60 * 1000;

let cachedAppCode: { value: string; expiresAt: number } | null = null;

export class CnoocAuthError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "CnoocAuthError";
    this.status = status;
  }
}

function requireConfigValue(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new CnoocAuthError(`missing CNOOC OAuth config: ${name}`, 500);
  }
  return value;
}

function loadConfig(): CnoocAuthConfig {
  return {
    baseUrl: requireConfigValue("CNOOC_OAUTH_BASE_URL"),
    appName: requireConfigValue("CNOOC_OAUTH_APP_NAME"),
    appId: requireConfigValue("CNOOC_OAUTH_APP_ID"),
    tokenCheckApiToken: requireConfigValue("CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN"),
    tokenCheckLoginUserCode: requireConfigValue(
      "CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE",
    ),
    userInfoApiToken: requireConfigValue("CNOOC_OAUTH_USER_INFO_API_TOKEN"),
  };
}

function digestToken(token: string) {
  return crypto.createHash("sha1").update(token, "utf8").digest("hex");
}

function safeUserId(userId: string) {
  const raw = userId;
  const safe = raw.replace(/[^A-Za-z0-9._-]+/g, "_");
  if (!safe) return digestToken(raw).slice(0, 10);
  const safeRe = /^[A-Za-z0-9._-]+$/;
  if (safe !== raw || safe.length > 64 || !safeRe.test(safe)) {
    const digest = digestToken(raw).slice(0, 10);
    const prefix = safe.slice(0, 48).replace(/_+$/g, "");
    return prefix ? `${prefix}_${digest}` : digest;
  }
  return safe;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function requiredString(
  row: Record<string, unknown>,
  field: string,
): string {
  const value = row[field];
  if (typeof value !== "string" || value.length === 0) {
    throw new CnoocAuthError(`missing required user field: ${field}`, 502);
  }
  return value;
}

export function resetCnoocAppCodeCache() {
  cachedAppCode = null;
}

export function buildCnoocQueryPayload(
  key: "zhId" | "token",
  token: string,
) {
  return JSON.stringify({ jsonObj: { [key]: token } });
}

export function buildCnoocUrl(
  baseUrl: string,
  pathname: string,
  params: Record<string, string>,
) {
  const url = new URL(pathname, baseUrl);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, value);
  }
  return url;
}

export function mapCnoocUserInfo(data: unknown): CnoocTokenCheckResult {
  const row = asRecord(data);
  const platformUserId = requiredString(row, "userid");
  const username = requiredString(row, "username");
  const name = requiredString(row, "ryxm");
  const deerflowUserId = `cnooc_${safeUserId(platformUserId)}`;

  return {
    ok: true,
    provider: "cnooc",
    platformTokenHash: "",
    user: {
      platformUserId,
      deerflowUserId,
      name,
      email: `${username.toLowerCase()}@cnooc.com.cn`,
    },
    roles: [],
    department: { code: "", name: "" },
  };
}

async function readJsonResponse(
  response: Response,
  label: string,
): Promise<CnoocApiResponse> {
  if (!response.ok) {
    throw new CnoocAuthError(`${label} request failed`, 502);
  }
  try {
    return (await response.json()) as CnoocApiResponse;
  } catch {
    throw new CnoocAuthError(`${label} returned invalid json`, 502);
  }
}

async function fetchCnooc(
  fetchImpl: typeof fetch,
  input: string | URL | Request,
  label: string,
  init?: RequestInit,
) {
  try {
    return await fetchImpl(input, init);
  } catch {
    throw new CnoocAuthError(`${label} request failed`, 502);
  }
}

function assertApiSuccess(
  payload: CnoocApiResponse,
  label: string,
  status = 502,
) {
  if (payload.code !== 200) {
    throw new CnoocAuthError(`${label} failed`, status);
  }
}

function currentTimestampSeconds(now: () => number) {
  return String(Math.floor(now() / 1000));
}

export function createCnoocAuthClient({
  config = loadConfig(),
  fetchImpl = fetch,
  now = Date.now,
}: CnoocAuthClientOptions = {}) {
  async function fetchAppCode() {
    const url = buildCnoocUrl(config.baseUrl, "/apigateway/appauth/getappid", {
      appName: config.appName,
      appId: config.appId,
      timeStamp: currentTimestampSeconds(now),
    });
    const payload = await readJsonResponse(
      await fetchCnooc(fetchImpl, url, "CNOOC appCode"),
      "CNOOC appCode",
    );
    assertApiSuccess(payload, "CNOOC appCode");
    if (typeof payload.data !== "string" || payload.data.length === 0) {
      throw new CnoocAuthError("CNOOC appCode missing data", 502);
    }
    cachedAppCode = {
      value: payload.data,
      expiresAt: now() + APP_CODE_TTL_MS,
    };
    return payload.data;
  }

  async function getAppCode() {
    if (cachedAppCode && cachedAppCode.expiresAt > now()) {
      return { value: cachedAppCode.value, fromCache: true };
    }
    return { value: await fetchAppCode(), fromCache: false };
  }

  async function verifyToken(appCode: string, token: string) {
    const url = buildCnoocUrl(
      config.baseUrl,
      "/apigateway/kzxmw/zcjksjfw_9543",
      {
        appCode,
        query: buildCnoocQueryPayload("zhId", token),
      },
    );
    const payload = await readJsonResponse(
      await fetchCnooc(
        fetchImpl,
        url,
        "CNOOC token verification",
        {
          headers: {
            apiToken: config.tokenCheckApiToken,
            loginUserCode: config.tokenCheckLoginUserCode,
          },
        },
      ),
      "CNOOC token verification",
    );
    assertApiSuccess(payload, "CNOOC token verification", 401);
  }

  async function fetchUserInfo(appCode: string, token: string) {
    const url = buildCnoocUrl(config.baseUrl, "/apigateway/zhy/tokenCheck", {
      appCode,
      query: buildCnoocQueryPayload("token", token),
    });
    const payload = await readJsonResponse(
      await fetchCnooc(
        fetchImpl,
        url,
        "CNOOC user info",
        {
          headers: {
            apiToken: config.userInfoApiToken,
          },
        },
      ),
      "CNOOC user info",
    );
    assertApiSuccess(payload, "CNOOC user info");
    return payload.data;
  }

  async function checkOnce(token: string, appCode: string) {
    await verifyToken(appCode, token);
    const userInfo = await fetchUserInfo(appCode, token);
    const result = mapCnoocUserInfo(userInfo);
    return {
      ...result,
      platformTokenHash: digestToken(token),
    };
  }

  return {
    async checkCnoocToken(token: string): Promise<CnoocTokenCheckResult> {
      if (token === "") {
        throw new CnoocAuthError("token is required", 400);
      }

      const appCode = await getAppCode();
      try {
        return await checkOnce(token, appCode.value);
      } catch (error) {
        if (!(error instanceof CnoocAuthError) || !appCode.fromCache) {
          throw error;
        }
        cachedAppCode = null;
        const refreshed = await getAppCode();
        return await checkOnce(token, refreshed.value);
      }
    },
  };
}

export async function checkCnoocToken(token: string) {
  return createCnoocAuthClient().checkCnoocToken(token);
}

import { p as post, g as get, b as put, i as i18n } from "./tdesign-icon-offline-DpBpi-YD.js";
import "./vendor-highlight-2iXUmHrb.js";
import "./vendor-mermaid-WsNc9-GI.js";

const translate = (key) => i18n.global.t(key);

function userInfoFromApi(user, tenantId) {
  const resolvedTenantId =
    user?.tenant_id !== undefined && user?.tenant_id !== null && user.tenant_id !== ""
      ? user.tenant_id
      : (tenantId ?? "");
  return {
    id: user?.id || "",
    username: user?.username || "",
    email: user?.email || "",
    avatar: user?.avatar,
    tenant_id: String(resolvedTenantId) || "",
    can_access_all_tenants: user?.can_access_all_tenants === true,
    is_system_admin: user?.is_system_admin === true,
    preferences: user?.preferences,
    created_at: user?.created_at || new Date().toISOString(),
    updated_at: user?.updated_at || new Date().toISOString(),
  };
}

async function login(payload) {
  try {
    return await post("/api/v1/auth/login", payload);
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.loginFailed") };
  }
}

async function getOIDCAuthorizationURL(redirectUri) {
  try {
    return await get(`/api/v1/auth/oidc/url?redirect_uri=${encodeURIComponent(redirectUri)}`);
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.loginFailed") };
  }
}

async function getOIDCConfig() {
  try {
    return await get("/api/v1/auth/oidc/config");
  } catch (error) {
    return {
      success: false,
      enabled: false,
      message: error.message || translate("error.auth.loginFailed"),
    };
  }
}

async function getAuthConfig() {
  try {
    return await get("/api/v1/auth/config");
  } catch {
    return { success: false, registration_mode: "self_serve" };
  }
}

async function register(payload) {
  try {
    return await post("/api/v1/auth/register", payload);
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.registerFailed") };
  }
}

async function autoSetup() {
  try {
    const url = "/api/weknora/sso-session";
    const response = await fetch(url, { credentials: "include", cache: "no-store" });
    if (response.ok) {
      const data = await response.json();
      if (data?.success) return data;
    }
  } catch {
    // Fall through to WeKnora's native auto-setup behavior.
  }
  try {
    return await post("/api/v1/auth/auto-setup", {});
  } catch (error) {
    return { success: false, message: error.message || "Auto-setup unavailable" };
  }
}

async function getCurrentUser() {
  try {
    return await get("/api/weknora-native/auth/me");
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.getUserFailed") };
  }
}

async function updateMyPreferences(payload) {
  try {
    return await put("/api/v1/auth/me/preferences", payload);
  } catch (error) {
    return {
      success: false,
      message: error.message || translate("error.auth.updatePreferencesFailed"),
    };
  }
}

async function getCurrentTenant() {
  try {
    return await get("/api/v1/auth/tenant");
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.getTenantFailed") };
  }
}

async function refreshToken(refreshTokenValue) {
  try {
    const response = await post("/api/v1/auth/refresh", { refreshToken: refreshTokenValue });
    return response?.success && (response.access_token || response.refresh_token)
      ? {
          success: true,
          data: { token: response.access_token, refreshToken: response.refresh_token },
        }
      : {
          success: false,
          message: response?.message || translate("error.auth.refreshTokenFailed"),
        };
  } catch (error) {
    return {
      success: false,
      message: error.message || translate("error.auth.refreshTokenFailed"),
    };
  }
}

async function logout() {
  try {
    await post("/api/v1/auth/logout", {});
    return { success: true };
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.logoutFailed") };
  }
}

async function validateToken() {
  try {
    return await get("/api/v1/auth/validate");
  } catch (error) {
    return {
      success: false,
      valid: false,
      message: error.message || translate("error.auth.validateTokenFailed"),
    };
  }
}

async function getInvitationByToken(token) {
  try {
    return await post("/api/v1/auth/invitations/lookup", { token });
  } catch (error) {
    return { success: false, message: error.message || "" };
  }
}

async function registerByInvite(payload) {
  try {
    return await post("/api/v1/auth/register-by-invite", payload);
  } catch (error) {
    return { success: false, message: error.message || translate("error.auth.registerFailed") };
  }
}

export {
  autoSetup,
  getAuthConfig,
  getCurrentTenant,
  getCurrentUser,
  getInvitationByToken,
  getOIDCAuthorizationURL,
  getOIDCConfig,
  login,
  logout,
  refreshToken,
  register,
  registerByInvite,
  updateMyPreferences,
  userInfoFromApi,
  validateToken,
};

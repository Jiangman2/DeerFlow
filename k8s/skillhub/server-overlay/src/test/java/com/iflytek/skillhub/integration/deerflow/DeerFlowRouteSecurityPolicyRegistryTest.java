package com.iflytek.skillhub.integration.deerflow;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DeerFlowRouteSecurityPolicyRegistryTest {

    private final DeerFlowRouteSecurityPolicyRegistry registry =
        new DeerFlowRouteSecurityPolicyRegistry();

    @Test
    void ignoresCsrfOnlyForThePassiveSessionBootstrapPost() {
        assertTrue(registry.shouldIgnoreCsrf(
            "POST",
            "/api/v1/auth/session/bootstrap",
            null,
            false
        ));
        assertFalse(registry.shouldIgnoreCsrf(
            "GET",
            "/api/v1/auth/session/bootstrap",
            null,
            false
        ));
        assertFalse(registry.shouldIgnoreCsrf(
            "POST",
            "/api/v1/admin/users",
            null,
            false
        ));
    }

    @Test
    void preservesNativeBearerAndDeviceFlowRules() {
        assertTrue(registry.shouldIgnoreCsrf("POST", "/not-api", "Bearer token", false));
        assertFalse(registry.shouldIgnoreCsrf("POST", "/not-api", "Bearer token", true));
        assertTrue(registry.shouldIgnoreCsrf("POST", "/api/v1/auth/device/code", null, false));
        assertTrue(registry.shouldIgnoreCsrf("POST", "/api/v1/auth/device/token", null, false));
    }
}

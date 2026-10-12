package com.iflytek.skillhub.integration.deerflow;

import com.iflytek.skillhub.auth.policy.RouteSecurityPolicyRegistry;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * Requires authentication for enterprise registry reads and preserves bootstrap CSRF.
 *
 * <p>The bootstrap request is the operation that creates the first SkillHub
 * browser session, so the caller cannot already possess a SkillHub CSRF token.
 * The endpoint remains protected by the exact Nginx route, DeerFlow session
 * authentication, and the short-lived HMAC identity assertion verified by
 * {@link DeerFlowSessionAuthenticator}. All other browser writes retain the
 * native SkillHub CSRF policy.</p>
 */
@Primary
@Component
@ConditionalOnProperty(
    name = "skillhub.auth.deerflow-session.enabled",
    havingValue = "true"
)
public final class DeerFlowRouteSecurityPolicyRegistry extends RouteSecurityPolicyRegistry {

    static final String SESSION_BOOTSTRAP_PATH = "/api/v1/auth/session/bootstrap";

    @Override
    public List<RouteAuthorizationPolicy> authorizationPolicies() {
        return super.authorizationPolicies().stream()
            .map(policy -> policy.accessLevel() == AccessLevel.PERMIT_ALL
                && isRegistryDataPath(policy.pattern())
                    ? RouteAuthorizationPolicy.authenticated(policy.method(), policy.pattern())
                    : policy)
            .toList();
    }

    private static boolean isRegistryDataPath(String path) {
        return path.equals("/api/v1/check")
            || path.equals("/api/v1/search")
            || path.startsWith("/api/v1/resolve/")
            || path.startsWith("/api/v1/download/")
            || path.equals("/api/v1/download")
            || path.equals("/api/v1/skills")
            || path.startsWith("/api/v1/skills/")
            || path.equals("/api/v1/labels")
            || path.equals("/api/web/skills")
            || path.startsWith("/api/web/skills/")
            || path.equals("/api/web/labels")
            || path.startsWith("/api/cli/v1/skills/");
    }

    @Override
    public boolean shouldIgnoreCsrf(
        String method,
        String path,
        String authorizationHeader,
        boolean hasSessionCookie
    ) {
        if ("POST".equalsIgnoreCase(method) && SESSION_BOOTSTRAP_PATH.equals(path)) {
            return true;
        }
        return super.shouldIgnoreCsrf(method, path, authorizationHeader, hasSessionCookie);
    }
}

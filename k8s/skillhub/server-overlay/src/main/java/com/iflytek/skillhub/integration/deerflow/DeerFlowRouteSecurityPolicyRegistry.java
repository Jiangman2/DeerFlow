package com.iflytek.skillhub.integration.deerflow;

import com.iflytek.skillhub.auth.policy.RouteSecurityPolicyRegistry;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * Extends SkillHub's CSRF policy for the passive DeerFlow session bootstrap.
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

package com.iflytek.skillhub.integration.deerflow;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.skillhub.auth.bootstrap.PassiveSessionAuthenticator;
import com.iflytek.skillhub.auth.identity.IdentityBindingService;
import com.iflytek.skillhub.auth.oauth.OAuthClaims;
import com.iflytek.skillhub.auth.rbac.PlatformPrincipal;
import com.iflytek.skillhub.domain.user.UserStatus;
import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.util.Base64;
import java.util.Map;
import java.util.Optional;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * Establishes a native SkillHub session from a short-lived identity assertion
 * injected by the same-origin DeerFlow Nginx auth subrequest.
 *
 * <p>The assertion intentionally carries no roles. IdentityBindingService
 * loads platform roles from SkillHub's own database, so this adapter cannot
 * promote a DeerFlow administrator into a SkillHub administrator.</p>
 */
@Component
@ConditionalOnProperty(
    name = "skillhub.auth.deerflow-session.enabled",
    havingValue = "true"
)
public final class DeerFlowSessionAuthenticator implements PassiveSessionAuthenticator {

    static final String PROVIDER = "deerflow-session";
    static final String IDENTITY_HEADER = "X-SkillHub-Identity";
    static final String SIGNATURE_HEADER = "X-SkillHub-Signature";
    private static final String ISSUER = "deerflow-gateway";
    private static final String AUDIENCE = "skillhub";
    private static final int MINIMUM_SECRET_BYTES = 32;
    private static final int MAXIMUM_ASSERTION_LENGTH = 8192;
    private static final long MAXIMUM_CLOCK_SKEW_SECONDS = 5;
    private static final long MAXIMUM_ASSERTION_TTL_SECONDS = 60;

    private final IdentityBindingService identityBindingService;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final byte[] secret;

    @Autowired
    public DeerFlowSessionAuthenticator(
        IdentityBindingService identityBindingService,
        ObjectMapper objectMapper,
        @Value("${skillhub.auth.deerflow-session.secret:}") String secret
    ) {
        this(identityBindingService, objectMapper, secret, Clock.systemUTC());
    }

    DeerFlowSessionAuthenticator(
        IdentityBindingService identityBindingService,
        ObjectMapper objectMapper,
        String secret,
        Clock clock
    ) {
        this.identityBindingService = identityBindingService;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
    }

    @Override
    public String providerCode() {
        return PROVIDER;
    }

    @Override
    public String displayName() {
        return "Enterprise SSO";
    }

    @Override
    public Optional<PlatformPrincipal> authenticate(HttpServletRequest request) {
        Optional<OAuthClaims> claims = verifiedClaims(
            request.getHeader(IDENTITY_HEADER),
            request.getHeader(SIGNATURE_HEADER)
        );
        return claims.map(value -> identityBindingService.bindOrCreate(value, UserStatus.ACTIVE));
    }

    Optional<OAuthClaims> verifiedClaims(String encodedIdentity, String encodedSignature) {
        if (secret.length < MINIMUM_SECRET_BYTES
            || !bounded(encodedIdentity)
            || !bounded(encodedSignature)) {
            return Optional.empty();
        }

        try {
            byte[] suppliedSignature = Base64.getUrlDecoder().decode(pad(encodedSignature));
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            byte[] expectedSignature = mac.doFinal(encodedIdentity.getBytes(StandardCharsets.US_ASCII));
            if (!MessageDigest.isEqual(expectedSignature, suppliedSignature)) {
                return Optional.empty();
            }

            byte[] decoded = Base64.getUrlDecoder().decode(pad(encodedIdentity));
            JsonNode payload = objectMapper.readTree(decoded);
            long now = clock.instant().getEpochSecond();
            long issuedAt = payload.path("iat").asLong(Long.MIN_VALUE);
            long expiresAt = payload.path("exp").asLong(Long.MIN_VALUE);
            if (payload.path("v").asInt(-1) != 1
                || !ISSUER.equals(payload.path("iss").asText())
                || !AUDIENCE.equals(payload.path("aud").asText())
                || issuedAt > now + MAXIMUM_CLOCK_SKEW_SECONDS
                || issuedAt < now - MAXIMUM_ASSERTION_TTL_SECONDS
                || expiresAt <= now
                || expiresAt > now + MAXIMUM_ASSERTION_TTL_SECONDS
                || expiresAt <= issuedAt) {
                return Optional.empty();
            }

            String subject = requiredText(payload, "sub", 128);
            String email = requiredText(payload, "email", 320);
            String displayName = requiredText(payload, "name", 255);
            requiredText(payload, "nonce", 128);
            if (subject == null || email == null || displayName == null) {
                return Optional.empty();
            }

            return Optional.of(new OAuthClaims(
                PROVIDER,
                subject,
                email,
                true,
                displayName,
                Map.of("deerflow_user_id", subject)
            ));
        } catch (Exception ignored) {
            return Optional.empty();
        }
    }

    private static boolean bounded(String value) {
        return value != null && !value.isBlank() && value.length() <= MAXIMUM_ASSERTION_LENGTH;
    }

    private static String pad(String value) {
        int padding = (4 - value.length() % 4) % 4;
        return value + "=".repeat(padding);
    }

    private static String requiredText(JsonNode payload, String field, int maximumLength) {
        JsonNode value = payload.path(field);
        if (!value.isTextual()) return null;
        String text = value.asText().trim();
        if (text.isEmpty() || text.length() > maximumLength) return null;
        return text;
    }
}

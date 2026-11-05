package com.iflytek.skillhub.integration.deerflow;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.skillhub.auth.identity.IdentityBindingService;
import com.iflytek.skillhub.auth.oauth.OAuthClaims;
import com.iflytek.skillhub.auth.rbac.PlatformPrincipal;
import com.iflytek.skillhub.domain.user.UserStatus;
import jakarta.servlet.http.HttpServletRequest;
import java.lang.reflect.Constructor;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.Map;
import java.util.Set;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;

class DeerFlowSessionAuthenticatorTest {

    private static final String SECRET = "skillhub-session-bridge-test-secret-2026";
    private static final Clock CLOCK = Clock.fixed(
        Instant.ofEpochSecond(1_788_534_000L),
        ZoneOffset.UTC
    );

    @Test
    void explicitlyMarksTheProductionConstructorForSpringInjection() throws Exception {
        Constructor<DeerFlowSessionAuthenticator> constructor =
            DeerFlowSessionAuthenticator.class.getConstructor(
                IdentityBindingService.class,
                ObjectMapper.class,
                String.class
            );

        assertTrue(constructor.isAnnotationPresent(Autowired.class));
    }

    @Test
    void springBeanFactoryCreatesTheAuthenticatorWithoutANoArgConstructor() {
        try (AnnotationConfigApplicationContext context =
                 new AnnotationConfigApplicationContext()) {
            context.getEnvironment().getPropertySources().addFirst(
                new MapPropertySource(
                    "deerflow-session-test",
                    Map.of(
                        "skillhub.auth.deerflow-session.enabled", "true",
                        "skillhub.auth.deerflow-session.secret", SECRET
                    )
                )
            );
            context.registerBean(
                IdentityBindingService.class,
                () -> mock(IdentityBindingService.class)
            );
            context.registerBean(ObjectMapper.class, () -> new ObjectMapper());
            context.register(DeerFlowSessionAuthenticator.class);
            context.refresh();

            DeerFlowSessionAuthenticator authenticator =
                context.getBean(DeerFlowSessionAuthenticator.class);

            assertEquals(DeerFlowSessionAuthenticator.PROVIDER, authenticator.providerCode());
        }
    }

    @Test
    void verifiesIdentityButDelegatesRolesToNativeSkillHubBindings() throws Exception {
        IdentityBindingService bindingService = mock(IdentityBindingService.class);
        PlatformPrincipal principal = new PlatformPrincipal(
            "usr-native",
            "Test User",
            "user@example.com",
            null,
            "deerflow-session",
            Set.of("USER")
        );
        when(bindingService.bindOrCreate(org.mockito.ArgumentMatchers.any(), eq(UserStatus.ACTIVE)))
            .thenReturn(principal);
        DeerFlowSessionAuthenticator authenticator = new DeerFlowSessionAuthenticator(
            bindingService,
            new ObjectMapper(),
            SECRET,
            CLOCK
        );
        HttpServletRequest request = signedRequest(validClaims());

        assertEquals(principal, authenticator.authenticate(request).orElseThrow());
        ArgumentCaptor<OAuthClaims> claims = ArgumentCaptor.forClass(OAuthClaims.class);
        verify(bindingService).bindOrCreate(claims.capture(), eq(UserStatus.ACTIVE));
        assertEquals("deerflow-session", claims.getValue().provider());
        assertEquals("deerflow-user-1", claims.getValue().subject());
        assertEquals(Map.of("deerflow_user_id", "deerflow-user-1"), claims.getValue().extra());
    }

    @Test
    void rejectsTamperedExpiredAndWeakSecretAssertions() throws Exception {
        IdentityBindingService bindingService = mock(IdentityBindingService.class);
        DeerFlowSessionAuthenticator authenticator = new DeerFlowSessionAuthenticator(
            bindingService,
            new ObjectMapper(),
            SECRET,
            CLOCK
        );
        HttpServletRequest tampered = signedRequest(validClaims());
        when(tampered.getHeader(DeerFlowSessionAuthenticator.IDENTITY_HEADER))
            .thenReturn("tampered");
        assertTrue(authenticator.authenticate(tampered).isEmpty());

        Map<String, Object> expired = validClaims();
        expired.put("iat", 1_788_533_900L);
        expired.put("exp", 1_788_533_930L);
        assertTrue(authenticator.authenticate(signedRequest(expired)).isEmpty());

        DeerFlowSessionAuthenticator weak = new DeerFlowSessionAuthenticator(
            bindingService,
            new ObjectMapper(),
            "too-short",
            CLOCK
        );
        assertTrue(weak.authenticate(signedRequest(validClaims())).isEmpty());
        verifyNoInteractions(bindingService);
    }

    private static Map<String, Object> validClaims() {
        return new java.util.HashMap<>(Map.of(
            "v", 1,
            "iss", "deerflow-gateway",
            "aud", "skillhub",
            "sub", "deerflow-user-1",
            "name", "Test User",
            "email", "user@example.com",
            "iat", 1_788_534_000L,
            "exp", 1_788_534_030L,
            "nonce", "nonce-1"
        ));
    }

    private static HttpServletRequest signedRequest(Map<String, Object> claims) throws Exception {
        String identity = Base64.getUrlEncoder().withoutPadding().encodeToString(
            new ObjectMapper().writeValueAsBytes(claims)
        );
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        String signature = Base64.getUrlEncoder().withoutPadding().encodeToString(
            mac.doFinal(identity.getBytes(StandardCharsets.US_ASCII))
        );
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getHeader(DeerFlowSessionAuthenticator.IDENTITY_HEADER)).thenReturn(identity);
        when(request.getHeader(DeerFlowSessionAuthenticator.SIGNATURE_HEADER)).thenReturn(signature);
        return request;
    }
}

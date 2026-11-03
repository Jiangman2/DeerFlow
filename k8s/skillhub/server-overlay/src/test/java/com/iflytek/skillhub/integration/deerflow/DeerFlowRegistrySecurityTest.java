package com.iflytek.skillhub.integration.deerflow;

import com.iflytek.skillhub.auth.entity.ApiToken;
import com.iflytek.skillhub.auth.rbac.PlatformPrincipal;
import com.iflytek.skillhub.auth.repository.UserRoleBindingRepository;
import com.iflytek.skillhub.auth.token.ApiTokenService;
import com.iflytek.skillhub.domain.user.UserAccount;
import com.iflytek.skillhub.domain.user.UserAccountRepository;
import jakarta.servlet.http.Cookie;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.ApplicationContext;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.mockito.BDDMockito.given;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestPropertySource(properties = {
    "skillhub.auth.session-bootstrap.enabled=true",
    "skillhub.auth.deerflow-session.enabled=true",
    "skillhub.auth.deerflow-session.secret=0123456789abcdef0123456789abcdef"
})
class DeerFlowRegistrySecurityTest {
    @Autowired private MockMvc mockMvc;
    @Autowired private ApplicationContext context;
    @MockBean private ApiTokenService apiTokenService;
    @MockBean private UserAccountRepository userAccountRepository;
    @MockBean private UserRoleBindingRepository roleBindingRepository;

    @ParameterizedTest
    @ValueSource(strings = {
        "/api/web/skills", "/api/web/labels", "/api/v1/skills", "/api/v1/labels",
        "/api/web/skills/global/example", "/api/web/skills/global/example/versions",
        "/api/web/skills/global/example/versions/1/files",
        "/api/web/skills/global/example/versions/1/file",
        "/api/web/skills/global/example/download",
        "/api/v1/search", "/api/v1/download", "/api/v1/skills/example",
        "/api/v1/resolve/global/example", "/api/v1/download/global/example",
        "/api/v1/check", "/api/cli/v1/skills/search",
        "/api/cli/v1/skills/global/example/resolve",
        "/api/cli/v1/skills/global/example/download"
    })
    void anonymousRequestsCannotReadRegistryData(String path) throws Exception {
        mockMvc.perform(get(path)).andExpect(status().isUnauthorized());
    }

    @Test
    void publicCompatibilityChainsCannotOverrideEnterpriseAuthentication() {
        assertFalse(context.containsBean("publicLabelFilterChain"));
        assertFalse(context.containsBean("clawHubRegistryFilterChain"));
    }

    @Test
    void fabricatedSessionAndIdentityHeadersDoNotAuthenticate() throws Exception {
        mockMvc.perform(get("/api/web/labels")
                .cookie(new Cookie("SESSION", "fabricated-session"))
                .header("X-SkillHub-Identity", "fabricated-identity")
                .header("X-SkillHub-Signature", "fabricated-signature"))
            .andExpect(status().isUnauthorized());
    }

    @Test
    void invalidBearerDoesNotReadLabels() throws Exception {
        given(apiTokenService.validateToken("invalid-token")).willReturn(Optional.empty());
        mockMvc.perform(get("/api/web/labels").header("Authorization", "Bearer invalid-token"))
            .andExpect(status().isUnauthorized());
    }

    @Test
    void authenticatedBrowserCanReadLabels() throws Exception {
        var principal = new PlatformPrincipal("reader", "Reader", null, null, "deerflow-session", Set.of("USER"));
        var auth = new UsernamePasswordAuthenticationToken(principal, null,
            List.of(new SimpleGrantedAuthority("ROLE_USER")));
        mockMvc.perform(get("/api/web/labels").with(authentication(auth)))
            .andExpect(status().isOk());
    }

    @Test
    void validBearerCanReadLabelsWithoutDeerFlowCookie() throws Exception {
        var token = new ApiToken("reader", "read-only", "skh_test", "test-hash", "[]");
        given(apiTokenService.validateToken("valid-read-token")).willReturn(Optional.of(token));
        given(userAccountRepository.findById("reader"))
            .willReturn(Optional.of(new UserAccount("reader", "Reader", null, null)));
        given(roleBindingRepository.findByUserId("reader")).willReturn(List.of());
        mockMvc.perform(get("/api/web/labels").header("Authorization", "Bearer valid-read-token"))
            .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/tokens")
                .header("Authorization", "Bearer valid-read-token")
                .contentType("application/json").content("{}"))
            .andExpect(status().isForbidden());
    }

    @Test
    void loginDiscoveryAndHealthRemainReachable() throws Exception {
        mockMvc.perform(get("/api/v1/auth/providers")).andExpect(status().isOk());
        mockMvc.perform(get("/api/v1/health")).andExpect(status().isOk());
    }
}

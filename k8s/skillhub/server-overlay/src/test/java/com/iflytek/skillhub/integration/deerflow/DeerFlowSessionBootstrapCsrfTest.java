package com.iflytek.skillhub.integration.deerflow;

import com.iflytek.skillhub.auth.bootstrap.PassiveSessionAuthenticator;
import com.iflytek.skillhub.auth.local.LocalCredentialRepository;
import com.iflytek.skillhub.auth.repository.UserRoleBindingRepository;
import com.iflytek.skillhub.auth.rbac.PlatformPrincipal;
import com.iflytek.skillhub.domain.namespace.NamespaceMemberRepository;
import com.iflytek.skillhub.domain.user.UserAccount;
import com.iflytek.skillhub.domain.user.UserAccountRepository;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Bean;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestPropertySource(properties = {
    "skillhub.auth.session-bootstrap.enabled=true",
    "skillhub.auth.deerflow-session.enabled=true",
    "skillhub.auth.deerflow-session.secret=0123456789abcdef0123456789abcdef"
})
class DeerFlowSessionBootstrapCsrfTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private NamespaceMemberRepository namespaceMemberRepository;

    @MockBean
    private UserAccountRepository userAccountRepository;

    @MockBean
    private UserRoleBindingRepository userRoleBindingRepository;

    @MockBean
    private LocalCredentialRepository localCredentialRepository;

    @Test
    void passiveBootstrapCreatesTheFirstSessionWithoutAPreexistingCsrfToken() throws Exception {
        given(namespaceMemberRepository.findByUserId("bootstrap-user-1")).willReturn(List.of());
        given(userAccountRepository.findById("bootstrap-user-1"))
            .willReturn(Optional.of(new UserAccount(
                "bootstrap-user-1",
                "Bootstrap User",
                null,
                null
            )));
        given(userRoleBindingRepository.findByUserId("bootstrap-user-1")).willReturn(List.of());
        given(localCredentialRepository.existsByUserId("bootstrap-user-1")).willReturn(false);

        mockMvc.perform(post("/api/v1/auth/session/bootstrap")
                .contentType("application/json")
                .content("""
                    {"provider":"csrf-test-session"}
                    """))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.code").value(0))
            .andExpect(jsonPath("$.data.userId").value("bootstrap-user-1"));
    }

    @TestConfiguration
    static class BootstrapTestConfig {

        @Bean
        PassiveSessionAuthenticator csrfTestSessionAuthenticator() {
            return new PassiveSessionAuthenticator() {
                @Override
                public String providerCode() {
                    return "csrf-test-session";
                }

                @Override
                public Optional<PlatformPrincipal> authenticate(
                    jakarta.servlet.http.HttpServletRequest request
                ) {
                    return Optional.of(new PlatformPrincipal(
                        "bootstrap-user-1",
                        "Bootstrap User",
                        null,
                        null,
                        "csrf-test-session",
                        Set.of("USER")
                    ));
                }
            };
        }
    }
}

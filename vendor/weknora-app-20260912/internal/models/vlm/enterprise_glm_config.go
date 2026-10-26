package vlm

import (
	"errors"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	secutils "github.com/Tencent/WeKnora/internal/utils"
)

func NewEnterpriseGLMVLM(config *Config) (*EnterpriseGLMVLM, error) {
	if config == nil || config.Extra["wire_profile"] != EnterpriseGLMWireProfile ||
		config.Source != types.ModelSourceRemote || config.ModelName != enterpriseGLMModel ||
		(config.BaseURL != enterpriseGLMBaseURL && config.BaseURL != enterpriseGLMBaseURL+"/") ||
		(config.InterfaceType != "" && config.InterfaceType != "openai") ||
		(config.Provider != "" && config.Provider != "openai") || len(config.CustomHeaders) != 0 {
		return nil, errors.New("enterprise GLM: invalid profile configuration")
	}
	// Reject unsupported options rather than pretending to apply them.
	for key := range config.Extra {
		if key != "wire_profile" {
			return nil, errors.New("enterprise GLM: unsupported profile option")
		}
	}
	if len(config.APIKey) == 0 || len(config.APIKey) > 4096 {
		return nil, errors.New("enterprise GLM: invalid credential")
	}
	for _, c := range config.APIKey {
		if c < 33 || c > 126 {
			return nil, errors.New("enterprise GLM: invalid credential")
		}
	}
	// The operator must already allow this exact host under the app SSRF policy.
	// This profile never extends the allowlist or bypasses DNS/IP enforcement.
	if err := validateVLMBaseURL(enterpriseGLMBaseURL); err != nil {
		return nil, errors.New("enterprise GLM: endpoint blocked by URL policy")
	}
	timeout := 180 * time.Second
	if raw := os.Getenv("VLM_HTTP_TIMEOUT_SECONDS"); raw != "" {
		seconds, err := strconv.Atoi(raw)
		if err != nil || seconds < 1 || seconds > 600 {
			return nil, errors.New("enterprise GLM: timeout must be 1..600 seconds")
		}
		timeout = time.Duration(seconds) * time.Second
	}
	return &EnterpriseGLMVLM{
		modelID: config.ModelID, apiKey: config.APIKey,
		endpoint: enterpriseGLMBaseURL + "/chat/completions",
		timeout:  timeout, client: newEnterpriseGLMHTTPClient(timeout),
	}, nil
}

func newEnterpriseGLMHTTPClient(timeout time.Duration) *http.Client {
	cfg := secutils.DefaultSSRFSafeHTTPClientConfig()
	cfg.Timeout = timeout
	cfg.DisableCompression = true
	transport := secutils.NewSSRFSafeTransport(cfg)
	transport.Proxy = nil // enterprise direct route; do not inherit proxy env
	transport.TLSHandshakeTimeout = 15 * time.Second
	transport.ResponseHeaderTimeout = timeout
	transport.MaxResponseHeaderBytes = 32 << 10
	transport.IdleConnTimeout = 30 * time.Second
	// TLSClientConfig remains nil: system/SSL_CERT_FILE trust and hostname
	// verification are preserved. Never use InsecureSkipVerify or a custom CA fallback.
	client := secutils.NewSSRFSafeHTTPClientWithTransport(cfg, transport)
	client.CheckRedirect = func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return client
}

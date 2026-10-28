package vlm

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	secutils "github.com/Tencent/WeKnora/internal/utils"
)

func glmTestConfig() *Config {
	return &Config{Source: types.ModelSourceRemote, ModelID: "synthetic-glm-vlm",
		ModelName: enterpriseGLMModel, BaseURL: enterpriseGLMBaseURL, APIKey: glmTestKey,
		Extra: map[string]any{"wire_profile": EnterpriseGLMWireProfile}}
}

func glmTestClient(t *testing.T) *EnterpriseGLMVLM {
	t.Helper()
	withVLMSSRFWhitelist(t, "aiapi.cnooc.com.cn,127.0.0.1")
	t.Setenv("VLM_HTTP_TIMEOUT_SECONDS", "2")
	v, err := NewEnterpriseGLMVLM(glmTestConfig())
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func TestEnterpriseGLMSelection(t *testing.T) {
	_ = glmTestClient(t)
	v, err := NewVLM(glmTestConfig(), nil)
	if err != nil {
		t.Fatal(err)
	}
	wrapper, ok := v.(*concurrencyVLM)
	if !ok || wrapper.GetModelID() != "synthetic-glm-vlm" || wrapper.GetModelName() != enterpriseGLMModel {
		t.Fatal("native model governor/identity not preserved")
	}
	cfg := glmTestConfig()
	cfg.Extra = nil
	v, err = newVLM(cfg, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := v.(*RemoteAPIVLM); !ok {
		t.Fatal("non-opt-in model changed path")
	}
	if _, err := newVLM(nil, nil); err == nil {
		t.Fatal("nil config accepted")
	}
}

func TestEnterpriseGLMConfigFromModel(t *testing.T) {
	m := &types.Model{ID: "glm", Name: enterpriseGLMModel, Source: types.ModelSourceRemote,
		Parameters: types.ModelParameters{BaseURL: enterpriseGLMBaseURL, APIKey: glmTestKey,
			MaxConcurrency: 12, ExtraConfig: map[string]string{"wire_profile": EnterpriseGLMWireProfile}}}
	cfg := ConfigFromModel(m, "", "")
	if cfg.Extra["wire_profile"] != EnterpriseGLMWireProfile || cfg.MaxConcurrency != 12 {
		t.Fatal("persisted model configuration lost")
	}
}

func TestEnterpriseGLMRejectConfiguration(t *testing.T) {
	_ = glmTestClient(t)
	for name, mutate := range map[string]func(*Config){
		"unknown profile":    func(c *Config) { c.Extra["wire_profile"] = "other" },
		"profile wrong type": func(c *Config) { c.Extra["wire_profile"] = map[string]string{} },
		"missing profile":    func(c *Config) { c.Extra = nil },
		"model":              func(c *Config) { c.ModelName = "qwen3-vl-plus" },
		"http":               func(c *Config) { c.BaseURL = strings.Replace(c.BaseURL, "https:", "http:", 1) },
		"host":               func(c *Config) { c.BaseURL = "https://attacker.invalid/v1" },
		"userinfo":           func(c *Config) { c.BaseURL = strings.Replace(c.BaseURL, "https://", "https://secret@", 1) },
		"query":              func(c *Config) { c.BaseURL += "?credential=secret" },
		"port":               func(c *Config) { c.BaseURL = strings.Replace(c.BaseURL, ".cn/", ".cn:443/", 1) },
		"fragment":           func(c *Config) { c.BaseURL += "#x" },
		"source":             func(c *Config) { c.Source = types.ModelSourceLocal },
		"provider":           func(c *Config) { c.Provider = "azure" },
		"interface":          func(c *Config) { c.InterfaceType = "ollama" },
		"extra":              func(c *Config) { c.Extra["temperature"] = "1.0" },
		"headers":            func(c *Config) { c.CustomHeaders = map[string]string{"Accept": "application/json"} },
		"empty key":          func(c *Config) { c.APIKey = "" },
		"key newline":        func(c *Config) { c.APIKey += "\r\nsecret" },
		"key huge":           func(c *Config) { c.APIKey = strings.Repeat("x", 4097) },
	} {
		t.Run(name, func(t *testing.T) {
			cfg := glmTestConfig()
			mutate(cfg)
			if _, err := NewEnterpriseGLMVLM(cfg); err == nil {
				t.Fatal("unsafe config accepted")
			}
		})
	}
	for _, value := range []string{"0", "-1", "601", "abc", "9999999999999999999999"} {
		t.Run("timeout_"+value, func(t *testing.T) {
			t.Setenv("VLM_HTTP_TIMEOUT_SECONDS", value)
			if _, err := NewEnterpriseGLMVLM(glmTestConfig()); err == nil {
				t.Fatal("bad timeout accepted")
			}
		})
	}
}

func TestEnterpriseGLMTransportPolicy(t *testing.T) {
	v := glmTestClient(t)
	wrapped := v.client.Transport.(*secutils.SSRFValidatingRoundTripper)
	transport := wrapped.Base.(*http.Transport)
	if transport.Proxy != nil || transport.TLSClientConfig != nil || transport.DialContext == nil || !transport.DisableCompression {
		t.Fatal("direct SSRF/TLS transport contract changed")
	}
	if v.client.CheckRedirect(nil, nil) != http.ErrUseLastResponse {
		t.Fatal("redirect policy changed")
	}
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		http.Redirect(w, r, "/second", 307)
	}))
	defer s.Close()
	v.endpoint = s.URL
	_, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "describe")
	if err == nil || calls.Load() != 1 {
		t.Fatal("redirect followed")
	}
	tlsServer := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("untrusted TLS reached handler") }))
	defer tlsServer.Close()
	v.endpoint = tlsServer.URL
	if _, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "describe"); err == nil {
		t.Fatal("untrusted TLS accepted")
	}
	withVLMSSRFWhitelist(t, "")
	if _, err := v.client.Post(s.URL, "application/json", bytes.NewBufferString("{}")); err == nil {
		t.Fatal("SSRF private host allowed")
	}
	if calls.Load() != 1 {
		t.Fatal("blocked host received traffic")
	}
}

package vlm

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Core tests run the real request/stream code without the unrelated CGO SQL
// dependency of the full VLM package. SSRF/factory tests remain in that package.
func glmCoreTestClient(t *testing.T) *EnterpriseGLMVLM {
	t.Helper()
	return &EnterpriseGLMVLM{
		modelID: "synthetic-glm-vlm", apiKey: glmTestKey,
		endpoint: enterpriseGLMBaseURL + "/chat/completions", timeout: 2 * time.Second,
		client: &http.Client{Timeout: 2 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
	}
}

type glmRoundTripFunc func(*http.Request) (*http.Response, error)

func (f glmRoundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type glmClosedBody struct {
	io.Reader
	closed bool
}

func (b *glmClosedBody) Close() error { b.closed = true; return nil }

func TestEnterpriseGLMRequestAndVisibleOnly(t *testing.T) {
	v := glmCoreTestClient(t)
	calls := 0
	responseBody := &glmClosedBody{Reader: strings.NewReader(glmGoodStream())}
	v.client.Transport = glmRoundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != enterpriseGLMBaseURL+"/chat/completions" || r.Method != "POST" ||
			r.Header.Get("Authorization") != "Bearer "+glmTestKey || r.Header.Get("Accept") != "text/event-stream" {
			t.Error("wrong endpoint/method/headers")
		}
		var request struct {
			Model     string `json:"model"`
			Stream    bool   `json:"stream"`
			MaxTokens int    `json:"max_tokens"`
			Messages  []struct {
				Role    string `json:"role"`
				Content []struct {
					Type     string `json:"type"`
					Text     string `json:"text"`
					ImageURL struct {
						URL string `json:"url"`
					} `json:"image_url"`
				} `json:"content"`
			} `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.Model != enterpriseGLMModel || !request.Stream || request.MaxTokens != 5000 || len(request.Messages) != 1 {
			t.Fatal("wrong request envelope")
		}
		parts := request.Messages[0].Content
		if len(parts) != 2 || parts[0].Type != "image_url" || !strings.HasPrefix(parts[0].ImageURL.URL, "data:image/png;base64,") ||
			parts[1].Type != "text" || parts[1].Text != "颜色？" {
			t.Fatal("image-first contract lost")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"text/event-stream; charset=utf-8"}}, Body: responseBody}, nil
	})
	got, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "颜色？")
	if err != nil || got != "红色" || calls != 1 || !responseBody.closed {
		t.Fatalf("result=%q error=%v calls=%d closed=%v", got, err, calls, responseBody.closed)
	}
}

func TestEnterpriseGLMRejectInputWithoutSubmission(t *testing.T) {
	v := glmCoreTestClient(t)
	v.client.Transport = glmRoundTripFunc(func(*http.Request) (*http.Response, error) {
		t.Fatal("unexpected provider submission")
		return nil, nil
	})
	for _, images := range [][][]byte{nil, {nil}, {[]byte("not an image")}, {[]byte("<svg/>")}, {[]byte("1"), []byte("2")}, {make([]byte, glmMaxImageBytes+1)}} {
		if got, err := v.Predict(context.Background(), images, "describe"); err == nil || got != "" {
			t.Fatal("invalid image input accepted")
		}
	}
	for _, prompt := range []string{"", "  ", string([]byte{255}), strings.Repeat("p", glmMaxPromptBytes+1)} {
		if got, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, prompt); err == nil || got != "" {
			t.Fatal("invalid prompt accepted")
		}
	}
}

func TestEnterpriseGLMHTTPFailuresNoRetryAndNoSecrets(t *testing.T) {
	for _, status := range []int{200, 202, 302, 401, 429, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			v := glmCoreTestClient(t)
			calls := 0
			body := &glmClosedBody{Reader: strings.NewReader(glmTestKey + glmTestReasoning)}
			v.client.Transport = glmRoundTripFunc(func(*http.Request) (*http.Response, error) {
				calls++
				return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": {"application/json"}}, Body: body}, nil
			})
			got, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "describe")
			if err == nil || got != "" || calls != 1 || !body.closed {
				t.Fatal("failure not safely closed")
			}
			if strings.Contains(err.Error(), glmTestKey) || strings.Contains(err.Error(), glmTestReasoning) {
				t.Fatal("private data leaked")
			}
		})
	}
	v := glmCoreTestClient(t)
	v.client.Transport = glmRoundTripFunc(func(*http.Request) (*http.Response, error) { return nil, errors.New(glmTestKey) })
	_, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "describe")
	if err == nil || strings.Contains(err.Error(), glmTestKey) {
		t.Fatal("transport error leaked")
	}
}

func TestEnterpriseGLMTotalDeadlineDespiteKeepalive(t *testing.T) {
	v := glmCoreTestClient(t)
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "text/event-stream")
		ticker := time.NewTicker(5 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-r.Context().Done():
				return
			case <-ticker.C:
				_, _ = io.WriteString(w, ": keepalive\n\n")
				w.(http.Flusher).Flush()
			}
		}
	}))
	defer s.Close()
	v.endpoint = s.URL
	v.timeout = 60 * time.Millisecond
	start := time.Now()
	got, err := v.Predict(context.Background(), [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "describe")
	if got != "" || !errors.Is(err, context.DeadlineExceeded) || time.Since(start) > time.Second || calls.Load() != 1 {
		t.Fatalf("deadline not enforced: %v calls=%d", err, calls.Load())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = v.Predict(ctx, [][]byte{[]byte("\x89PNG\r\n\x1a\n")}, "describe"); !errors.Is(err, context.Canceled) {
		t.Fatal("parent cancellation lost")
	}
	if calls.Load() != 1 {
		t.Fatal("canceled request submitted")
	}
}

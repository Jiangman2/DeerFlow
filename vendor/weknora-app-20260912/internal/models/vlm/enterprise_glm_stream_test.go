package vlm

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

const glmTestKey = "synthetic-test-credential"
const glmTestReasoning = "DO-NOT-RETURN-HIDDEN-REASONING"

func glmFrame(content string, finish any) string {
	b, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{
		"index": 0, "delta": map[string]string{"content": content}, "finish_reason": finish,
	}}})
	return "data: " + string(b) + "\n\n"
}

func glmGoodStream() string {
	return ": keepalive\n\ndata: {\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"reasoning_content\":\"" + glmTestReasoning + "\"}}]}\n\n" +
		glmFrame("红", nil) + glmFrame("色", "stop") +
		"data: {\"choices\":[],\"usage\":{\"completion_tokens\":2}}\n\ndata: [DONE]\n\n"
}

func TestEnterpriseGLMStreamFailuresNeverPublishPartial(t *testing.T) {
	for name, stream := range map[string]string{
		"empty":             "",
		"missing done":      glmFrame("partial", "stop"),
		"missing finish":    glmFrame("partial", nil) + "data: [DONE]\n\n",
		"unterminated done": glmFrame("partial", "stop") + "data: [DONE]",
		"truncated":         glmFrame("partial", nil) + "data: {",
		"length":            glmFrame("partial", "length") + "data: [DONE]\n\n",
		"content filter":    glmFrame("partial", "content_filter"),
		"after finish":      glmFrame("partial", "stop") + glmFrame("extra", nil) + "data: [DONE]\n\n",
		"empty output":      glmFrame("  ", "stop") + "data: [DONE]\n\n",
		"error":             glmFrame("partial", nil) + "data: {\"error\":{\"message\":\"" + glmTestKey + "\"}}\n\n",
		"null":              "data: null\n\n",
		"bad JSON":          "data: {secret}\n\n",
		"bad utf8":          "data: \xff\n\n",
		"wrong field":       "secret: value\n\n",
		"array content":     "data: {\"choices\":[{\"delta\":{\"content\":[\"secret\"]}}]}\n\n",
		"refusal":           "data: {\"choices\":[{\"delta\":{\"refusal\":\"secret\"}}]}\n\n",
		"tools":             "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{}]}}]}\n\n",
		"function":          "data: {\"choices\":[{\"delta\":{\"function_call\":{}}}]}\n\n",
		"index":             "data: {\"choices\":[{\"index\":1,\"delta\":{\"content\":\"secret\"}}]}\n\n",
		"two choices":       "data: {\"choices\":[{},{}]}\n\n",
		"line limit":        "data: " + strings.Repeat("x", glmMaxLineBytes) + "\n\n",
		"event limit":       strings.Repeat("data: "+strings.Repeat("x", 1024)+"\n", glmMaxEventBytes/1024+1) + "\n",
		"response limit":    strings.Repeat(":"+strings.Repeat("x", 1022)+"\n", glmMaxResponseBytes/1024+1),
		"visible limit":     strings.Repeat(glmFrame(strings.Repeat("v", 8192), nil), glmMaxVisibleBytes/8192+1),
		"event count":       strings.Repeat("data: {}\n\n", glmMaxEvents+1),
	} {
		t.Run(name, func(t *testing.T) {
			got, err := readEnterpriseGLMStream(context.Background(), strings.NewReader(stream))
			if err == nil || got != "" {
				t.Fatal("invalid stream published output")
			}
			if strings.Contains(err.Error(), "secret") || strings.Contains(err.Error(), glmTestKey) {
				t.Fatal("upstream data leaked")
			}
		})
	}
}

func TestEnterpriseGLMCRLFAndMultiline(t *testing.T) {
	stream := "event: message\r\nid: 1\r\nretry: 1000\r\n" + strings.ReplaceAll(glmGoodStream(), "\n", "\r\n")
	got, err := readEnterpriseGLMStream(context.Background(), strings.NewReader(stream))
	if err != nil || got != "红色" {
		t.Fatalf("CRLF failed: %v", err)
	}
	stream = "data: {\n" + "data: \"choices\":[{\"index\":0,\"delta\":{\"content\":\"红色\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n"
	got, err = readEnterpriseGLMStream(context.Background(), strings.NewReader(stream))
	if err != nil || got != "红色" {
		t.Fatalf("multiline failed: %v", err)
	}
}

package vlm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	// EnterpriseGLMWireProfile is an opt-in in ModelParameters.ExtraConfig.
	EnterpriseGLMWireProfile = "enterprise_glm_single_image_sse_v1"
	enterpriseGLMBaseURL     = "https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1"
	enterpriseGLMModel       = "GLM-5.3-Flash"
	glmMaxImageBytes         = 20 << 20
	glmMaxPromptBytes        = 64 << 10
	glmMaxResponseBytes      = 4 << 20
	glmMaxLineBytes          = 256 << 10
	glmMaxEventBytes         = 512 << 10
	glmMaxVisibleBytes       = 256 << 10
	glmMaxEvents             = 32768
)

// EnterpriseGLMVLM adapts the audited enterprise endpoint to the native VLM
// contract. One Predict is exactly one provider POST: no retry or fallback.
// NewVLM still supplies the normal model governor, debug and tracing wrappers.
type EnterpriseGLMVLM struct {
	modelID  string
	apiKey   string
	endpoint string
	timeout  time.Duration
	client   *http.Client
}

func (v *EnterpriseGLMVLM) GetModelName() string { return enterpriseGLMModel }
func (v *EnterpriseGLMVLM) GetModelID() string   { return v.modelID }

func (v *EnterpriseGLMVLM) Predict(ctx context.Context, images [][]byte, prompt string) (string, error) {
	if len(images) != 1 || len(images[0]) == 0 || len(images[0]) > glmMaxImageBytes {
		return "", errors.New("enterprise GLM: exactly one bounded image is required")
	}
	if len(prompt) > glmMaxPromptBytes || !utf8.ValidString(prompt) || strings.TrimSpace(prompt) == "" {
		return "", errors.New("enterprise GLM: invalid prompt")
	}
	mediaType := http.DetectContentType(images[0])
	switch mediaType {
	case "image/png", "image/jpeg", "image/webp":
	default:
		return "", errors.New("enterprise GLM: unsupported image type")
	}
	ctx, cancel := context.WithTimeout(ctx, v.timeout)
	defer cancel()
	// The provider requires image-first, single-image content. Do not silently
	// discard additional images or fan them out into extra paid submissions.
	payload := map[string]any{
		"model": enterpriseGLMModel, "stream": true, "max_tokens": 5000, "temperature": 0.1,
		"messages": []any{map[string]any{
			"role": "user", "content": []any{
				map[string]any{"type": "image_url", "image_url": map[string]string{
					"url": "data:" + mediaType + ";base64," + base64.StdEncoding.EncodeToString(images[0]),
				}},
				map[string]string{"type": "text", "text": prompt},
			},
		}},
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return "", errors.New("enterprise GLM: request encoding failed")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, v.endpoint, bytes.NewReader(body))
	if err != nil {
		return "", errors.New("enterprise GLM: request creation failed")
	}
	req.Header.Set("Authorization", "Bearer "+v.apiKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")
	response, err := v.client.Do(req)
	if err != nil {
		return "", glmTransportError(ctx, "transport failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		// No upstream response text, URL, request ID or credential in error/log.
		return "", fmt.Errorf("enterprise GLM: HTTP status %d", response.StatusCode)
	}
	contentType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || contentType != "text/event-stream" ||
		(response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity") {
		return "", errors.New("enterprise GLM: invalid stream content type")
	}
	result, err := readEnterpriseGLMStream(ctx, response.Body)
	if err != nil {
		return "", err
	}
	if ctx.Err() != nil {
		return "", glmTransportError(ctx, "stream deadline exceeded")
	}
	return result, nil
}

func glmTransportError(ctx context.Context, message string) error {
	if ctx.Err() != nil {
		return fmt.Errorf("enterprise GLM: %w", ctx.Err())
	}
	return errors.New("enterprise GLM: " + message)
}

// Only visible delta.content is represented here. In particular raw
// reasoning_content is never accumulated, returned, or passed to trace hooks.
type glmStreamChunk struct {
	Error   json.RawMessage `json:"error"`
	Choices []struct {
		Index int `json:"index"`
		Delta struct {
			Content      *string         `json:"content"`
			Refusal      json.RawMessage `json:"refusal"`
			ToolCalls    json.RawMessage `json:"tool_calls"`
			FunctionCall json.RawMessage `json:"function_call"`
		} `json:"delta"`
		FinishReason *string `json:"finish_reason"`
	} `json:"choices"`
}

func glmJSONPresent(raw json.RawMessage) bool {
	return len(raw) != 0 && string(bytes.TrimSpace(raw)) != "null"
}

func readEnterpriseGLMStream(ctx context.Context, reader io.Reader) (string, error) {
	limited := &io.LimitedReader{R: reader, N: glmMaxResponseBytes + 1}
	scanner := bufio.NewScanner(limited)
	scanner.Buffer(make([]byte, 4096), glmMaxLineBytes)
	var event, visible strings.Builder
	finished, done, eventCount := false, false, 0
	dispatch := func() error {
		if event.Len() == 0 {
			return nil
		}
		eventCount++
		if eventCount > glmMaxEvents {
			return errors.New("enterprise GLM: stream event limit")
		}
		data := strings.TrimSuffix(event.String(), "\n")
		event.Reset()
		if data == "[DONE]" {
			if !finished || strings.TrimSpace(visible.String()) == "" {
				return errors.New("enterprise GLM: incomplete or empty completion")
			}
			done = true
			return nil
		}
		var chunk glmStreamChunk
		if !json.Valid([]byte(data)) || !strings.HasPrefix(strings.TrimSpace(data), "{") ||
			json.Unmarshal([]byte(data), &chunk) != nil {
			return errors.New("enterprise GLM: malformed stream event")
		}
		if glmJSONPresent(chunk.Error) {
			return errors.New("enterprise GLM: upstream stream error")
		}
		if len(chunk.Choices) == 0 { // usage-only / keepalive frames
			return nil
		}
		if len(chunk.Choices) != 1 || chunk.Choices[0].Index != 0 || finished {
			return errors.New("enterprise GLM: invalid stream choices")
		}
		choice := chunk.Choices[0]
		if glmJSONPresent(choice.Delta.Refusal) || glmJSONPresent(choice.Delta.ToolCalls) || glmJSONPresent(choice.Delta.FunctionCall) {
			return errors.New("enterprise GLM: unsupported stream action")
		}
		if choice.Delta.Content != nil {
			if visible.Len()+len(*choice.Delta.Content) > glmMaxVisibleBytes {
				return errors.New("enterprise GLM: visible output limit")
			}
			visible.WriteString(*choice.Delta.Content)
		}
		if choice.FinishReason != nil {
			if *choice.FinishReason != "stop" {
				return errors.New("enterprise GLM: unfinished completion")
			}
			finished = true
		}
		return nil
	}
	for scanner.Scan() {
		if ctx.Err() != nil {
			return "", glmTransportError(ctx, "stream canceled")
		}
		if limited.N <= 0 {
			return "", errors.New("enterprise GLM: response byte limit")
		}
		line := scanner.Text()
		if !utf8.ValidString(line) {
			return "", errors.New("enterprise GLM: invalid stream encoding")
		}
		if line == "" {
			if err := dispatch(); err != nil {
				return "", err
			}
			if done {
				return strings.TrimSpace(visible.String()), nil
			}
			continue
		}
		if strings.HasPrefix(line, "data:") {
			data := strings.TrimPrefix(line, "data:")
			data = strings.TrimPrefix(data, " ")
			if event.Len()+len(data)+1 > glmMaxEventBytes {
				return "", errors.New("enterprise GLM: stream event byte limit")
			}
			event.WriteString(data)
			event.WriteByte('\n')
		} else if !strings.HasPrefix(line, ":") && !strings.HasPrefix(line, "id:") &&
			!strings.HasPrefix(line, "event:") && !strings.HasPrefix(line, "retry:") {
			return "", errors.New("enterprise GLM: invalid SSE field")
		}
	}
	// An EOF (including after finish_reason) without a delimited [DONE] event
	// is not success. A transport interruption never publishes partial text.
	return "", glmTransportError(ctx, "stream incomplete or limit exceeded")
}

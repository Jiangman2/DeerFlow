// A once-only native VLM transport probe for an isolated candidate container.
// It is not a tenant model update or an Asynq/business enrichment acceptance.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"image"
	"image/color"
	"image/png"
	"io"
	"os"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/models/vlm"
	"github.com/Tencent/WeKnora/internal/types"
)

func main() {
	once := flag.Bool("probe-once", false, "submit exactly one synthetic image; no automatic retry")
	flag.Parse()
	if !*once || flag.NArg() != 0 {
		emitFailure("explicit_probe_once_required", false)
	}
	if logger.LLMDebugEnabled() {
		emitFailure("probe_requires_debug_logging_disabled_at_startup", false)
	}
	// Never accept a key in command arguments or print raw errors. The parent
	// launcher obtains hidden input and streams this JSON on stdin, not a file.
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, 8193))
	if err != nil || len(raw) > 8192 {
		emitFailure("credential_input_invalid", false)
	}
	var input struct {
		APIKey string `json:"api_key"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&input) != nil {
		emitFailure("credential_input_invalid", false)
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		emitFailure("credential_input_invalid", false)
	}
	model := &types.Model{ID: "isolated-glm-native-probe", Name: "GLM-5.3-Flash", Source: types.ModelSourceRemote,
		Parameters: types.ModelParameters{
			BaseURL: "https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1", APIKey: input.APIKey,
			InterfaceType: "openai", Provider: "openai", MaxConcurrency: 1,
			ExtraConfig: map[string]string{"wire_profile": vlm.EnterpriseGLMWireProfile},
		}}
	client, err := vlm.NewVLM(vlm.ConfigFromModel(model, "", ""), nil)
	if err != nil {
		emitFailure("native_client_initialization_failed", false)
	}
	img := image.NewRGBA(image.Rect(0, 0, 128, 128))
	for y := 0; y < 128; y++ {
		for x := 0; x < 128; x++ {
			img.SetRGBA(x, y, color.RGBA{R: 255, A: 255})
		}
	}
	var encoded bytes.Buffer
	if png.Encode(&encoded, img) != nil {
		emitFailure("synthetic_image_failed", false)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	start := time.Now()
	// Do not wrap or retry this call. On any uncertain outcome the host uses
	// its saved attempted receipt rather than launching another probe.
	result, err := client.Predict(ctx, [][]byte{encoded.Bytes()}, "图片的主要颜色是什么？只回答颜色名称，不要解释。")
	if err != nil {
		emitFailure("native_predict_failed", true)
	}
	matched := strings.Contains(result, "红") || strings.Contains(strings.ToLower(result), "red")
	state := "FAIL"
	if matched {
		state = "PASS"
	}
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{
		"native_glm_probe": state, "scope": "NATIVE_GO_VLM_CLIENT_SYNTHETIC_IMAGE",
		"maximum_submissions": 1, "elapsed_seconds": time.Since(start).Seconds(),
		"synthetic_red_check": matched, "visible_characters": len([]rune(result)),
		"raw_response_saved": false, "credentials_saved": false, "production_model_changed": false,
		"native_enrichment_acceptance": "NOT_TESTED", "ready_for_rollout": false,
	})
	if !matched {
		os.Exit(1)
	}
}

func emitFailure(check string, attempted bool) {
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{
		"native_glm_probe": "UNCONFIRMED", "failed_check": check,
		"provider_submission_may_have_occurred": attempted, "automatic_retry": false,
		"maximum_submissions": 1, "credentials_saved": false, "raw_error_printed": false,
		"production_model_changed": false, "ready_for_rollout": false,
	})
	os.Exit(1)
}

// Offline reproduction of the exact Viper/mapstructure pointer allocation path.
// No WeKnora server, stores, provider clients, or network calls are imported.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/go-viper/mapstructure/v2"
	"github.com/spf13/viper"
)

type PromptTemplateStructured struct {
	Description string `json:"description"`
}
type FebriText struct {
	WithTag string `yaml:"with_tag" json:"with_tag"`
	WithNoTag string `yaml:"with_no_tag" json:"with_no_tag"`
}
type ExtractManagerConfig struct {
	ExtractGraph *PromptTemplateStructured `yaml:"extract_graph" json:"extract_graph"`
	ExtractEntity *PromptTemplateStructured `yaml:"extract_entity" json:"extract_entity"`
	FabriText *FebriText `yaml:"fabri_text" json:"fabri_text"`
}
type SummaryConfig struct {
	MaxInputChars int `yaml:"max_input_chars" json:"max_input_chars"`
}
type ConversationConfig struct {
	Summary *SummaryConfig `yaml:"summary" json:"summary"`
}
type Config struct {
	ExtractManager *ExtractManagerConfig `yaml:"extract" json:"extract"`
	Conversation *ConversationConfig `yaml:"conversation" json:"conversation"`
}

func load(path string) Config {
	raw, err := os.ReadFile(path)
	if err != nil { panic(err) }
	viper.Reset()
	viper.SetConfigType("yaml")
	viper.AutomaticEnv()
	viper.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))
	if err := viper.ReadConfig(strings.NewReader(string(raw))); err != nil { panic(err) }
	var cfg Config
	if err := viper.Unmarshal(&cfg, func(dc *mapstructure.DecoderConfig) { dc.TagName = "yaml" }); err != nil { panic(err) }
	return cfg
}

func accessPanics(cfg Config) (panicked bool) {
	defer func() { panicked = recover() != nil }()
	// Same dereference as NewPluginExtractEntity, extract_entity.go:41.
	_ = cfg.ExtractManager.ExtractEntity
	return
}

func main() {
	if len(os.Args) != 3 { panic("two fixture paths required") }
	before, after := load(os.Args[1]), load(os.Args[2])
	if before.ExtractManager != nil || !accessPanics(before) { panic("original nil panic not reproduced") }
	if after.ExtractManager == nil || after.ExtractManager.ExtractEntity == nil ||
		after.ExtractManager.ExtractGraph == nil || after.ExtractManager.FabriText == nil || accessPanics(after) {
		panic("fixed extract pointers absent")
	}
	if after.Conversation == nil || after.Conversation.Summary == nil || after.Conversation.Summary.MaxInputChars != 16384 {
		panic("fixed summary pointer absent")
	}
	raw, _ := json.Marshal(map[string]any{
		"fixture_config_reproduction":"PASS", "original_extract_nil":true,
		"original_exact_dereference_panics":true, "fixed_extract_pointers_present":true,
		"fixed_summary_pointer_present":true, "provider_requests":0,
		"scope":"PINNED_VIPER_MAPSTRUCTURE_SHAPE_NOT_FULL_APP_ACCEPTANCE",
	})
	fmt.Println(string(raw))
}

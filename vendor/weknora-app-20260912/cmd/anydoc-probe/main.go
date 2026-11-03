package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/Tencent/WeKnora/internal/infrastructure/docparser/anydoc"
)

const maxInputBytes = 100 << 20

type probeResult struct {
	Available       bool   `json:"available"`
	Version         string `json:"version"`
	Format          string `json:"format"`
	InputBytes      int    `json:"input_bytes"`
	InputSHA256     string `json:"input_sha256"`
	MarkdownBytes   int    `json:"markdown_bytes"`
	MarkdownSHA256  string `json:"markdown_sha256"`
	AssetCount      int    `json:"asset_count"`
	AssetsError     bool   `json:"assets_error"`
	OCRRequired     bool   `json:"ocr_required"`
	SelfTest        bool   `json:"self_test"`
}

func digest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func emit(value probeResult) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(true)
	return encoder.Encode(value)
}

func convert(data []byte, format string, withAssets bool, selfTest bool) error {
	if !anydoc.Available() {
		return fmt.Errorf("anydoc binding is unavailable")
	}
	if anydoc.Version() != "0.1.9" {
		return fmt.Errorf("unexpected anydoc version %q", anydoc.Version())
	}
	result, err := anydoc.Convert(data, anydoc.Options{Format: format, WithAssets: withAssets})
	if err != nil {
		if anydoc.PDFNeedsOCR(err) {
			return emit(probeResult{
				Available: true, Version: anydoc.Version(), Format: format,
				InputBytes: len(data), InputSHA256: digest(data), OCRRequired: true,
				SelfTest: selfTest,
			})
		}
		return fmt.Errorf("conversion failed: %w", err)
	}
	if strings.TrimSpace(result.Markdown) == "" {
		return fmt.Errorf("conversion returned empty markdown")
	}
	return emit(probeResult{
		Available: true, Version: anydoc.Version(), Format: format,
		InputBytes: len(data), InputSHA256: digest(data),
		MarkdownBytes: len([]byte(result.Markdown)),
		MarkdownSHA256: digest([]byte(result.Markdown)),
		AssetCount: len(result.Assets), AssetsError: result.AssetsError != nil,
		SelfTest: selfTest,
	})
}

func main() {
	selfTest := flag.Bool("self-test", false, "run the deterministic linked-engine smoke test")
	input := flag.String("input", "", "read one local document without printing its content")
	format := flag.String("format", "", "explicit format, for example docx, xlsx, or pdf")
	withAssets := flag.Bool("with-assets", false, "exercise embedded-image extraction")
	flag.Parse()

	if *selfTest == (*input != "") {
		fmt.Fprintln(os.Stderr, "choose exactly one of --self-test or --input")
		os.Exit(2)
	}

	if *selfTest {
		data := []byte("quarter,widgets\nQ1,12\nQ2,15\n")
		if err := convert(data, "csv", false, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	info, err := os.Stat(*input)
	if err != nil {
		fmt.Fprintln(os.Stderr, "input stat failed")
		os.Exit(1)
	}
	if !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > maxInputBytes {
		fmt.Fprintln(os.Stderr, "input must be a non-empty regular file no larger than 100 MiB")
		os.Exit(1)
	}
	data, err := os.ReadFile(*input)
	if err != nil {
		fmt.Fprintln(os.Stderr, "input read failed")
		os.Exit(1)
	}
	if err := convert(data, strings.TrimSpace(strings.ToLower(*format)), *withAssets, false); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}


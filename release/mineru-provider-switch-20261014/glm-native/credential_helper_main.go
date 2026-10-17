// Release-only helper. Linked with the exact vendored utils/crypto.go (package
// declaration changed only). Never linked with WeKnora application init code.
package main

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"io"
	"os"
	"strings"
)

type request struct {
	Mode string `json:"mode"`
	APIKey string `json:"api_key"`
	Ciphertext string `json:"ciphertext"`
}

func checkKey(s string) bool {
	if len(s) == 0 || len(s) > 4096 || strings.HasPrefix(s, EncPrefix) { return false }
	for _, c := range []byte(s) { if c < 33 || c > 126 { return false } }
	return true
}

func fail() {
	// No panic, raw exception, input, key hash or stack trace on failures.
	os.Stdout.Write([]byte("{\"credential_helper\":\"FAIL\"}\n"))
	os.Exit(1)
}

func main() {
	if len(os.Args) != 1 { fail() }
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, 16385))
	if err != nil || len(raw) > 16384 { fail() }
	var r request
	d := json.NewDecoder(bytes.NewReader(raw)); d.DisallowUnknownFields()
	if d.Decode(&r) != nil { fail() }
	var extra any
	if d.Decode(&extra) != io.EOF { fail() }
	key := GetAESKey()
	if len(key) != 32 { fail() }
	if r.Mode == "self-test" {
		if r.APIKey != "" || r.Ciphertext != "" { fail() }
		r.APIKey = "RELEASE-SYNTHETIC-NONCREDENTIAL"
	} else if r.Mode != "encrypt" && r.Mode != "verify" { fail() }
	if !checkKey(r.APIKey) { fail() }
	var cipher string
	if r.Mode == "verify" {
		if !strings.HasPrefix(r.Ciphertext, EncPrefix) || len(r.Ciphertext) > 5600 { fail() }
		cipher = r.Ciphertext
	} else {
		if r.Ciphertext != "" { fail() }
		cipher, err = EncryptAESGCM(r.APIKey, key)
		if err != nil || !strings.HasPrefix(cipher, EncPrefix) || cipher == r.APIKey { fail() }
	}
	plain, err := DecryptStoredSecret(cipher)
	if err != nil || subtle.ConstantTimeCompare([]byte(plain), []byte(r.APIKey)) != 1 { fail() }
	out := map[string]any{"credential_helper":"PASS", "mode":r.Mode, "native_roundtrip":true}
	if r.Mode == "encrypt" { out["ciphertext"] = cipher }
	if json.NewEncoder(os.Stdout).Encode(out) != nil { os.Exit(1) }
}

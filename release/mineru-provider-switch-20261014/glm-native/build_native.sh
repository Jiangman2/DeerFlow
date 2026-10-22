#!/bin/bash
# Only inside an offline, cgroup-bounded, private candidate builder.
set -Eeuo pipefail
umask 022
test "$(uname -m)" = aarch64
test "$(getconf GNU_LIBC_VERSION)" = 'glibc 2.36'
test "$(go env GOVERSION)" = go1.26.8
test "$(go env GOOS)/$(go env GOARCH)" = linux/arm64
for binary in gcc g++ make pkg-config sha256sum python3 readelf dpkg dpkg-query; do
    command -v "$binary" >/dev/null
done
mkdir -p /out/evidence /opt/rust-installer /opt/rust /opt/cargo
cd /inputs
sha256sum --check INPUTS.SHA256
cd /src
sha256sum --check SOURCE.SHA256
cp SOURCE.SHA256 /out/evidence/source.sha256
sha256sum go.mod go.sum third_party/anydoc-go/Cargo.lock > /out/evidence/dependency-locks.before.sha256

# Install locked Debian ARM64 packages ONLY inside this disposable builder.
# PostgreSQL remains the configured production database. SQLite is an imported
# compile-time backend; omitting its headers breaks a full server compilation.
test "$(dpkg --print-architecture)" = arm64
dpkg -i /inputs/debian/libsqlite3-0_3.40.1-2+deb12u2_arm64.deb \
    /inputs/debian/libsqlite3-dev_3.40.1-2+deb12u2_arm64.deb
test "$(dpkg-query -W -f='${Version}' libsqlite3-dev:arm64)" = '3.40.1-2+deb12u2'
test "$(dpkg-query -W -f='${Version}' libsqlite3-0:arm64)" = '3.40.1-2+deb12u2'
printf '%s\n' '#include <sqlite3.h>' \
    'int main(void) { sqlite3 *db = 0; if (sqlite3_open(":memory:", &db) != SQLITE_OK) return 1; return sqlite3_close(db); }' \
    | gcc -Werror -x c - -o /out/sqlite-build-probe -lsqlite3
/out/sqlite-build-probe
# Check the exact CGO dependency before spending time on the Rust/AnyDoc build.
go build -p 2 github.com/asg017/sqlite-vec-go-bindings/cgo
printf '%s\n' 'sqlite_header_link_and_cgo=PASS builder_only=true database_configuration_changed=false' \
    > /out/evidence/sqlite-build-preflight.txt
printf '%s\n' 'sqlite_header_link_and_cgo=PASS starting_anydoc_build=true'

# These are hash-checked local archives, never shell downloads.
for component in rustc cargo rust-std; do
    archive="${component}-1.99.1-aarch64-unknown-linux-gnu"
    python3 -I -B /unpack_rust.py "/inputs/rust/${archive}.tar.xz" /opt/rust-installer
    "/opt/rust-installer/${archive}/install.sh" --prefix=/opt/rust --disable-ldconfig
done
test "$(rustc --version | cut -d ' ' -f 2)" = 1.99.1
test "$(cargo --version | cut -d ' ' -f 2)" = 1.99.1
rustc --version --verbose > /out/evidence/rust-toolchain.txt
go version > /out/evidence/go-toolchain.txt
gcc --version > /out/evidence/c-compiler.txt
printf '%s\n' '[source.crates-io]' 'replace-with = "local-locked"' \
    '[source.local-locked]' 'local-registry = "/inputs/cargo-local-registry"' \
    '[net]' 'offline = true' > /opt/cargo/config.toml
test -f third_party/anydoc-go/patched-anydoc/.weknora-patched
test "$(<third_party/anydoc-go/patched-anydoc/.weknora-patched)" = 0.1.9
grep -Fx 'pub use render::markdown::document_to_markdown;' third_party/anydoc-go/patched-anydoc/src/lib.rs
cargo build --offline --locked --release --manifest-path third_party/anydoc-go/Cargo.toml --target aarch64-unknown-linux-gnu
mkdir -p third_party/anydoc-go/lib/linux_arm64_gnu
install -m 0644 third_party/anydoc-go/target/aarch64-unknown-linux-gnu/release/libanydoc_go.a \
    third_party/anydoc-go/lib/linux_arm64_gnu/libanydoc_go.a

# go.sum validation remains active against pinned local proxy bytes. External
# sumdb lookup is off ONLY in this disconnected build; acquisition used sumdb.
go test -p 2 -tags anydoc -count=1 -timeout 180s -json ./internal/models/vlm > /out/evidence/vlm-tests.jsonl
go vet -p 2 -tags anydoc ./internal/models/vlm ./internal/infrastructure/docparser/...
go test -p 2 -tags anydoc -count=1 -timeout 300s -json ./internal/infrastructure/docparser/... > /out/evidence/anydoc-tests.jsonl
go test -p 2 -count=1 -timeout 120s -json ./internal/utils -run '^TestNewSSRFSafeTransportConfiguresProxyResolver$' > /out/evidence/ssrf-regression.jsonl
go build -p 2 -tags anydoc -trimpath -o /out/weknora-anydoc-probe ./cmd/anydoc-probe
/out/weknora-anydoc-probe --self-test > /out/evidence/anydoc-self-test.json
go build -p 2 -tags anydoc -trimpath -o /out/weknora-glm-probe ./cmd/glm-vlm-probe
VERSION=0.8.0-enterprise-proxy1-anydoc1-glm1 \
    COMMIT_ID="$(sha256sum SOURCE.SHA256 | cut -d ' ' -f 1)" \
    BUILD_TIME=2026-10-16 GO_VERSION=go1.26.8 \
    GOFLAGS='-mod=readonly -p=2 -trimpath' make build-prod GO_BUILD_TAGS=anydoc
install -m 0755 WeKnora /out/WeKnora
go mod verify > /out/evidence/go-mod-verify.txt
sha256sum --check /out/evidence/dependency-locks.before.sha256
go version -m /out/WeKnora > /out/evidence/server-buildinfo.txt
readelf -h /out/WeKnora > /out/evidence/server-elf.txt
sha256sum /out/WeKnora /out/weknora-anydoc-probe /out/weknora-glm-probe > /out/evidence/binaries.sha256
printf '%s\n' 'offline_native_qualification=PASS provider_requests=0 native_intranet_acceptance=NOT_TESTED'

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $env:DEER_FLOW_ROOT) {
  $env:DEER_FLOW_ROOT = $repoRoot
}
if (-not $env:HOME) {
  $env:HOME = $env:USERPROFILE
}

if (-not $env:UV_CACHE_PATH) {
  $env:UV_CACHE_PATH = (Join-Path $repoRoot ".uv-cache")
}
if (-not $env:PNPM_STORE_PATH) {
  $env:PNPM_STORE_PATH = (Join-Path $repoRoot ".pnpm-store")
}
if (-not $env:KUBECONFIG_HOST_PATH) {
  $env:KUBECONFIG_HOST_PATH = (Join-Path $repoRoot ".kube\\config")
}

New-Item -ItemType Directory -Force -Path $env:UV_CACHE_PATH | Out-Null
New-Item -ItemType Directory -Force -Path $env:PNPM_STORE_PATH | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot ".claude") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot ".codex") | Out-Null

docker compose -f (Join-Path $repoRoot "docker\\docker-compose-dev.yaml") up -d --build
docker compose -f (Join-Path $repoRoot "docker\\docker-compose-dev.yaml") ps

param(
  [string]$OutZip = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$outDir = Join-Path $repoRoot "deploy\non-container\out"
if ([string]::IsNullOrWhiteSpace($OutZip)) {
  $OutZip = Join-Path $outDir "deerflow-transfer-standalone.zip"
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
if (Test-Path $OutZip) { Remove-Item -Force $OutZip }

$sevenZip = "C:\Program Files\7-Zip\7z.exe"
if (-not (Test-Path $sevenZip)) {
  $sevenZip = "C:\Program Files (x86)\7-Zip\7z.exe"
}
if (-not (Test-Path $sevenZip)) {
  throw "7z.exe not found. Install 7-Zip first."
}

Set-Location $repoRoot

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  throw "pnpm not found on PATH. Install pnpm first."
}

$env:SKIP_ENV_VALIDATION = "1"
$env:BETTER_AUTH_SECRET = "offline-build-placeholder-secret"
$env:BETTER_AUTH_BASE_URL = "http://localhost:3000"

Push-Location "$repoRoot\frontend"
try {
  pnpm install --frozen-lockfile
  pnpm build
} finally {
  Pop-Location
}

if (-not (Test-Path "$repoRoot\frontend\.next\standalone\server.js")) {
  throw "Next standalone output not found at frontend/.next/standalone/server.js"
}

& $sevenZip a -tzip $OutZip ".\*" `
  "-xr!.git" `
  "-xr!backend\.venv" `
  "-xr!backend\.deer-flow" `
  "-x!frontend\node_modules" `
  "-x!node_modules" `
  "-xr!.venv" `
  "-xr!_upstream_tmp" `
  "-xr!deploy\non-container\out" `
  "-xr!deploy\zero-build\out" `
  "-xr!local_minio\data" `
  "-xr!local_minio\minio.exe" `
  "-xr!frontend\.next\cache" `
  "-xr!frontend\.next\dev"

Write-Output $OutZip

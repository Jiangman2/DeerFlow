param(
  [string]$OutZip = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$outDir = Join-Path $repoRoot "deploy\non-container\out"
if ([string]::IsNullOrWhiteSpace($OutZip)) {
  $OutZip = Join-Path $outDir "deerflow-transfer.zip"
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

& $sevenZip a -tzip $OutZip ".\*" `
  "-xr!.git" `
  "-xr!backend\.venv" `
  "-xr!backend\.deer-flow" `
  "-xr!frontend\node_modules" `
  "-xr!frontend\.next" `
  "-xr!node_modules" `
  "-xr!.venv" `
  "-xr!_upstream_tmp" `
  "-xr!deploy\non-container\out" `
  "-xr!deploy\zero-build\out" `
  "-xr!local_minio\data" `
  "-xr!local_minio\minio.exe"

Write-Output $OutZip


$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$compose = Join-Path $repoRoot "local_minio\\docker-compose.yml"
$dataDir = Join-Path $repoRoot "local_minio\\data"

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

docker compose -f $compose up -d
docker compose -f $compose ps

$bucket = $env:MINIO_BUCKET
if (-not $bucket) { $bucket = "deerflow" }
$accessKey = $env:MINIO_ROOT_USER
if (-not $accessKey) { $accessKey = "minioadmin" }
$secretKey = $env:MINIO_ROOT_PASSWORD
if (-not $secretKey) { $secretKey = "minioadmin" }

# Ensure the default bucket exists (idempotent).
Push-Location (Join-Path $repoRoot "backend")
$env:MINIO_ENDPOINT = "http://127.0.0.1:9000"
$env:MINIO_ACCESS_KEY = $accessKey
$env:MINIO_SECRET_KEY = $secretKey
$env:MINIO_BUCKET = $bucket
$env:MINIO_SECURE = "false"
uv run python -c "import os; from minio import Minio; ep=os.environ['MINIO_ENDPOINT'].replace('http://','').replace('https://',''); c=Minio(ep, access_key=os.environ['MINIO_ACCESS_KEY'], secret_key=os.environ['MINIO_SECRET_KEY'], secure=False); b=os.environ['MINIO_BUCKET']; exists=c.bucket_exists(b); (c.make_bucket(b) if not exists else None); print('bucket_ready', b)" | Out-Null
Pop-Location

Write-Host "ready:"
Write-Host " - minio s3:      http://127.0.0.1:9000"
Write-Host " - minio console: http://127.0.0.1:9001"

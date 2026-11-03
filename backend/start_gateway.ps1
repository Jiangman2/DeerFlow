# 本地启动 Gateway（Windows 直连模式，无 nginx / make）
$root = "f:\jupyter\6_deerflow_cnooc\backup\3_git\DeerFlow-codex-enterprise-consolidation-20260716"
Set-Location "$root\backend"

# 逐行加载根目录 .env 到进程环境
Get-Content "$root\.env" | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
    }
}

$env:DEER_FLOW_PROJECT_ROOT = $root
$env:DEER_FLOW_HOME = "$root\backend\.deer-flow"
$env:PYTHONPATH = "."
$env:PYTHONIOENCODING = "utf-8"

New-Item -ItemType Directory -Force -Path "$root\backend\logs" | Out-Null
uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 *>> "$root\backend\logs\gateway.log"

# 本地启动前端（Windows 直连模式，pnpm dev / next --turbo）
$root = "f:\jupyter\6_deerflow_cnooc\backup\3_git\DeerFlow-codex-enterprise-consolidation-20260716"
Set-Location "$root\frontend"

New-Item -ItemType Directory -Force -Path "$root\frontend\logs" | Out-Null

# pnpm 在 PowerShell 中解析为 pnpm.ps1；输出追加到日志便于排查启动问题
pnpm dev *>> "$root\frontend\logs\frontend.log"

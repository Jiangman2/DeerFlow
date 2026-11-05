# 本地启动前端（Windows 直连模式，无 nginx / make）
$root = "f:\jupyter\6_deerflow_cnooc\backup\6_latest_df_0716\DeerFlow-codex-enterprise-consolidation-20260716"

# 前置 node/nvm 与全局包路径，避免非交互 shell 报 "No active Node.js version"
$env:PATH = "D:\no_space_app\nvm\v22.18;D:\no_space_app\node\node_global;" + $env:PATH

Set-Location "$root\frontend"
New-Item -ItemType Directory -Force -Path "$root\frontend\logs" | Out-Null
& "D:\no_space_app\node\node_global\pnpm.cmd" run dev *>> "$root\frontend\logs\frontend.log"

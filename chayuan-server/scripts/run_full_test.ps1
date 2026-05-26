# ============================================================
# 察元一键全栈测试脚本（Windows PowerShell）
# ============================================================
#
# 用途：从 0 到全绿，一条命令搞定：
#   1. docker compose 起 postgres + redis + minio + milvus + langfuse + ollama
#   2. 等待所有服务 healthy
#   3. 跑 setup_local.py 自动改 yaml
#   4. 跑 pytest 白盒（快速）
#   5. 启动察元 API
#   6. 跑 smoke_test.py 黑盒
#   7. 输出汇总报告
#
# 使用：
#   cd <project_root>
#   pwsh -File scripts/run_full_test.ps1                # 默认
#   pwsh -File scripts/run_full_test.ps1 -SkipDocker   # 假定 docker 已起
#   pwsh -File scripts/run_full_test.ps1 -SkipOllama   # 本机已有 Ollama
#   pwsh -File scripts/run_full_test.ps1 -Python D:\soft\conda_envs\py312\python.exe

param(
    [string]$Python = "python",
    [switch]$SkipDocker,
    [switch]$SkipOllama,
    [switch]$SkipLangfuse,
    [switch]$SkipWhiteBox,
    [switch]$SkipSmoke,
    [switch]$SkipE2E,
    [string]$ApiBase = "http://127.0.0.1:62581",
    [string]$WebUIBase = "http://127.0.0.1:8501",
    [string]$LangfuseBase = "http://127.0.0.1:3000",
    [string]$MinioBase = "http://127.0.0.1:9001"
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Step([string]$msg) {
    Write-Host "`n========== $msg ==========" -ForegroundColor Cyan
}

function Success([string]$msg) { Write-Host "  ✅ $msg" -ForegroundColor Green }
function Warn([string]$msg)    { Write-Host "  ⚠️  $msg" -ForegroundColor Yellow }
function Fail([string]$msg)    { Write-Host "  ❌ $msg" -ForegroundColor Red }

# ------------------------------------------------------------
# 1. Docker 栈
# ------------------------------------------------------------
if (-not $SkipDocker) {
    Step "1. 部署 Docker 依赖栈"
    Push-Location docker/dev-stack
    if (-not (Test-Path .env)) {
        Copy-Item .env.example .env
        Success ".env 由 .env.example 复制"
    }
    $profiles = @()
    if (-not $SkipOllama)   { $profiles += "ollama" }
    if (-not $SkipLangfuse) { $profiles += "langfuse" }

    $cmd = "docker compose"
    foreach ($p in $profiles) { $cmd += " --profile $p" }
    $cmd += " up -d"
    Write-Host "  running: $cmd"
    Invoke-Expression $cmd
    Pop-Location

    Step "2. 等待核心服务 healthy"
    $maxWait = 120
    $interval = 5
    $waited = 0
    while ($waited -lt $maxWait) {
        $pg_ok   = (docker inspect -f '{{.State.Health.Status}}' chayuan-dev-postgres 2>$null) -eq "healthy"
        $redis_ok = (docker inspect -f '{{.State.Health.Status}}' chayuan-dev-redis 2>$null) -eq "healthy"
        $minio_ok = (docker inspect -f '{{.State.Health.Status}}' chayuan-dev-minio 2>$null) -eq "healthy"
        $milvus_ok = (docker inspect -f '{{.State.Health.Status}}' chayuan-dev-milvus 2>$null) -eq "healthy"
        $status = "pg=$pg_ok redis=$redis_ok minio=$minio_ok milvus=$milvus_ok"
        Write-Host "  [$waited s] $status"
        if ($pg_ok -and $redis_ok -and $minio_ok -and $milvus_ok) {
            Success "核心 4 件套就绪"
            break
        }
        Start-Sleep $interval
        $waited += $interval
    }
    if ($waited -ge $maxWait) { Warn "超时：部分服务可能仍在启动；继续" }
} else {
    Warn "跳过 Docker 部署（--SkipDocker）"
}

# ------------------------------------------------------------
# 2. 自动配置 yaml
# ------------------------------------------------------------
Step "3. 自动探活 + 写配置"
& $Python scripts/setup_local.py --ollama-llm qwen3:4b --ollama-embed nomic-embed-text
if ($LASTEXITCODE -ne 0) {
    Warn "setup_local.py 部分探活未通过（通常 langfuse 首次启动较慢或未启用）"
}

# ------------------------------------------------------------
# 3. 白盒 pytest
# ------------------------------------------------------------
if (-not $SkipWhiteBox) {
    Step "4. 白盒 pytest（快速）"
    Push-Location libs/chayuan-server
    & $Python -m pytest tests/knowledge_source `
        --tb=line -q `
        --deselect tests/knowledge_source/test_raptor_graphrag.py::test_graphrag_augment_returns_local_doc `
        --ignore=tests/knowledge_source/test_sql_containers.py `
        --ignore=tests/knowledge_source/test_text2sql_golden.py `
        --ignore=tests/knowledge_source/test_chat_graph.py
    $whiteBoxExit = $LASTEXITCODE
    Pop-Location
    if ($whiteBoxExit -eq 0) { Success "白盒全绿" } else { Fail "白盒失败 exit=$whiteBoxExit" }
} else {
    Warn "跳过白盒"
}

# ------------------------------------------------------------
# 4. 启动察元 API（后台）
# ------------------------------------------------------------
Step "5. 启动察元 API（后台）"
# 检查是否已在跑
try {
    $resp = Invoke-WebRequest -Uri "$ApiBase/healthz" -UseBasicParsing -TimeoutSec 3
    if ($resp.StatusCode -eq 200) {
        Success "察元已在 $ApiBase 运行"
    }
} catch {
    Write-Host "  察元未运行；请另开终端执行："
    Write-Host "    & $Python -m chayuan.startup --all" -ForegroundColor Yellow
    Write-Host "  或：chayuan start -a"
    Warn "脚本继续；若察元未起 smoke 会 fail"
    Start-Sleep 5
}

# ------------------------------------------------------------
# 5. 黑盒 smoke
# ------------------------------------------------------------
if (-not $SkipSmoke) {
    Step "6. 黑盒 smoke_test"
    & $Python scripts/smoke_test.py --base $ApiBase --json smoke_report.json --junit smoke_junit.xml
    $smokeExit = $LASTEXITCODE
    if ($smokeExit -eq 0) { Success "smoke 全绿" } else { Fail "smoke 有失败 exit=$smokeExit" }
    Write-Host "  报告：smoke_report.json / smoke_junit.xml"
}

# ------------------------------------------------------------
# 6. 浏览器 E2E
# ------------------------------------------------------------
if (-not $SkipE2E) {
    Step "7. 浏览器 E2E（Playwright）"
    # 确保 playwright 和 chromium 已装
    & $Python -c "import playwright" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  playwright 未装，安装中..."
        & $Python -m pip install -q playwright
        & $Python -m playwright install chromium
    }
    $e2eArgs = @(
        "scripts/e2e_test.py",
        "--api-base", $ApiBase,
        "--webui-base", $WebUIBase,
        "--langfuse-base", $LangfuseBase,
        "--minio-base", $MinioBase,
        "--junit", "e2e_junit.xml"
    )
    # WebUI 探活；未起则跳过 webui 组
    $webuiUp = $false
    try {
        $r = Invoke-WebRequest -Uri "$WebUIBase/" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -lt 500) { $webuiUp = $true }
    } catch {}
    if (-not $webuiUp) {
        Warn "WebUI $WebUIBase 未运行；E2E 将跳过 webui 组"
        $e2eArgs += "--skip-webui"
    }
    & $Python @e2eArgs
    $e2eExit = $LASTEXITCODE
    if ($e2eExit -eq 0) { Success "E2E 全绿" } else { Fail "E2E 有失败 exit=$e2eExit" }
    Write-Host "  报告：e2e_junit.xml  截图：e2e_artifacts/"
}

# ------------------------------------------------------------
# 7. 汇总
# ------------------------------------------------------------
Step "全流程完成"
Write-Host "下一步建议："
Write-Host "  - 打开 Langfuse：http://localhost:3000 → 注册第一个管理员"
Write-Host "  - 打开 MinIO 控制台：http://localhost:9001 → 用 minioadmin/minioadmin 登录"
Write-Host "  - 打开察元 WebUI：http://localhost:8501"

#requires -Version 5.1
<#
.SYNOPSIS
    察元 AI 模型 / 服务 诊断脚本 — 排查"扫描挂载扫不到 + 服务起不来"。

.DESCRIPTION
    一次性收集:
      0. 环境:CHAYUAN_ROOT 解析、state.json
      1. <CHAYUAN_ROOT>/models 目录树 + 每个模型目录里的关键文件
      2. local_models.json(扫描索引现状)
      3. vendor/bundled_models + vendor/services(install-vendor 装的东西)
      4. llama-server / whisper-server 二进制是否就位
      5. 后端 API:健康、/v1/models、scan_and_mount 实测
    全部 best-effort,缺啥跳啥,不会中断。

.USAGE
    .\scripts\diagnose-models.ps1
    把完整输出整段复制回贴给开发者。
#>
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); chcp 65001 > $null 2>&1 } catch {}

function Sec($t) { Write-Host ''; Write-Host ('======= ' + $t + ' =======') -ForegroundColor Cyan }
function KV($k, $v) { Write-Host ('  ' + $k.PadRight(22) + ': ' + $v) }
# PS 5.1 没有 ?? 空值合并,用显式 helper
function Def($v, $d) { if ($v) { return $v } else { return $d } }

Write-Host '察元 AI 模型/服务 诊断' -ForegroundColor Green
Write-Host ('时间: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))

# --------- 0. 环境 / CHAYUAN_ROOT ---------
Sec '0. 环境 / CHAYUAN_ROOT'
KV 'env:CHAYUAN_ROOT'        (Def $env:CHAYUAN_ROOT '(未设)')
KV 'env:CHAYUAN_ROOT_IGNORE_STATE' (Def $env:CHAYUAN_ROOT_IGNORE_STATE '(未设)')
KV 'env:APPDATA'            $env:APPDATA

# state.json:Windows 在 %APPDATA%\chayuan\state.json
$stateFile = Join-Path $env:APPDATA 'chayuan\state.json'
KV 'state.json 路径'         $stateFile
$root = $null
if (Test-Path $stateFile) {
    Write-Host '  --- state.json 内容 ---' -ForegroundColor DarkGray
    Get-Content $stateFile -Raw | Write-Host
    try {
        $st = Get-Content $stateFile -Raw | ConvertFrom-Json
        if ($st.last_init_root) { $root = $st.last_init_root }
    } catch { Write-Host ('  [WARN] state.json 解析失败: ' + $_) -ForegroundColor Yellow }
} else {
    Write-Host '  state.json 不存在 — 用默认 %APPDATA%\chayuan' -ForegroundColor DarkYellow
}
if ($env:CHAYUAN_ROOT) { $root = $env:CHAYUAN_ROOT }       # env 覆盖
if (-not $root) { $root = Join-Path $env:APPDATA 'chayuan' } # 兜底默认
$root = [System.IO.Path]::GetFullPath($root)
KV '>>> 实际 CHAYUAN_ROOT'   $root
KV '   目录存在?'            (Test-Path $root)

# --------- 1. models 目录树 ---------
Sec '1. CHAYUAN_ROOT/models 目录树'
$modelsDir = Join-Path $root 'models'
KV 'models 目录'             $modelsDir
KV '存在?'                   (Test-Path $modelsDir)
if (Test-Path $modelsDir) {
    Write-Host '  --- 目录树(最深 5 层,目录 + 关键文件)---' -ForegroundColor DarkGray
    Get-ChildItem $modelsDir -Recurse -Depth 5 -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($modelsDir.Length).TrimStart('\')
        if ($_.PSIsContainer) {
            Write-Host ('  [D] ' + $rel)
        } else {
            $mb = [math]::Round($_.Length / 1MB, 2)
            Write-Host ('  [F] ' + $rel + '   (' + $mb + ' MB)')
        }
    }
    # 每个 bundled/<cap>/<model> 目录,判定是否"像一个模型仓库"
    Write-Host ''
    Write-Host '  --- 模型仓库判定(config.json / *.safetensors / *.gguf / *.onnx / *.bin)---' -ForegroundColor DarkGray
    $bundled = Join-Path $modelsDir 'bundled'
    if (Test-Path $bundled) {
        Get-ChildItem $bundled -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $cap = $_
            Get-ChildItem $cap.FullName -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                $m = $_
                $markers = @('config.json','tokenizer.json','model_index.json','manifest.json')
                $hasMarker = $false
                foreach ($mk in $markers) { if (Test-Path (Join-Path $m.FullName $mk)) { $hasMarker = $true } }
                $weights = Get-ChildItem $m.FullName -Recurse -Depth 3 -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Extension -in '.safetensors','.gguf','.onnx','.bin','.pt','.pth' }
                $verdict = if ($hasMarker -or $weights) { 'OK 像模型仓库' } else { 'BAD 没有标志文件/权重' }
                Write-Host ('  ' + $cap.Name + '/' + $m.Name)
                Write-Host ('      标志文件: ' + $hasMarker + '  权重文件数: ' + ($weights | Measure-Object).Count + '  => ' + $verdict)
                # 列出该目录下直接子项(看 ModelScope 是否多套了一层 owner/name)
                $direct = Get-ChildItem $m.FullName -ErrorAction SilentlyContinue | Select-Object -First 12 |
                    ForEach-Object { if ($_.PSIsContainer) { '[D]' + $_.Name } else { $_.Name } }
                Write-Host ('      直接子项: ' + ($direct -join ', '))
            }
        }
    } else {
        Write-Host '  bundled 子目录不存在' -ForegroundColor DarkYellow
    }
}

# --------- 2. local_models.json 扫描索引 ---------
Sec '2. local_models.json(扫描索引现状)'
$idxFile = Join-Path $root 'model_registry\local_models.json'
KV 'local_models.json'       $idxFile
if (Test-Path $idxFile) {
    Get-Content $idxFile -Raw | Write-Host
} else {
    Write-Host '  不存在 — 说明 scan_once 从没成功写过索引' -ForegroundColor DarkYellow
}

# --------- 3. vendor 目录 ---------
Sec '3. vendor 目录(install-vendor 装的东西)'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$vendor = Join-Path $repo 'chayuan-server\vendor'
KV 'vendor 路径'             $vendor
foreach ($sub in @('bundled_models','services','torch_wheels')) {
    $p = Join-Path $vendor $sub
    Write-Host ('  --- vendor\' + $sub + ' (存在: ' + (Test-Path $p) + ') ---') -ForegroundColor DarkGray
    if (Test-Path $p) {
        Get-ChildItem $p -Recurse -Depth 3 -ErrorAction SilentlyContinue | ForEach-Object {
            $rel = $_.FullName.Substring($p.Length).TrimStart('\')
            $tag = if ($_.PSIsContainer) { '[D]' } else { '[F]' }
            Write-Host ('    ' + $tag + ' ' + $rel)
        }
    }
}

# --------- 4. 服务二进制 ---------
Sec '4. llama-server / whisper-server 二进制'
# 可能落点:vendor/services/<engine>/<platform>/  或  CHAYUAN_ROOT/services/
$svcRoots = @(
    (Join-Path $vendor 'services'),
    (Join-Path $root 'services')
)
foreach ($sr in $svcRoots) {
    Write-Host ('  --- ' + $sr + ' (存在: ' + (Test-Path $sr) + ') ---') -ForegroundColor DarkGray
    if (Test-Path $sr) {
        Get-ChildItem $sr -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'llama-server|whisper-server' -or $_.Extension -eq '.exe' } |
            ForEach-Object {
                $rel = $_.FullName.Substring($sr.Length).TrimStart('\')
                Write-Host ('    [F] ' + $rel + '   (' + [math]::Round($_.Length/1MB,2) + ' MB)')
            }
    }
}

# --------- 5. 后端 API 实测 ---------
Sec '5. 后端 API(127.0.0.1:62581)'
function Api($method, $path, $body) {
    $url = 'http://127.0.0.1:62581' + $path
    try {
        if ($body) {
            $r = Invoke-RestMethod -Uri $url -Method $method -Body $body -ContentType 'application/json' -TimeoutSec 30
        } else {
            $r = Invoke-RestMethod -Uri $url -Method $method -TimeoutSec 30
        }
        return ($r | ConvertTo-Json -Depth 6)
    } catch {
        return ('[请求失败] ' + $_.Exception.Message)
    }
}
Write-Host '  --- GET /v1/models(已配置 + 本地扫到的模型)---' -ForegroundColor DarkGray
Api 'GET' '/v1/models' $null | Write-Host
Write-Host ''
Write-Host '  --- POST /admin/models/scan_and_mount(手动触发扫描+挂载)---' -ForegroundColor DarkGray
Api 'POST' '/admin/models/scan_and_mount' '{}' | Write-Host
Write-Host ''
Write-Host '  --- GET /runtime/models(本地磁盘模型索引内存视图)---' -ForegroundColor DarkGray
Api 'GET' '/runtime/models' $null | Write-Host

Write-Host ''
Write-Host '  --- GET /runtime/llama/registry(各 capability 运行时状态 + 错误)---' -ForegroundColor DarkGray
Api 'GET' '/runtime/llama/registry' $null | Write-Host
Write-Host ''
Write-Host '  --- GET /runtime/services(受管 sidecar 服务端点)---' -ForegroundColor DarkGray
Api 'GET' '/runtime/services' $null | Write-Host

Write-Host ''
Write-Host '======= 诊断完成 — 请把以上完整输出整段复制回贴 =======' -ForegroundColor Green

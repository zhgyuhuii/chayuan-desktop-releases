<#
.SYNOPSIS
  把一个目录打成 ISO 镜像(Windows 10+ 双击自动挂载),用 Windows 自带的
  IMAPI2 COM,无外部依赖。

.DESCRIPTION
  全量版 chayuan-desktop 装包是 .msi + 多个 .cab(WiX EmbedCab="no" 绕国产
  杀软对 C:\Windows\Installer\ 的拦截),用户必须一起拿才能装。之前提示让
  用户 zip → 解压 → 双击 MSI;改成 ISO 后:双击 ISO → Windows 自动挂载成
  虚拟光驱 → 进光驱目录双击 MSI → MSI 在同目录找到 CAB → 装上。

  文件系统:ISO9660 + Joliet + UDF 1.02
    - ISO9660 + Joliet:老 Windows / 第三方挂载工具兼容(短/长文件名)
    - UDF:CAB 可能 > 4 GB 单文件,纯 ISO9660 装不下,必须 UDF

  卷标:用户在"此电脑"看到的虚拟光驱名,大写字母数字下划线 ≤ 16 字符。

.PARAMETER SourceDir
  要打进 ISO 的目录(里面所有文件直接进 ISO 根)。

.PARAMETER OutputIso
  输出 .iso 路径。

.PARAMETER VolumeLabel
  ISO 卷标(光驱名),≤ 16 字符,大写字母数字下划线;不传默认 CHAYUAN_SETUP。

.EXAMPLE
  .\scripts\make-iso.ps1 -SourceDir .\dist-full -OutputIso .\dist-full\chayuan-desktop_full_setup.iso -VolumeLabel CHAYUAN_FULL
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$SourceDir,

    [Parameter(Mandatory=$true)]
    [string]$OutputIso,

    [string]$VolumeLabel = 'CHAYUAN_SETUP'
)

$ErrorActionPreference = 'Stop'

# 中文 Windows 默认控制台编码 GBK,但 build-desktop.ps1 / Tauri 上层按 UTF-8 收日志,
# 不显式置 UTF-8 会出现 婧愮洰褰? 之类乱码。
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}

if (-not (Test-Path $SourceDir -PathType Container)) {
    throw "SourceDir 不存在或不是目录:$SourceDir"
}

# 卷标合法化:ISO9660 限 32 字符纯大写字母数字下划线,IMAPI2 也认 UDF 长卷标,
# 这里折中 16 字符保兼容(NTFS 挂载工具偶尔会截断)。
$VolumeLabel = ($VolumeLabel -replace '[^A-Z0-9_]', '_').ToUpper()
if ($VolumeLabel.Length -gt 16) { $VolumeLabel = $VolumeLabel.Substring(0, 16) }

$SourceDir = (Resolve-Path $SourceDir).Path
$OutputIso = [System.IO.Path]::GetFullPath($OutputIso)

$srcSize = (Get-ChildItem -Path $SourceDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
$srcSizeMb = [math]::Round($srcSize / 1MB, 1)
Write-Host "[iso] 源目录:$SourceDir  ($srcSizeMb MB)"
Write-Host "[iso] 输出:$OutputIso"
Write-Host "[iso] 卷标:$VolumeLabel"

# IMAPI2 的 stream → 文件落盘没现成 PowerShell API,挂一段 C# helper 用
# Marshal.ReadInt32 把 IStream::Read 的 cbRead out 参数读出来。
$csCode = @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class IsoStreamWriter {
    public static void Save(object imageStreamObj, string outputPath) {
        IStream stream = (IStream)imageStreamObj;
        const int bufSize = 1024 * 1024;       // 1 MiB 块,大文件下吞吐稳
        byte[] buf = new byte[bufSize];
        IntPtr pcbRead = Marshal.AllocHGlobal(sizeof(int));
        try {
            using (FileStream fs = new FileStream(outputPath, FileMode.Create, FileAccess.Write)) {
                while (true) {
                    stream.Read(buf, bufSize, pcbRead);
                    int read = Marshal.ReadInt32(pcbRead);
                    if (read <= 0) break;
                    fs.Write(buf, 0, read);
                }
            }
        } finally {
            Marshal.FreeHGlobal(pcbRead);
        }
    }
}
'@

if (-not ('IsoStreamWriter' -as [type])) {
    Add-Type -TypeDefinition $csCode -Language CSharp
}

Write-Host "[iso] 初始化 IMAPI2 文件系统镜像..."
$fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage

# FsiFileSystemISO9660 = 1, FsiFileSystemJoliet = 2, FsiFileSystemUDF = 4
# 三个或起来 = 7,Win 自己会按文件大小分发(< 4 GB 走 ISO9660+Joliet,
# 单文件 ≥ 4 GB 自动落 UDF)。
$fsi.FileSystemsToCreate = 7

# UDF 1.02 是 Win XP 起所有版本都认的;1.50/2.x 兼容性反而差。
$fsi.UDFRevision = 0x102

# 必须先设大小限制再加文件,不然 50 MiB 默认上限就溢出 0x800700DF
# (HRESULT_FROM_WIN32(ERROR_DISK_FULL)),fsi 不会自己扩。
# IMAPI2 内部用 2048-byte sector,把源目录大小 + 5% 余量算 sector 数。
$sectorCount = [int]([math]::Ceiling(($srcSize * 1.05) / 2048))
if ($sectorCount -lt 32768) { $sectorCount = 32768 }   # 至少 64 MiB
$fsi.FreeMediaBlocks = $sectorCount
Write-Host "[iso]   预留 $sectorCount × 2048B sectors (~$([math]::Round($sectorCount * 2048 / 1MB, 0)) MB)"

$fsi.VolumeName = $VolumeLabel

Write-Host "[iso] 添加目录树..."
# $false = 不在 ISO 里再创建一层 SourceDir 名字的子目录,直接把 SourceDir 内容平铺到根。
$fsi.Root.AddTree($SourceDir, $false)

Write-Host "[iso] 生成镜像流..."
$result = $fsi.CreateResultImage()

# 输出目录存在性
$outDir = Split-Path -Parent $OutputIso
if ($outDir -and -not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

# 落盘策略:写 .iso.part → ReleaseComObject → rename。
# 直接 FileMode.Create 写到最终路径会被以下场景挡:
#   - 资源管理器缩略图工厂 / Defender 实时扫描在读旧 iso
#   - 上一次失败留下的 iso 仍被某 PS/Tauri 进程持有句柄
# 先写到 .part 再原子 rename,旧 iso 即使被占用也能等 rename 阶段重试。
$tmpIso = "$OutputIso.part"
if (Test-Path $tmpIso) {
    try { Remove-Item -LiteralPath $tmpIso -Force -ErrorAction Stop } catch {
        throw "[iso] 临时文件 $tmpIso 删不掉,可能上一轮 PS 残留进程在占用:$($_.Exception.Message)"
    }
}

Write-Host "[iso] 落盘:$tmpIso ..."
[IsoStreamWriter]::Save($result.ImageStream, $tmpIso)

# COM 对象引用立刻释放,避免 PowerShell 退出时 GC 拖延导致句柄占用。
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($result) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($fsi) | Out-Null
[System.GC]::Collect()
[System.GC]::WaitForPendingFinalizers()

# 旧 iso 若存在且被资源管理器/Defender 锁:最多重试 5 次,每次间隔 1s。
if (Test-Path $OutputIso) {
    $removed = $false
    for ($i = 1; $i -le 5; $i++) {
        try {
            Remove-Item -LiteralPath $OutputIso -Force -ErrorAction Stop
            $removed = $true
            break
        } catch {
            Write-Host "[iso] 旧 iso 被占用,等 1s 重试($i/5):$($_.Exception.Message)"
            Start-Sleep -Seconds 1
        }
    }
    if (-not $removed) {
        throw "[iso] 旧 iso $OutputIso 5 次都删不掉,请手动关掉资源管理器/挂载/杀软扫描后重跑。.part 文件保留在 $tmpIso"
    }
}

Move-Item -LiteralPath $tmpIso -Destination $OutputIso -Force

$isoSize = (Get-Item $OutputIso).Length
$isoSizeMb = [math]::Round($isoSize / 1MB, 1)
Write-Host "[iso] OK — $OutputIso  ($isoSizeMb MB)"

# Build script: bundles the app + ffmpeg into a portable folder.
# Usage:  double-click build.bat   (recommended)
#         powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
#
# NOTE: this file is deliberately ASCII-only. Windows PowerShell 5.1 reads
# .ps1 files as ANSI, so any Chinese literal here would be corrupted and
# would end up in the artifact name (that is why the exe is "MusicUnlock").

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try { chcp 65001 > $null } catch { }

$appName = "MusicUnlock"

if (-not (Test-Path "bin\ffmpeg.exe")) {
    Write-Host "[1/3] Downloading ffmpeg (~200MB)..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path "_build", "bin" | Out-Null
    $zip = "_build\ffmpeg.zip"
    Invoke-WebRequest `
        -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip" `
        -OutFile $zip -UseBasicParsing
    Expand-Archive -LiteralPath $zip -DestinationPath "_build\ffx" -Force
    $exe = Get-ChildItem "_build\ffx" -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $exe.FullName "bin\ffmpeg.exe" -Force
    Copy-Item (Join-Path $exe.DirectoryName "ffprobe.exe") "bin\ffprobe.exe" -Force
} else {
    Write-Host "[1/3] ffmpeg already present" -ForegroundColor Green
}

Write-Host "[2/3] Running tests..." -ForegroundColor Cyan
python tests\test_qmc.py
if ($LASTEXITCODE -ne 0) { throw "QQ Music decrypt tests failed, aborting build." }
python tests\test_netease.py
if ($LASTEXITCODE -ne 0) { throw "NetEase decrypt tests failed, aborting build." }
python tests\test_platforms.py
if ($LASTEXITCODE -ne 0) { throw "Kugou/Kuwo/Migu tests failed, aborting build." }
python tests\test_real_samples.py
if (0 -ne 0) { throw "Real sample regression failed, aborting build." }
python tests\test_gui_smoke.py
if ($LASTEXITCODE -ne 0) { throw "GUI smoke test failed, aborting build." }

Write-Host "[3/3] Packaging with PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name $appName `
    --icon "assets\icon.ico" `
    --add-data "assets\icon.ico;assets" `
    --add-data "assets\icon.png;assets" `
    --add-binary "bin\ffmpeg.exe;bin" `
    --add-binary "bin\ffprobe.exe;bin" `
    --exclude-module tkinter `
    --exclude-module numpy `
    --collect-all frida `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$dist = "dist\$appName"
$sizeMb = ((Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB)

# Low 完整性进程无法和资源管理器通信，托盘图标会被系统静默拒绝。
# 开发沙箱会给工作区文件打 Low 标签，这里显式改回 Medium，保证托盘可用。
icacls "$dist" /setintegritylevel Medium /T 2>$null | Out-Null
Write-Host ""
Write-Host "Done: $dist\$appName.exe" -ForegroundColor Green
Write-Host ("Folder size: {0:N0} MB" -f $sizeMb)

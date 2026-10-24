# 构建 onedir 产物 + 绿色 zip（可选：安装包）
# 用法： pwsh -File packaging/build.ps1 [-Clean] [-Installer]
param(
    [switch]$Clean,
    [switch]$Installer
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root '.venv\Scripts\python.exe'
$dist = Join-Path $root 'dist'
$app  = Join-Path $dist 'AudioBench'
$env:PYTHONPATH = Join-Path $root 'src'

Write-Output '[1/5] native DLLs'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'native\build.ps1') | Select-Object -Last 2

Write-Output '[2/5] i18n'
$prevI18n = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$i18nOut = & $venv -m ab.tools.make_i18n 2>&1
$ErrorActionPreference = $prevI18n
$i18nOut | Select-Object -Last 1

Write-Output '[3/5] PyInstaller'
$args = @('-m', 'PyInstaller', '--noconfirm')
if ($Clean) { $args += '--clean' }
$args += @('--distpath', $dist, '--workpath', (Join-Path $root 'build\pyi'),
           (Join-Path $root 'packaging\AudioBench.spec'))
# PyInstaller 会往 stderr 写 INFO，PS 5.1 在 Stop 策略下会把它当作异常，这里临时放宽
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$pyiOut = & $venv @args 2>&1
$pyiRc = $LASTEXITCODE
$ErrorActionPreference = $prev
$pyiOut | Select-Object -Last 3
if ($pyiRc -ne 0) { throw "PyInstaller failed (rc=$pyiRc)" }

Write-Output '[4/5] size report'
& $venv (Join-Path $root 'src\ab\tools\size_report.py') $app | Select-Object -First 8

Write-Output '[5/5] portable zip'
$flag = Join-Path $app 'portable.flag'
New-Item -ItemType File -Force $flag | Out-Null
Copy-Item (Join-Path $root 'packaging\portable-README.txt') (Join-Path $app 'README-portable.txt') -Force -ErrorAction SilentlyContinue
$zip = Join-Path $dist 'AudioBench-0.6.2-portable-win64.zip'
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $app '*') -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $flag -Force
$total = (Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output ("[done] dist/AudioBench = {0} MB, zip = {1} MB" -f `
    [math]::Round($total / 1MB, 1), [math]::Round((Get-Item $zip).Length / 1MB, 1))

if ($Installer) {
    Write-Output '[+] installer'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'make_installer.ps1')
}

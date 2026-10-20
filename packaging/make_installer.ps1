# 构建安装包（Inno Setup 6）
# 前置：dist\AudioBench 已由 packaging\build.ps1 生成
param(
    [string]$Root = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = 'Stop'
if (-not $Root) {
    if ($PSScriptRoot) { $Root = Split-Path $PSScriptRoot -Parent }
    else { $Root = (Get-Location).Path }
}
$Root = (Resolve-Path $Root).Path
$pkg = Join-Path $Root 'packaging'

if (-not $Iscc) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "ISCC.exe not found (install Inno Setup 6)" }

$app = Join-Path $Root 'dist\AudioBench'
if (-not (Test-Path (Join-Path $app 'AudioBench.exe'))) {
    throw "dist\AudioBench not built yet; run packaging\build.ps1 first"
}
Write-Output ("[0/2] root={0}  app={1:N1} MB" -f $Root, ((Get-ChildItem $app -Recurse -File |
    Measure-Object Length -Sum).Sum / 1MB))

# ffmpeg 的许可证文本随包
$lic = Join-Path $pkg 'licenses'
New-Item -ItemType Directory -Force $lic | Out-Null
$ff = Join-Path $Root 'tools\ffmpeg'
foreach ($f in @('LICENSE.txt', 'README.txt')) {
    $p = Join-Path $ff $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $lic "ffmpeg-$f") -Force }
}

Write-Output "[1/2] ISCC: $Iscc"
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$out = & $Iscc (Join-Path $pkg 'installer.iss') 2>&1
$rc = $LASTEXITCODE
$ErrorActionPreference = $prev
$out | Select-Object -Last 6
if ($rc -ne 0) { throw "ISCC failed (rc=$rc)" }

$setup = Get-ChildItem (Join-Path $Root 'dist') -Filter 'AudioBench-*-setup-x64.exe' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "installer not produced" }
Write-Output ("[2/2] {0}  {1} MB" -f $setup.Name, [math]::Round($setup.Length / 1MB, 1))

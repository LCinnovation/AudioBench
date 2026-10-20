# 构建 native 目录下的 DLL（MSVC x64，静态 CRT）
# 用法： pwsh -File native/build.ps1          -> 产出 native/build/ab_audio.dll
#        pwsh -File native/build.ps1 -Clean
#
# 中间产物（.obj/.lib/.exp）都落在 native/build 里，不污染仓库根目录。

param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$out  = Join-Path $here 'build'
$vcvars = 'C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat'

if ($Clean -and (Test-Path $out)) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force $out | Out-Null

if (-not (Test-Path $vcvars)) {
    throw "MSVC vcvars64.bat not found at $vcvars (install VS Build Tools + VCTools workload)"
}

$src = Join-Path $here 'audio\ab_audio.c'
$def = Join-Path $here 'audio\ab_audio.def'
$dll = Join-Path $out 'ab_audio.dll'
$cmd = @"
call "$vcvars" >nul && cd /d "$out" && cl /nologo /O2 /W3 /MT /LD /utf-8 /D_CRT_SECURE_NO_WARNINGS "$src" /Fe"$dll" /link /INCREMENTAL:NO /DEF:"$def" ole32.lib oleaut32.lib uuid.lib winmm.lib user32.lib advapi32.lib
"@
Write-Output "[build] ab_audio.c -> $dll"
cmd /c $cmd
if (-not (Test-Path $dll)) { throw "build failed: $dll not produced" }
$size = [math]::Round((Get-Item $dll).Length / 1KB, 1)
Write-Output "[ok] $dll ($size KB)"

# --- 变速变调：Signalsmith Stretch（header-only C++）包成 C ABI
$ssrc = Join-Path $here 'stretch\ab_stretch.cpp'
$sdef = Join-Path $here 'stretch\ab_stretch.def'
$sdll = Join-Path $out 'ab_stretch.dll'
if (Test-Path $ssrc) {
    $sinc1 = Join-Path $here 'stretch'
    $sinc2 = Join-Path $here 'stretch\include'
    $scmd = @"
call "$vcvars" >nul && cd /d "$out" && cl /nologo /O2 /W3 /MT /LD /EHsc /std:c++14 /utf-8 /I"$sinc1" /I"$sinc2" "$ssrc" /Fe"$sdll" /link /INCREMENTAL:NO /DEF:"$sdef" ole32.lib
"@
    Write-Output "[build] ab_stretch.cpp -> $sdll"
    cmd /c $scmd
    if (Test-Path $sdll) {
        $ssize = [math]::Round((Get-Item $sdll).Length / 1KB, 1)
        Write-Output "[ok] $sdll ($ssize KB)"
    } else {
        Write-Output "[warn] ab_stretch.dll not produced (stretch falls back to tape mode)"
    }
}

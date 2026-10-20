# 获取运行所需的大体积依赖（模型 + FFmpeg）
#
# 这些文件不进 git 仓库（单个 100MB+ 会被 GitHub 拒绝，且不便版本管理）。
# 下载后放到约定位置，程序即可直接使用。
#
# 用法：
#   pwsh -File scripts\fetch-deps.ps1                 # 全部
#   pwsh -File scripts\fetch-deps.ps1 -SkipModels     # 只取 ffmpeg
#   pwsh -File scripts\fetch-deps.ps1 -SkipFfmpeg     # 只取模型

param(
    [switch]$SkipModels,
    [switch]$SkipFfmpeg
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$ProgressPreference = 'SilentlyContinue'

function Get-File([string]$url, [string]$dest, [string]$mirror = '') {
    if (Test-Path $dest) {
        Write-Output "  [skip] $([IO.Path]::GetFileName($dest)) 已存在"
        return
    }
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    $urls = @($url)
    if ($mirror) { $urls = @($mirror, $url) }
    foreach ($u in $urls) {
        try {
            Write-Output "  下载 $u"
            & curl.exe -L --fail --retry 2 --retry-delay 3 -C - -o $dest $u
            if ((Test-Path $dest) -and (Get-Item $dest).Length -gt 10MB) {
                Write-Output ("  [ok] {0}  {1:N1} MB" -f (Split-Path $dest -Leaf), ((Get-Item $dest).Length / 1MB))
                return
            }
        } catch {
            Write-Output "  失败：$($_.Exception.Message)"
        }
    }
    throw "下载失败：$url"
}

if (-not $SkipModels) {
    Write-Output '[1/2] 分离模型（放到 assets\models\）'
    # 原始 fp16 权重（构建期需要自行预优化，见 docs/models.md）
    Get-File 'https://huggingface.co/adowu/htdemucs-onnx/resolve/main/htdemucs_fp16weights.onnx' `
             (Join-Path $root 'build-env\models\htdemucs_4s_fp16.onnx') `
             'https://hf-mirror.com/adowu/htdemucs-onnx/resolve/main/htdemucs_fp16weights.onnx'
    Get-File 'https://huggingface.co/alekshandru/htdemucs-6s-onnx/resolve/main/htdemucs_6s_fp16weights.onnx' `
             (Join-Path $root 'build-env\models\htdemucs_6s_fp16weights.onnx') `
             'https://hf-mirror.com/alekshandru/htdemucs-6s-onnx/resolve/main/htdemucs_6s_fp16weights.onnx'
    Write-Output '  预优化（生成 assets\models\*.opt.onnx）：'
    & "$root\.venv\Scripts\python.exe" "$root\build-env\t_sep_4s.py" 2>&1 | Select-Object -Last 3
    & "$root\.venv\Scripts\python.exe" "$root\build-env\m3_model.py" 2>&1 | Select-Object -Last 3
}

if (-not $SkipFfmpeg) {
    Write-Output '[2/2] FFmpeg（放到 tools\ffmpeg\）'
    $zip = Join-Path $root 'build-env\ffmpeg-lgpl.zip'
    Get-File 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip' $zip
    $tmp = Join-Path $root 'build-env\ffmpeg_unzip'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
    Remove-Item (Join-Path $root 'tools\ffmpeg') -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Join-Path $root 'tools\ffmpeg') | Out-Null
    Copy-Item (Join-Path $src.FullName '*') (Join-Path $root 'tools\ffmpeg') -Recurse -Force
    Write-Output ("  [ok] tools\ffmpeg  {0:N1} MB" -f ((Get-ChildItem (Join-Path $root 'tools\ffmpeg') -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
}

Write-Output ''
Write-Output '完成。自检：'
Write-Output '  .venv\Scripts\python.exe -m ab --selftest --out selftest.json'

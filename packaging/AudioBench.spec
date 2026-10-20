# -*- mode: python ; coding: utf-8 -*-
"""AudioBench onedir 打包（§5：关 UPX、onedir、剔掉用不到的 Qt 模块）。

构建： pwsh -File packaging/build.ps1
产物： dist/AudioBench/AudioBench.exe（+ _internal/）
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent          # noqa: F821  (SPECPATH 由 PyInstaller 注入)
NATIVE = ROOT / "native" / "build" / "ab_audio.dll"
ASSETS = ROOT / "assets"
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin"

datas = []
for sub in ("i18n", "fonts", "icons", "models"):
    p = ASSETS / sub
    if p.exists():
        datas.append((str(p), f"assets/{sub}"))

# ffmpeg 用「构建后拷贝」而不是 datas：PyInstaller 会把放进 datas 的 exe 当依赖扫描，
# 结果每个 DLL 在 _internal 根下再存一份（实测多出 160MB）。见文件末尾的 copytree。

binaries = []
for dll in ("ab_audio.dll", "ab_stretch.dll"):
    p = ROOT / "native" / "build" / dll
    if p.exists():
        binaries.append((str(p), "native"))

# 用不到就剔掉：省 ~60MB，也避免把 QML/WebEngine 拖进 Wine
excludes = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtUiTools",
    "PySide6.QtSql", "PySide6.QtDBus", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtHttpServer", "PySide6.QtConcurrent",
    "tkinter", "unittest", "pydoc", "doctest", "difflib", "email", "http",
    "xmlrpc", "pdb", "lib2to3", "distutils", "setuptools", "pip",
    # 注意：onnxruntime 是分离功能的运行时，**不能**排除（M3 曾误排除，冻结版分离直接 ModuleNotFoundError）
    "torch", "scipy", "pandas", "matplotlib", "PIL",
]

# onnxruntime 是延迟导入（只有 --sep-worker 才 import 它），静态分析看不到，
# 所以必须显式收集：包 + 它的动态库（onnxruntime/capi/*.dll）
from PyInstaller.utils.hooks import collect_all  # noqa: E402

_ort_datas, _ort_binaries, _ort_hidden = collect_all("onnxruntime")
datas += _ort_datas
binaries += _ort_binaries
hiddenimports = list(_ort_hidden)

a = Analysis(                                   # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["numpy", "soxr", "Crypto.Cipher.AES"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)                # noqa: F821

# 纯 Qt 的光栅渲染不需要 OpenGL 软实现/D3D 编译器，剔掉省 20MB+
_DROP = {"opengl32sw.dll", "d3dcompiler_47.dll", "qt6pdf.dll", "qt6quick.dll",
         "qt6qml.dll", "qt6webenginecore.dll"}
a.binaries = [b for b in a.binaries if Path(b[0]).name.lower() not in _DROP]
a.datas = [d for d in a.datas if Path(d[0]).name.lower() not in _DROP]

exe = EXE(                                      # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioBench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                  # §9 R6：关 UPX，少招杀软
    console=False,
    disable_windowed_traceback=False,
    icon=None,
    version=str(ROOT / "packaging" / "version.rc") if (ROOT / "packaging" / "version.rc").exists() else None,
)

coll = COLLECT(                                 # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AudioBench",
)

# ---- 构建后置：只拷 ffmpeg/ffprobe 与它们自己的 DLL（不带 ffplay），且不做依赖扫描
import shutil                                              # noqa: E402

_dist = Path(DISTPATH) / "AudioBench" / "_internal" / "tools" / "ffmpeg" / "bin"   # noqa: F821
if FFMPEG.exists():
    _dist.mkdir(parents=True, exist_ok=True)
    for f in FFMPEG.iterdir():
        if f.name.lower() == "ffplay.exe":
            continue
        shutil.copy2(f, _dist / f.name)
    print(f"[spec] ffmpeg -> {_dist} ({sum(g.stat().st_size for g in _dist.iterdir()) / 1048576:.0f} MB)")

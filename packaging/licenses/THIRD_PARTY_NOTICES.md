# AudioBench · 第三方组件与许可证

AudioBench 本体闭源分发。随包附带以下第三方组件，均为**允许闭源分发**的许可证；
LGPL 组件以「独立的、可替换的文件」形式发布，未做静态链接修改。

---

## 运行时组件

| 组件 | 版本 | 许可证 | 形态 | 合规动作 |
|---|---|---|---|---|
| CPython | 3.11.9 | PSF-2.0 | `python311.dll` + stdlib | 保留版权声明 |
| PySide6-Essentials / shiboken6 | 6.8.3 | LGPL-3.0 | 独立 Qt6*.dll + .pyd | DLL 可被用户替换；附 Qt 源码获取方式（本文件 §源码） |
| Qt 6.8.3 | 6.8.3 | LGPL-3.0 | 同上 | 同上 |
| NumPy | 2.4.x | BSD-3-Clause | `numpy/*.pyd`、`numpy.libs/*` | 保留版权声明 |
| onnxruntime | 1.31.0 | MIT | `onnxruntime/*` | 保留版权声明 |
| soxr | 1.1.0 | LGPL-2.1 | `soxr/*.pyd` | 可替换 .pyd；源码见 §源码 |
| pycryptodome | 4.1.0 | BSD-2-Clause / Public Domain | `Crypto/*` | 保留版权声明 |
| FFmpeg | n8.1.3 (LGPL shared) | LGPL-3.0（`--enable-version3`，未启用 `--enable-gpl`） | **独立程序** `tools/ffmpeg/bin/*.exe` + 独立 DLL | 附 `ffmpeg-LICENSE.txt` 与源码获取方式；不与主程序链接 |
| miniaudio | 0.11.x | Public Domain / MIT-0 | 编译进 `ab_audio.dll` | 无需额外义务 |
| Signalsmith Stretch | 1.0.0 | MIT | 编译进 `ab_stretch.dll` | 保留版权声明 |
| Signalsmith Linear | main | MIT | 同上（仅头文件） | 同上 |
| htdemucs_6s ONNX | opset17 导出 | 模型卡 MIT（源权重训练集 MUSDB18-HQ 为非商用） | `assets/models/htdemucs_6s.opt.onnx` | **仅限个人学习用途**；关于页与导出提示已声明；不内置任何音源 |
| Noto Sans SC | 2024 | SIL OFL-1.1 | 子集改名为 `AB Sans SC` | 改名满足 OFL 的保留字体名条款；附 OFL 全文 |

> 模型说明：htdemucs_6s 权重来自 Demucs 项目（MIT），其训练数据 MUSDB18-HQ 的许可限制为
> 学术/非商用。因此本软件把「分离」定位为个人学习用途，不用于商业分发分离结果。

---

## 禁止引入的组件（红线）

- **Rubber Band**（GPL-2.0）—— 变速用 Signalsmith 替代。
- 任何 `--enable-gpl` 的 FFmpeg 构建（含 x264/x265 等）。
- Ardour / Audacity / fre:ac / Videomass 的代码或资源（GPL，仅只读借鉴交互）。

---

## 源码获取

- Qt/PySide6：<https://download.qt.io/official_releases/QtForPython/>（版本 6.8.3）
- soxr：<https://sourceforge.net/projects/soxr/>（1.1.0 或更新）
- FFmpeg：<https://github.com/BtbN/FFmpeg-Builds>（`ffmpeg-n8.1-latest-win64-lgpl-shared`）
  或自行用 `--disable-everything --enable-libmp3lame --enable-shared --disable-gpl` 重建。

如需要，我们可在收到书面请求后提供上述源码的对应快照（三年内有效）。

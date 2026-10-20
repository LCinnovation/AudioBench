# 第三方组件与致谢

AudioBench 本体（`src/`、`native/`、`packaging/`、`tests/`）由本项目编写，以 MIT 许可发布。
运行时用到了下面这些优秀的开源项目，**感谢它们的作者和贡献者**。

> 说明：随安装包/绿色版分发的第三方二进制，其许可证均允许再分发；
> LGPL 组件以独立文件（DLL / 独立进程）形式提供，可被用户替换。

---

## 1. 音频分离模型

### Demucs v4（htdemucs / htdemucs_6s）

- **作者**：Alexandre Défossez 及 Meta AI Research（原 Facebook AI）团队
- **项目**：<https://github.com/facebookresearch/demucs>
- **许可证**：MIT
- **用在**：`assets/models/htdemucs_4s.opt.onnx`（四轨）、`htdemucs_6s.opt.onnx`（六轨）
- **说明**：本项目使用的是社区导出的 ONNX 版本，推理完全在 onnxruntime 里完成，不含 PyTorch：
  - 四轨导出：`adowu/htdemucs-onnx`（Hugging Face，MIT）
  - 六轨导出：`alekshandru/htdemucs-6s-onnx`（Hugging Face，MIT）
- **注意**：Demucs 权重的训练集 MUSDB18-HQ 为非商用许可，因此本项目的分离功能定位为**个人学习用途**。

### ONNX Runtime

- **作者**：Microsoft
- **项目**：<https://github.com/microsoft/onnxruntime>
- **许可证**：MIT

---

## 2. 实时音频与算法

### miniaudio

- **作者**：David Reid（mackron）
- **项目**：<https://github.com/mackron/miniaudio>
- **许可证**：Public Domain（CC0）或 MIT-0，二选一
- **用在**：`native/audio/ab_audio.c` —— WASAPI（共享模式）设备封装、环形缓冲、EQ 内核

### Signalsmith Stretch / Signalsmith Linear

- **作者**：Signalsmith Audio（Geraint Luff）
- **项目**：<https://github.com/Signalsmith-Audio/signalsmith-stretch>
- **许可证**：MIT
- **用在**：`native/stretch/` —— 变速不变调 / 变调不变速（50–200%、±12 半音）

### soxr

- **作者**：Rob Sykes 及 soxr 贡献者
- **项目**：<https://sourceforge.net/projects/soxr/>
- **许可证**：LGPL-2.1
- **用在**：解码后的采样率转换（Python 绑定 `python-soxr`）

---

## 3. 编解码

### FFmpeg

- **作者**：FFmpeg 项目及全部贡献者（Fabrice Bellard 等）
- **项目**：<https://ffmpeg.org/>
- **许可证**：本构建为 **LGPL-3.0 共享版**（未启用 `--enable-gpl`），作为独立可执行程序调用
- **用在**：解码、编码（mp3 via libmp3lame）、转码、探测 —— 全部通过子进程，不做链接

---

## 4. 应用框架与基础库

### Qt for Python（PySide6）与 Qt 6

- **作者**：The Qt Company
- **项目**：<https://doc.qt.io/qtforpython/>
- **许可证**：LGPL-3.0（以独立 Qt6*.dll 形式分发，可替换）
- **用在**：全部界面

### NumPy

- **作者**：NumPy 开发者社区
- **项目**：<https://numpy.org/>
- **许可证**：BSD-3-Clause

### PyCryptodome

- **作者**：Legrandin 及贡献者
- **项目**：<https://www.pycryptodome.org/>
- **许可证**：BSD-2-Clause / Public Domain
- **用在**：NCM 容器解密

### Noto Sans SC

- **作者**：Google（Adobe 参与设计）
- **项目**：<https://fonts.google.com/noto>
- **许可证**：SIL OFL-1.1
- **用在**：界面中文字体，子集化后改名为 `AB Sans SC`（OFL 允许改名以满足保留字体名条款）

### Python

- **作者**：Python Software Foundation
- **许可证**：PSF-2.0

---

## 5. 打包与安装

### PyInstaller

- **作者**：PyInstaller 开发组
- **项目**：<https://pyinstaller.org/>
- **许可证**：GPL-2.0 with bootloader exception（打包产物可闭源分发）

### Inno Setup

- **作者**：Jordan Russell、Martijn Laan
- **项目**：<https://jrsoftware.org/isinfo.php>
- **许可证**：自由使用（安装器本身）
- **用在**：`packaging/installer.iss`

---

## 6. 交互与界面的参考对象（未使用其代码）

- **Logic Pro X**（Apple）—— 轨道头、走带条、混音器的**布局**参考
- **Ardour**、**Audacity**、**Ocenaudio** —— 交互习惯的参考（均为 GPL，本项目**没有**复制其代码）
- **UVR / Demucs GUI 类工具** —— 分离功能的使用流程参考

---

## 7. 关于"代码来源"的声明

本项目在实现过程中使用了 AI 辅助编程（见 README 顶部声明）。
所有第三方代码均以**依赖**或**可辨识的源码副本**形式引入（如 `miniaudio.h`、`signalsmith-*` 头文件），
并在文件头部保留其原始版权与许可证声明；项目自身的代码不包含从上述 GPL 项目复制的片段。

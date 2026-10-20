# 分离模型（不随仓库分发）

这两个 ONNX 文件各 ~300MB，超过 GitHub 的单文件限制，因此**不进仓库**。
首次使用前执行：

```powershell
pwsh -File scripts\fetch-deps.ps1 -SkipFfmpeg
```

脚本会下载 fp16 权重并做构建期预优化，最终得到：

```
assets/models/htdemucs_4s.opt.onnx    329 MB   4 轨（默认）drums/bass/other/vocals
assets/models/htdemucs_6s.opt.onnx    272 MB   6 轨 + guitar/piano
```

## 也可以手动准备

```powershell
# 4 轨（Demucs htdemucs，MIT）
curl -L -o build-env\models\htdemucs_4s_fp16.onnx `
  https://huggingface.co/adowu/htdemucs-onnx/resolve/main/htdemucs_fp16weights.onnx
# 国内镜像：https://hf-mirror.com/adowu/htdemucs-onnx/resolve/main/htdemucs_fp16weights.onnx

# 6 轨（htdemucs_6s，MIT）
curl -L -o build-env\models\htdemucs_6s_fp16weights.onnx `
  https://huggingface.co/alekshandru/htdemucs-6s-onnx/resolve/main/htdemucs_6s_fp16weights.onnx

# 预优化（把 ORT 的图优化挪到构建期，避免运行时 4GB 峰值）
.venv\Scripts\python.exe build-env\t_sep_4s.py      # 生成 htdemucs_4s.opt.onnx
.venv\Scripts\python.exe build-env\m3_model.py      # 生成 htdemucs_6s.opt.onnx
```

更多说明见 [`docs/models.md`](../../docs/models.md)。

## 许可

权重为 Demucs（MIT），但其训练集 MUSDB18-HQ 为非商用许可 —— 分离功能请仅用于个人学习，
不要把结果用于商业发行。商用请替换为自有许可模型（`src/ab/sep/models.py` 里加一条即可）。

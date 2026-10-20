# 分离模型（Demucs v4 · MIT）

AudioBench 内置**两套** Demucs v4 模型，都是构建期预优化过的 ONNX：

| 模型 id | 文件 | 体积 | 输出 | 默认 |
|---|---|---|---|---|
| `4s` | `assets/models/htdemucs_4s.opt.onnx` | 329 MB | drums / bass / other / vocals | ✅ |
| `6s` | `assets/models/htdemucs_6s.opt.onnx` | 272 MB | 上面 4 个 + guitar / piano | |

两者都是固定 shape，段长 **7.8 s @44.1 kHz**：

```
输入 mix   : float32 [1, 2, 343980]
输出 stems : float32 [1, N, 2, 343980]     N=4 或 6
```

---

## 为什么默认用 4 轨

同一首真实歌曲（3:08）全曲实测：

| 指标 | htdemucs_6s | **htdemucs（4 轨）** |
|---|---|---|
| 四/六轨相加 vs 原曲的残差 | **17.2%** | **2.20%** |
| 与原曲的相关 | 0.9889 | **0.99982** |
| `other` 轨 RMS | 0.024（内容被丢） | **0.090**（找回来了） |
| RTF | 0.328 | 0.327 |
| 峰值工作集 | 842 MB | 768 MB |

6 轨变体是 Demucs 家族里最弱的一支（多分两路，代价是整体重建变差）；4 轨模型基本做到
「四轨相加 ≈ 原曲」（残差 −33 dB），听感和可用性都更好。**需要吉他/钢琴时再切 6 轨。**

---

## 为什么是「预优化」的

直接用原始导出加载时，ORT 的图优化阶段会产生约 **4.3GB 的瞬时峰值**，而 `enable_cpu_mem_arena=True`
还会让提交内存到 6.2GB。预优化把这一步挪到构建期：

```python
so = ort.SessionOptions()
so.enable_cpu_mem_arena = False
so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
so.optimized_model_filepath = "assets/models/htdemucs_4s.opt.onnx"
ort.InferenceSession("htdemucs_fp16weights.onnx", sess_options=so)
```

运行期只用 `ORT_ENABLE_BASIC` + `enable_cpu_mem_arena=False`：加载 **0.42 s**。

---

## 重新生成

```powershell
.venv\Scripts\pip install onnxruntime

# 4 轨（默认）
#   https://huggingface.co/adowu/htdemucs-onnx/resolve/main/htdemucs_fp16weights.onnx
#   国内可用 https://hf-mirror.com/adowu/htdemucs-onnx/resolve/main/htdemucs_fp16weights.onnx
.venv\Scripts\python build-env\t_sep_4s.py      # 预优化 + 质量量测（会自动生成 opt 文件）

# 6 轨（含吉他/钢琴）
.venv\Scripts\python build-env\m3_model.py
```

模型来源：
- 4 轨：`adowu/htdemucs-onnx`（Hugging Face，MIT），原始权重来自 Demucs（`facebookresearch/demucs`，MIT）
- 6 轨：`alekshandru/htdemucs-6s-onnx`（Hugging Face，MIT）

---

## 关于"用 GitHub 上的开源实现改进"

本轮试过并**否决**的几种做法（都有实测数据，见 `AudioBench-分离改进说明-v1.md`）：

| 做法 | 来源 | 结果 |
|---|---|---|
| 输入标准化（`(mix-mean)/std`） | Demucs `apply_model` | **无效**：ONNX 图里已经包含，输出逐位相同 |
| 三角窗 + 50% 重叠 | Demucs `apply.py` 的默认窗 | 残差 18.5% → 18.1%，收益 0.4 个点，成本翻倍 |
| test-time shift 平均 | Demucs `-shifts` | **更差**（29.9%），简单循环移位与重叠相加冲突 |
| 换 4 轨模型 | htdemucs | **残差 18.5% → 1.8%**（40s 片段）/ 17.2% → 2.2%（全曲） |

结论：真正有效的是换模型，而不是调重叠参数。

---

## 许可边界

- 两套模型均为 MIT（Demucs 权重），训练集 MUSDB18-HQ 为非商用许可 —— 因此本软件把分离定位为**个人学习用途**；
- 不内置任何音源，不提供下载；
- 若要商用，替换为自有许可的模型即可：`sep/models.py` 里 `MODELS` 加一条，`file` 指向新 onnx。

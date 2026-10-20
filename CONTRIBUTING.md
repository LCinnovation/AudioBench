# 参与贡献

欢迎任何形式的贡献：报 Bug、提建议、改文档、提 PR。这个项目**由 AI 辅助完成**，
所以对"代码风格不统一""注释像机器写的"这类问题也请直接指出。

## 提交 PR 前

1. 跑通测试：

   ```powershell
   .venv\Scripts\python.exe -m pytest
   .venv\Scripts\python.exe -m ab.tools.make_i18n --check   # 新增文案必须补中文
   ```

2. 如果改动涉及**打包**（`packaging/`、`assets/`、延迟导入的库），
   请同时验证打包产物，而不只是源码：

   ```powershell
   pwsh -File packaging\build.ps1 -Clean
   dist\AudioBench\AudioBench.exe --selftest --out s.json
   ```

   本项目真实踩过三次"源码绿、打包炸"：分离运行时漏打包、NCM 对话框缺方法、
   冻结版 stdout 用 GBK 把中文路径变 `????`。

3. 新增界面文案请用 `tr("上下文", "英文原句")` 形式，并在
   `src/ab/tools/make_i18n.py` 的 `ZH` 表里补上中文翻译。

## 代码约定（简要）

- 音频线程里不允许进 Python；设备回调在 `native/audio/ab_audio.c`；
- 所有编辑操作走 `state.edit("动作名")`，以便进入撤销栈；
- 文件读写用 UTF-8 显式编码，路径类数据不要跨进程传递（用父进程自己算的路径）；
- 面向用户的失败要给"人话"，不要抛裸异常给界面。

## 提交 Issue 前

请先看 README 的「已知限制」，并附上 `--selftest` 的 JSON 与日志尾部。

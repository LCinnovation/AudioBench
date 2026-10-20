AudioBench 绿色版（便携模式）使用说明
=====================================

1. 把整个 AudioBench 文件夹解压到任意可写目录（U 盘也行）。
2. 双击 AudioBench.exe 启动。
3. 本目录下的 portable.flag 就是「便携模式」开关：
   有它 → 配置/日志/缓存全部写在同目录的 userdata\ 里，不碰 APPDATA、不写注册表；
   删掉它 → 变成安装模式（配置写 %APPDATA%\AudioBench\，缓存写 %LOCALAPPDATA%\AudioBench\cache\）。
4. 如果目录不可写，程序会退到 %TEMP%\AudioBench-<pid> 并在标题栏提示。

主要功能
--------
· 多轨编排：导入 wav/mp3/flac/ogg/m4a/aac/wma，轨道头 11 个元素 + 混音器，中英双语
· 编辑：拖移 / 裁剪 / 分割(S) / 删除(Del) / 片段淡入淡出 / 变速 50–200% / 变调 ±12 半音
· 导出：mp3 320/192/128、wav 16/24、flac，混音或单轨，命名模板 {track}_{index}_{date}
· 格式转换：NCM → mp3/flac（仅本地已有文件），批量队列，失败清单 CSV
· 音轨分离：vocals / drums / bass / guitar / piano / other（其他含弦乐），可取消、可一键建轨

自检（排障用，Wine/CrossOver 下同样可用）
----------------------------------------
    AudioBench.exe --selftest --out selftest.json
    AudioBench.exe --selftest --deep --out selftest.json    （额外加载模型、编码一次 mp3）

压测
----
    AudioBench.exe --audio-probe --seconds 10 --tracks 8 --stretch 1.5

常用参数
--------
    --open <工程.abproj>      启动时直接打开工程
    --lang zh_CN|en_US|auto   界面语言
    --scale 1.25              界面缩放
    --backend shim|sounddevice|auto
    --buffer 128|256|512|1024
    --audio-device "<设备名>"
    --data-root <目录>        强制指定数据目录（测试用）

日志：userdata\logs\AudioBench.log（10MB × 3 滚动），崩溃另存 crash-*.log。
分离模型：assets\models\htdemucs_6s.opt.onnx（272MB，仅个人学习用途）。

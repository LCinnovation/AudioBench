"""生成并编译 Qt 翻译文件（M1 的 i18n 流水线）。

做法：
  1. 扫源码里的 tr(上下文, 英文) 调用，得到当前全部待翻译串（不漏）；
  2. 用下面的 ZH 映射生成 `assets/i18n/ab_zh_CN.ts`；
  3. 调 `pyside6-lrelease` 编成 `.qm`（运行时加载的就是它）。

用法：
    python -m ab.tools.make_i18n            # 生成 + 编译
    python -m ab.tools.make_i18n --check     # 只检查有没有漏翻（CI 用，漏了退出码 1）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
# .../AudioBench/src/ab → 仓库根
OUT = SRC.parents[1] / "assets" / "i18n"

PATTERN = re.compile(r'tr\("([^"]+)",\s*"((?:[^"\\]|\\.)*)"\)')

ZH: dict[tuple[str, str], str] = {
    # ---------------------------------------------------------------- Dialog
    ("Dialog", "About"): "关于",
    ("Dialog", "Audio backend"): "音频后端",
    ("Dialog", "Auto"): "自动",
    ("Dialog", "Buffer size"): "缓冲大小",
    ("Dialog", "Cancel"): "取消",
    ("Dialog", "Cannot open project"): "无法打开工程",
    ("Dialog", "Cannot save project"): "无法保存工程",
    ("Dialog", "Copy this for bug reports"): "复制以下内容用于反馈问题",
    ("Dialog", "Diagnostics"): "诊断信息",
    ("Dialog", "Discard unsaved changes?"): "放弃未保存的修改？",
    ("Dialog", "Follow system"): "跟随系统",
    ("Dialog", "Import audio"): "导入音频",
    ("Dialog", "Import failed"): "导入失败",
    ("Dialog", "Language"): "语言",
    ("Dialog", "Local multi-track audio workstation. Runs offline."):
        "本地多轨音频工作站，完全离线运行。",
    ("Dialog", "Missing audio files"): "缺失的音频文件",
    ("Dialog", "No supported audio file"): "没有可支持的音频文件",
    ("Dialog", "Open project"): "打开工程",
    ("Dialog", "Output device"): "输出设备",
    ("Dialog", "Save project"): "保存工程",
    ("Dialog", "Settings"): "设置",
    ("Dialog", "System default"): "系统默认",
    ("Dialog", "Unsaved changes"): "未保存的修改",
    ("Dialog", "Recover last session"): "恢复上次会话",
    ("Dialog", "AudioBench did not exit cleanly last time. Recover the autosaved draft?"):
        "上次 AudioBench 没有正常退出。要恢复自动保存的草稿吗？",
    ("Menu", "Format conversion (NCM)..."): "格式转换（NCM）...",
    # ---------------------------------------------------------------- Ncm
    ("Ncm", "NCM format conversion"): "NCM 格式转换",
    ("Ncm", "Drop .ncm files here (local files only)."): "把 .ncm 文件拖到这里（仅限本地文件）",
    ("Ncm", "Output folder"): "输出目录",
    ("Ncm", "Output format"): "输出格式",
    ("Ncm", "Add files…"): "添加文件...",
    ("Ncm", "Add NCM files"): "添加 NCM 文件",
    ("Ncm", "Clear"): "清空",
    ("Ncm", "File"): "文件",
    ("Ncm", "Status"): "状态",
    ("Ncm", "Message"): "说明",
    ("Ncm", "Start conversion"): "开始转换",
    ("Ncm", "Export failures (CSV)"): "导出失败清单（CSV）",
    ("Ncm", "Close"): "关闭",
    ("Ncm", "Done: {ok} succeeded, {fail} failed"): "完成：成功 {ok} 个，失败 {fail} 个",
    ("Ncm", "Nothing failed"): "没有失败项",
    ("Ncm", "Save failure list"): "保存失败清单",
    ("Ncm", "Before you start"): "开始之前",
    ("Ncm", "This tool only converts NCM files you already have locally. "
            "It does not download, search or bypass any paywall."):
        "本工具只转换你本地已有的 NCM 文件，不提供下载、搜索或绕过付费墙的能力。",
    ("Ncm", "Continue?"): "继续？",
    # ---------------------------------------------------------------- Sep
    ("Sep", "Separate stems"): "音轨分离",
    ("Sep", "Model"): "模型",
    ("Sep", "No stem track could be created"): "没有一条 stem 轨道创建成功",
    ("Sep", "{n} stems could not be loaded: {names}"): "有 {n} 条 stem 读取失败：{names}",
    ("Sep", "Source track"): "源轨道",
    ("Sep", "Choose file…"): "选择文件...",
    ("Sep", "Choose file"): "选择文件",
    ("Sep", "Stems"): "分离轨道",
    ("Sep", "CPU threads"): "CPU 线程",
    ("Sep", "Ready"): "就绪",
    ("Sep", "Start"): "开始分离",
    ("Sep", "Cancel"): "取消",
    ("Sep", "Close"): "关闭",
    ("Sep", "Create tracks"): "创建轨道",
    ("Sep", "Model: {name} ({mb:.0f} MB)"): "模型：{name}（{mb:.0f} MB）",
    ("Sep", "Model not installed — see docs/models.md"): "模型未安装——见 docs/models.md",
    ("Sep", "No audio selected"): "未选择音频",
    ("Sep", "~{eta:.0f}s for {dur:.0f}s audio"): "约 {eta:.0f} 秒（音频 {dur:.0f} 秒）",
    ("Sep", "Select at least one stem"): "至少选择一条分离轨道",
    ("Sep", "Starting worker…"): "正在启动分离进程...",
    ("Sep", "Cancelled — no tracks were created"): "已取消——没有创建任何轨道",
    ("Sep", "Model {name} on {threads} threads"): "模型 {name}，{threads} 线程",
    ("Sep", "Decoded {sec:.0f}s, starting inference…"): "已解码 {sec:.0f} 秒，开始推理...",
    ("Sep", "Model loaded in {t:.1f}s"): "模型加载耗时 {t:.1f} 秒",
    ("Sep", "Chunk {i}/{n} · ETA {eta:.0f}s · RSS {rss:.0f} MB"):
        "第 {i}/{n} 块 · 预计剩余 {eta:.0f} 秒 · 内存 {rss:.0f} MB",
    ("Sep", "Done in {sec:.0f}s (RTF {rtf:.2f})"): "完成，用时 {sec:.0f} 秒（RTF {rtf:.2f}）",
    ("Sep", "Separation failed"): "分离失败",
    ("Sep", "Created {n} stem tracks"): "已创建 {n} 条分离轨道",
    ("Sep", "Separation runtime missing — reinstall AudioBench"): "分离运行时缺失——请重新安装 AudioBench",
    # ---------------------------------------------------------------- Guide / 帮助
    # ---------------------------------------------------------------- 分轨名（给小白看中文）
    ("Stem", "Vocals"): "人声",
    ("Stem", "Drums"): "鼓",
    ("Stem", "Bass"): "贝斯",
    ("Stem", "Guitar"): "吉他",
    ("Stem", "Piano"): "钢琴",
    ("Stem", "Other (incl. strings)"): "其他（含弦乐）",
    # ---------------------------------------------------------------- 新手引导（浮层）
    ("Guide", "Beginner's guide"): "新手教程",
    ("Guide", "Close"): "关闭",
    ("Guide", "Step 1 · Bring a song in"): "第 1 步：把歌拖进来",
    ("Guide", "Step 2 · Press Space to listen"): "第 2 步：按空格试听",
    ("Guide", "Step 3 · Meet the track panel"): "第 3 步：认识左边这一栏",
    ("Guide", "Step 4 · Select a bar to edit it"): "第 4 步：选中就能改",
    ("Guide", "Step 5 · Split vocals from backing"): "第 5 步：分开人声和伴奏",
    ("Guide", "Step 6 · Export an mp3"): "第 6 步：导出成 mp3",
    ("Guide", "Hold your song with the mouse (mp3, wav or flac), drag it onto the "
              "dark area below and let go. You can also press Ctrl+I and pick a file.\n"
              "A long bar appears — that bar is your song."):
        "用鼠标按住你的歌（mp3、wav、flac 都行），拖到下面这块深色区域，松手就行；"
        "也可以按 Ctrl+I 选文件。\n拖进来后，会多出一条横条——那就是这首歌。",
    ("Guide", "Nothing happens? Check the file ends with mp3/wav/flac/ogg/m4a/aac/wma."):
        "没反应？确认文件后缀是 mp3/wav/flac/ogg/m4a/aac/wma 之一。",
    ("Guide", "Space starts and stops playback; Home jumps back to the beginning.\n"
              "To practise one part over and over, hold Shift and drag a short "
              "range on the ruler — that range will loop."):
        "按空格键开始播放，再按一下停止；按 Home 回到开头。\n"
        "想反复练同一段：按住 Shift 在顶部刻度尺上拖一小段，就会循环这一段。",
    ("Guide", "No sound? Look at the M (mute) button on the track first."):
        "没声音？先看轨道上的 M（静音）有没有被点亮。",
    ("Guide", "Left of every bar is a small panel: the song name on top "
              "(double-click to rename), M mutes, S solos (only that track plays), "
              "the round knob is volume, EQ shapes high and low frequencies.\n"
              "Not sure what a button does? Hover over it for a tooltip."):
        "每条横条左边是一个小面板：最上面是歌名（双击可以改名），M 是静音、"
        "S 是独奏（只听这一条），圆钮是音量，EQ 是高低音。\n"
        "拿不准就先把鼠标停上去，会显示提示。",
    ("Guide", "Click a bar once to select it. A row of controls appears above: "
              "speed (slower/faster, pitch stays), pitch (key change, speed stays), "
              "volume, fade in and fade out.\n"
              "Drag the left or right edge of the bar to trim it. Wrong move? Ctrl+Z."):
        "在横条上单击一下，它会被选中，上方出现一排控件：速度（放慢/加快，音高不变）、"
        "变调、音量、渐入/渐出。\n拖横条的左右边缘可以剪短；拖错了按 Ctrl+Z 撤销。",
    ("Guide", "Click the person icon at the far right of the transport bar, keep "
              "\"4 stems · high quality\", then press Start.\n"
              "When the progress bar finishes, press Create tracks: vocals, drums, "
              "bass and other appear, and the original track is muted for you."):
        "点走带条最右边的人形按钮，保持「四轨 · 高音质」，点「开始分离」。\n"
        "等进度条走完，再点「创建轨道」，就会多出人声、鼓、贝斯、其他四条轨道，"
        "原来那条会自动静音。",
    ("Guide", "A 3-minute song takes about 1 minute. You can cancel any time."):
        "3 分钟的歌大约要 1 分钟；中途不想等可以点「取消」。",
    ("Guide", "Press Ctrl+E: choose a format (mp3 320 is the usual choice), pick a "
              "folder and press Start.\n"
              "The folder opens with your new file selected when it is done."):
        "按 Ctrl+E 打开导出窗口：选格式（mp3 320 最常用）、选文件夹，点「开始导出」。\n"
        "完成后会自动打开文件夹并选中文件。",
    ("Guide", "Press F1 any time to reopen this tutorial and the shortcut list."):
        "随时按 F1 可以重新打开这份教程和快捷键表。",    ("Guide", "Help"): "帮助",
    ("Guide", "AudioBench in four steps"): "四步上手 AudioBench",
    ("Guide", "Import audio"): "导入音频",
    ("Guide", "Loop and slow down"): "循环与放慢",
    ("Guide", "Separate stems"): "分离轨道",
    ("Guide", "Export"): "导出",
    ("Guide", "Keyboard shortcuts"): "快捷键",
    ("Guide", "Key"): "按键",
    ("Guide", "Action"): "动作",
    ("Guide", "Play / pause"): "播放/暂停",
    ("Guide", "Back to start"): "回到起点",
    ("Guide", "Save / save as"): "保存 / 另存为",
    ("Guide", "Undo / redo"): "撤销 / 重做",
    ("Guide", "Export audio"): "导出音频",
    ("Guide", "Delete selected clip"): "删除选中片段",
    ("Guide", "Split at playhead"): "在播放头处分割",
    ("Guide", "Duplicate clip"): "复制片段",
    ("Guide", "Zoom timeline"): "缩放时间线",
    ("Guide", "Show / hide mixer"): "显示/收起混音器",
    ("Guide", "Set A-B loop"): "设置 A-B 循环",
    ("Guide", "Add marker at playhead"): "在播放头加标记",
    ("Guide", "Open this help"): "打开本帮助",
    ("Guide", "Replay the guided tour"): "重播新手引导",
    ("Guide",
     "Shortcut customisation is planned; the table above is read-only for now."):
        "快捷键自定义排在后续版本，上表当前只读。",
    ("Guide",
     "1. Drag audio files onto the timeline, or press Ctrl+I.\n"
     "2. Supported: wav / mp3 / flac / ogg / m4a / aac / wma.\n"
     "3. Each file becomes a track; the colour bar and icon identify it.\n"
     "4. Double-click a track name to rename it."):
        "1. 把音频文件拖到时间线，或按 Ctrl+I。\n"
        "2. 支持 wav / mp3 / flac / ogg / m4a / aac / wma。\n"
        "3. 每个文件变成一条轨道，左侧色条与图标用于识别。\n"
        "4. 双击轨道名可以重命名。",
    ("Guide",
     "1. Shift-drag on the ruler to set an A-B loop; double-click to clear.\n"
     "2. Turn on the metronome for a steady click.\n"
     "3. Select a clip and set speed 50–200% (pitch kept) or pitch ±12 semitones "
     "(tempo kept) in the inspector.\n"
     "4. Solo a track (S) to practise along."):
        "1. 在标尺上按住 Shift 拖动设置 A-B 循环，双击取消。\n"
        "2. 打开节拍器，跟着稳定的咔哒声练习。\n"
        "3. 选中片段，在检查器里设速度 50–200%（保持音高）或变调 ±12 半音（保持速度）。\n"
        "4. 用 S 独奏某条轨道，便于跟弹。",
    ("Guide",
     "1. Press the stem button in the transport bar.\n"
     "2. Pick the source track (or a file) and choose the stems you need.\n"
     "3. Watch progress and ETA; cancel at any time — no tracks are created.\n"
     "4. On finish press Create tracks: six tracks appear and the source is muted.\n"
     "   The 'other' stem also contains strings."):
        "1. 点击走带条右侧的分离按钮。\n"
        "2. 选择源轨道（或文件），勾选需要的分离轨。\n"
        "3. 观察进度与预计剩余时间；随时可取消，取消不会产生任何轨道。\n"
        "4. 完成后点「创建轨道」：出现六条轨道，源轨自动静音。\n"
        "   other 轨里也包含弦乐。",
    ("Guide",
     "1. Press Ctrl+E (or the export button).\n"
     "2. Choose format (mp3 320/192/128, wav 16/24, flac), scope and folder.\n"
     "3. File names follow {track}_{index}_{date}; illegal characters are replaced.\n"
     "4. When it finishes the folder opens with the file selected."):
        "1. 按 Ctrl+E（或点导出按钮）。\n"
        "2. 选格式（mp3 320/192/128、wav 16/24、flac）、范围和目录。\n"
        "3. 文件名按 {track}_{index}_{date} 生成，非法字符会被替换。\n"
        "4. 完成后会自动打开目录并选中文件。",
    # ---------------------------------------------------------------- 引导浮层
    ("Guide", "Skip"): "跳过",
    ("Guide", "Back"): "上一步",
    ("Guide", "Next"): "下一步",
    ("Guide", "Done"): "完成",
    ("Guide", "Step {i} / {n}"): "第 {i}/{n} 步",
    ("Guide", "Tour finished — press F1 for help"): "引导已完成——按 F1 打开帮助",
    ("Guide", "Tour skipped — press F1 to open help"): "已跳过引导——按 F1 打开帮助",
    ("Guide", "1. Bring your audio in"): "1. 把音频导进来",
    ("Guide", "2. Play, loop, slow down"): "2. 播放、循环、放慢",
    ("Guide", "3. Trim, fade, change speed"): "3. 裁剪、淡变、变速",
    ("Guide", "4. Separate into stems"): "4. 分离成六轨",
    ("Guide", "5. Export when done"): "5. 完成后导出",
    ("Guide", "Drag wav / mp3 / flac files onto the timeline, or press Ctrl+I. "
              "Every file becomes a track with its own colour."):
        "把 wav / mp3 / flac 文件拖到时间线，或按 Ctrl+I。每个文件都会变成一条带颜色的轨道。",
    ("Guide", "Tip: you can also drop files onto the window."): "小提示：文件也可以直接拖到窗口上。",
    ("Guide", "Space plays and stops. Shift-drag on the ruler sets an A-B loop. "
              "Turn on the metronome when practising a part."):
        "空格播放/停止；在标尺上按住 Shift 拖动可设 A-B 循环；练习某一段时打开节拍器。",
    ("Guide", "Drag a clip edge to trim, drag its top corners for fades. "
              "Select a clip and use the inspector to change speed 50–200% "
              "(pitch kept) or pitch ±12 semitones (tempo kept)."):
        "拖片段左右边缘可裁剪，拖上沿两角做淡入淡出。选中片段后，在上方检查器里改速度 "
        "50–200%（保持音高）或变调 ±12 半音（保持速度）。",
    ("Guide", "The stem button splits a song into vocals / drums / bass / "
              "guitar / piano / other. Progress and ETA are shown; you can "
              "cancel at any time."):
        "分离按钮把一首歌拆成 人声/鼓/贝斯/吉他/钢琴/其他 六轨，显示进度与剩余时间，随时可取消。",
    ("Guide", "Runs offline on CPU. The 'other' stem also contains strings."):
        "纯 CPU 离线推理；other 轨里也包含弦乐。",
    ("Guide", "Ctrl+E exports the mix or a single track to mp3 / wav / flac. "
              "Names follow the template and the folder opens when finished."):
        "Ctrl+E 导出混音或单轨为 mp3 / wav / flac，文件名按模板生成，完成后自动打开目录。",
    # ---------------------------------------------------------------- 菜单/标记
    ("Menu", "Guided tour"): "新手引导",
    ("Menu", "Beat grid"): "节拍网格",
    ("Menu", "Add marker at playhead"): "在播放头添加标记",
    ("Menu", "Clear markers"): "清除所有标记",
    ("Menu", "Light theme (experimental)"): "浅色主题（实验）",
    ("Menu", "Records the preference; the palette ships next version"): "当前只记录偏好，色板在下一版接入",
    ("Menu", "Light theme is an interface only for now"): "浅色主题目前只留接口",
    ("Menu", "Theme applies after restart"): "主题将在重启后生效",
    ("Timeline", "Add marker here"): "在此添加标记",
    ("Timeline", "Delete marker"): "删除标记",
    ("Timeline", "Rename marker"): "重命名标记",
    ("Timeline", "Rename marker…"): "重命名标记...",
    ("Timeline", "Marker name"): "标记名称",
    # ---------------------------------------------------------------- Menu
    ("Menu", "&File"): "文件(&F)",
    ("Menu", "&Edit"): "编辑(&E)",
    ("Menu", "&Track"): "轨道(&T)",
    ("Menu", "&View"): "视图(&V)",
    ("Menu", "&Help"): "帮助(&H)",
    ("Menu", "About"): "关于",
    ("Menu", "Add track"): "添加轨道",
    ("Menu", "Delete track"): "删除轨道",
    ("Menu", "Diagnostics"): "诊断",
    ("Menu", "Exit"): "退出",
    ("Menu", "Import audio..."): "导入音频...",
    ("Menu", "Mixer"): "混音器",
    ("Menu", "New project"): "新建工程",
    ("Menu", "Open project..."): "打开工程...",
    ("Menu", "Redo"): "重做",
    ("Menu", "Save"): "保存",
    ("Menu", "Save as..."): "另存为...",
    ("Menu", "Settings..."): "设置...",
    ("Menu", "Undo"): "撤销",
    ("Menu", "Zoom in"): "放大",
    ("Menu", "Zoom out"): "缩小",
    # ---------------------------------------------------------------- Mixer
    ("Mixer", "Master"): "主输出",
    # ---------------------------------------------------------------- Status
    ("Status", "Audio device unavailable — see log"): "音频设备不可用——详见日志",
    ("Status", "Audio: unavailable"): "音频：不可用",
    ("Status", "Import already running"): "已在导入中",
    ("Status", "Imported {n} file(s)"): "已导入 {n} 个文件",
    ("Status", "Importing..."): "导入中...",
    # ---------------------------------------------------------------- Timeline
    ("Timeline", "Delete clip"): "删除片段",
    ("Timeline", "Duplicate clip"): "复制片段",
    ("Timeline", "Split at playhead"): "在播放头处分割",
    ("Timeline", "Drop audio files here, or use Import (Ctrl+O)"):
        "把音频文件拖到这里，或用「导入」(Ctrl+O)",
    ("Timeline", "Set loop start here"): "以此为循环起点",
    # ---------------------------------------------------------------- Inspector
    ("Inspector", "No clip selected"): "未选中片段",
    ("Inspector", "Speed"): "速度",
    ("Inspector", "1×"): "原速",
    ("Inspector", "50–200%, pitch preserved"): "50–200%，保持音高",
    ("Inspector", "Pitch"): "变调",
    ("Inspector", "±12 semitones, tempo preserved"): "±12 半音，保持速度",
    ("Inspector", "Gain"): "增益",
    ("Inspector", "Fade in/out"): "渐入/渐出",
    ("Inspector", "Linear"): "线性",
    ("Inspector", "Exponential"): "指数",
    ("Inspector", "Split"): "分割",
    ("Inspector", "Duplicate"): "复制",
    ("Inspector", "Delete"): "删除",
    ("Menu", "Export audio..."): "导出音频...",
    # ---------------------------------------------------------------- Export
    ("Export", "Export audio"): "导出音频",
    ("Export", "Format"): "格式",
    ("Export", "Scope"): "范围",
    ("Export", "Mix (all tracks)"): "混音（全部轨道）",
    ("Export", "Current track only"): "仅当前轨道",
    ("Export", "Folder"): "目录",
    ("Export", "Choose folder"): "选择目录",
    ("Export", "File name template"): "文件名模板",
    ("Export", "Available fields: {track} {index} {date}"): "可用字段：{track} {index} {date}",
    ("Export", "Open folder when finished"): "完成后打开所在目录",
    ("Export", "Start export"): "开始导出",
    ("Export", "Rendering…"): "渲染中…",
    ("Export", "Done: {name}  ({sec:.1f}s)"): "完成：{name}（{sec:.1f}s）",
    ("Export", "clipping detected"): "检测到削波",
    ("Export", "Export failed"): "导出失败",
    ("Export", "Possible clipping"): "可能削波",
    ("Export", "Peak level is above 0 dBFS ({peak:.2f}). Export anyway?"):
        "峰值超过 0 dBFS（{peak:.2f}）。仍要导出吗？",
    ("Export", "Select a track first"): "请先选择一条轨道",
    ("Export", "Nothing to export"): "没有可导出的内容",
    # ---------------------------------------------------------------- TrackHeader
    ("TrackHeader", "Collapse"): "折叠",
    ("TrackHeader", "EQ on/off"): "EQ 开关",
    ("TrackHeader", "Fade in (s)"): "渐入（秒）",
    ("TrackHeader", "Fade out (s)"): "渐出（秒）",
    ("TrackHeader", "Fade seconds (0 disables)"): "渐变秒数（0 = 关闭）",
    # ---------------------------------------------------------------- Transport
    ("Transport", "A-B loop"): "A-B 循环",
    ("Transport", "BPM"): "BPM",
    ("Transport", "C maj"): "C 大调",
    ("Transport", "Double-click to switch time / bars"): "双击切换「时码 / 小节」",
    ("Transport", "Export (M2)"): "导出（M2）",
    ("Transport", "Format convert (M2)"): "格式转换（M2）",
    ("Transport", "Go to start"): "回到起点",
    ("Transport", "Help"): "帮助",
    ("Transport", "Master volume"): "主音量",
    ("Transport", "Metronome"): "节拍器",
    ("Transport", "Play / pause (Space)"): "播放/暂停（空格）",
    ("Transport", "Separate stems (M3)"): "音轨分离（M3）",
    ("Transport", "Settings"): "设置",
    ("Transport", "Stop"): "停止",
}


def collect() -> dict[str, set[str]]:
    """用 AST 收集 `tr(ctx, text)`，而不是正则 —— 这样隐式字符串拼接、
    `\\n` 转义、引号转义都能拿到「运行时真正的那个字符串」。"""
    import ast

    out: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "tr"):
                continue
            if len(node.args) < 2:
                continue
            ctx, text = node.args[0], node.args[1]
            if isinstance(ctx, ast.Constant) and isinstance(text, ast.Constant):
                if isinstance(ctx.value, str) and isinstance(text.value, str):
                    out.setdefault(ctx.value, set()).add(text.value)
    return out


def missing() -> list[tuple[str, str]]:
    found = collect()
    gaps = []
    for ctx, items in found.items():
        for s in items:
            if (ctx, s) not in ZH:
                gaps.append((ctx, s))
    return sorted(gaps)


def build_ts() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    found = collect()
    root = ET.Element("TS", {"version": "2.1", "language": "zh_CN", "sourcelanguage": "en_US"})
    for ctx in sorted(found):
        c = ET.SubElement(root, "context")
        ET.SubElement(c, "name").text = ctx
        for src in sorted(found[ctx]):
            msg = ET.SubElement(c, "message")
            ET.SubElement(msg, "source").text = src
            tr = ET.SubElement(msg, "translation")
            tr.text = ZH.get((ctx, src), "")
            if not tr.text:
                tr.set("type", "unfinished")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    ts = OUT / "ab_zh_CN.ts"
    tree.write(ts, encoding="utf-8", xml_declaration=True)
    return ts


def compile_qm(ts: Path) -> Path | None:
    exe = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if exe is None:
        # PySide6 安装目录里找
        try:
            import PySide6

            cand = Path(PySide6.__file__).parent / "lrelease.exe"
            if cand.exists():
                exe = str(cand)
        except Exception:
            pass
    if exe is None:
        print("lrelease not found; .qm NOT regenerated")
        return None
    qm = ts.with_suffix(".qm")
    subprocess.run([exe, str(ts), "-qm", str(qm)], check=True, capture_output=True)
    return qm


def main(argv: list[str]) -> int:
    gaps = missing()
    if "--check" in argv:
        if gaps:
            for ctx, s in gaps:
                print(f"MISSING  {ctx}: {s}")
            print(f"\n{gaps.__len__()} untranslated string(s)")
            return 1
        print(f"i18n OK: {sum(len(v) for v in collect().values())} strings, all translated")
        return 0
    ts = build_ts()
    print(f"wrote {ts}")
    if gaps:
        print(f"WARNING: {len(gaps)} untranslated string(s):")
        for ctx, s in gaps:
            print(f"  {ctx}: {s}")
    qm = compile_qm(ts)
    if qm:
        print(f"wrote {qm} ({qm.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

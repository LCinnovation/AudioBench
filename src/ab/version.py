"""版本与产品常量（单一来源：打包脚本、日志、自检、关于页都读这里）。"""

APP_NAME = "AudioBench"
APP_ID = "AudioBench"
VERSION = "0.5.1"
BUILD_DATE = "2026-10-20"

# 工程文件扩展名（不含点）
PROJECT_EXT = "abproj"

# 轨道颜色（8 色循环，§4.1）
TRACK_COLORS = [
    "#4EA1B8",  # 蓝绿
    "#F5A524",  # 琥珀
    "#9B7EDE",  # 紫
    "#5FB878",  # 绿
    "#E5484D",  # 红
    "#E07A5F",  # 陶土
    "#5B8FF9",  # 蓝
    "#B8B8C0",  # 灰
]

# 乐器类型 → 图标名（ui/icons.py 里一一对应）
INSTRUMENTS = ["vocal", "drum", "bass", "guitar", "piano", "other"]

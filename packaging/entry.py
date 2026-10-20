"""PyInstaller 入口：只做一件事——把控制权交给 ab.app.main。"""

from __future__ import annotations

import sys

from ab.app.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

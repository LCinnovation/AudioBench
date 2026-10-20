"""> python -m ab  的入口。"""

from __future__ import annotations

import sys

from .app.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

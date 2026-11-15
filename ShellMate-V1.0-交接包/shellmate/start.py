"""免安装启动入口：python start.py --mock

把 src/ 加进模块搜索路径，省掉 pip install 这一步。
想装成全局命令时再用：python -m pip install -e .
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from shellmate.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

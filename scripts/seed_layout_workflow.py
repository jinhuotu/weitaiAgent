"""已弃用：旧「充电站平面布置」已由 v2 替代。

本脚本转发到 seed_layout_workflow_v2.py（案例库 + 发布 v2 + 下线旧工作流）。
请改跑：

  python scripts/seed_layout_workflow_v2.py
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    print("[warn] seed_layout_workflow.py is deprecated; forwarding to v2")
    runpy.run_path(str(Path(__file__).with_name("seed_layout_workflow_v2.py")), run_name="__main__")

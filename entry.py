#!/usr/bin/env python3
"""PyInstaller 入口脚本"""
import sys
from pathlib import Path

# 将 src 目录加入 Python 路径
src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(src_dir))

# 导入并执行 CLI
from aion.cli.main import main

if __name__ == "__main__":
    main()
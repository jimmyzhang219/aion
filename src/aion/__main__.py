"""aion 包入口模块

支持 ``python -m aion`` 方式启动，将命令行参数转发给 CLI 主入口。
"""

from aion.cli.main import main

if __name__ == "__main__":
    main()

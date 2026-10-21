"""命令行入口：``python -m open_dream_rsi``。

学习链路第 1 步：所有子命令（loop / dashboard / bench …）都从这里进入 ``cli.main``。
"""

from open_dream_rsi.cli import main

if __name__ == "__main__":
    # 把 CLI 的退出码原样交给操作系统（0 = 成功）
    raise SystemExit(main())

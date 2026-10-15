# core/logger.py - 统一日志配置（标准库 logging，无需额外依赖）
#
# 设计说明：
# - handler 挂在 root logger 上，项目内所有模块统一使用 logging.getLogger(__name__)
#   （core.* / data.* 命名空间的日志都会向上传播到 root 输出）
# - data/ 目录因此不依赖本文件即可独立分发：独立运行（python -m data.cli）时
#   由 data/cli.py 自行 basicConfig；在 pytest 下运行时本文件完成统一配置
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def _setup_logger():
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 避免重复添加 handler（多次 import / 重复初始化）
    if root.handlers:
        return logging.getLogger("apitest")

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 文件输出：output/logs/run.log，按天滚动，保留 14 天（与其他运行产物统一在 output/ 下）
    log_dir = Path(__file__).parent.parent / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = TimedRotatingFileHandler(
        log_dir / "run.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 压低第三方库的噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("faker").setLevel(logging.WARNING)

    # 兼容 core 内既有写法（from core.logger import logger）
    return logging.getLogger("apitest")


logger = _setup_logger()

# plugins/log_setup/plugin.py - 统一日志配置积木
#
# 职责：把 root logger 配置成「控制台 + 按天滚动文件」，core.* 日志统一收口。
# 核心只需 logging.getLogger(__name__) 产出日志，配置本身由本积木负责。
#
# 加载：pytest.ini addopts 中的 `-p plugins.log_setup.plugin`
# CLI 复用：casegen 工具等非 pytest 场景可 `from plugins.log_setup.plugin import configure`
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = Path(os.getenv("APITEST_OUTPUT_DIR", PROJECT_ROOT / "output"))
LOG_DIR = OUTPUT_ROOT / "logs"


def configure():
    """配置 root logger：控制台 + output/logs/run.log（按天滚动，保留 14 天）"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 避免重复添加 handler（多次 import / 重复初始化）
    if root.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 文件输出：output/logs/run.log，按天滚动，保留 14 天
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = TimedRotatingFileHandler(
        LOG_DIR / "run.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 压低第三方库的噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def pytest_configure(config):
    """pytest 启动即挂上日志配置（早于用例收集）"""
    configure()

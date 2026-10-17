# plugins/loader.py - 积木装载器（pytest.ini addopts 中唯一硬编码的 -p 入口）
#
# 职责：pytest 启动早期读取根目录 plugins.yaml 清单，
# 把启用的积木逐个注册为 pytest 插件（效果等同手写一排 -p，但启停不用改 pytest.ini）。
#
# 启停方式：
#   长期拔出 = 注释 plugins.yaml 中对应行（核心框架照常运行）
#   临时禁用 = 环境变量 APITEST_PLUGINS_OFF=reporting,http_heal（短名逗号分隔；* 表示全拔，即纯核心模式）
import os
from pathlib import Path

import pytest
import yaml

# 清单文件：项目根目录 plugins.yaml
MANIFEST = Path(__file__).resolve().parent.parent / "plugins.yaml"


def _load_manifest() -> list:
    """读取积木清单；文件缺失视为无积木（纯核心模式）"""
    if not MANIFEST.exists():
        return []
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    return [str(x).strip() for x in (data.get("plugins") or []) if str(x).strip()]


def pytest_load_initial_conftests(early_config, parser, args):
    """注册清单中未被禁用的积木（import 时积木自行完成端口注册与钩子挂载）"""
    off = {x.strip() for x in os.getenv("APITEST_PLUGINS_OFF", "").split(",") if x.strip()}
    if "*" in off:
        return
    for name in _load_manifest():
        short = name.split(".")[1] if "." in name else name
        if short in off or name in off:
            continue
        try:
            early_config.pluginmanager.import_plugin(name)
        except Exception as e:
            raise pytest.UsageError(
                f"积木 {name} 加载失败: {e}；"
                f"如需拔出该积木，请注释 plugins.yaml 中对应行，"
                f"或设环境变量 APITEST_PLUGINS_OFF={short}"
            ) from e

# core/config_loader.py - 环境配置加载：YAML 读取 + .env 注入 + 环境变量插值
#
# YAML 中的敏感信息不写明文，使用占位符由环境变量注入：
#   password: "${MYSQL_PASSWORD}"          # 必填风格：未设置时替换为空串并告警
#   password: "${MYSQL_PASSWORD:-123456}"  # 带默认值（默认值仅建议用于非敏感配置）
#
# 环境变量来源优先级：系统环境变量 > 项目根目录 .env 文件 > 占位符默认值
import logging
import os
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 匹配 ${VAR} 与 ${VAR:-default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_dotenv(dotenv_path: Path = None):
    """加载项目根目录 .env 文件。

    python-dotenv 为可选依赖：未安装或 .env 不存在时静默跳过，
    此时仍可使用系统环境变量（如 CI 中直接注入凭据的场景）。
    """
    path = dotenv_path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv as _load_dotenv
    except ImportError:
        logger.warning("检测到 .env 文件但未安装 python-dotenv，已跳过（pip install python-dotenv）")
        return
    # override=False：真实系统环境变量优先于 .env
    _load_dotenv(path, override=False, encoding="utf-8")


def expand_env_vars(obj):
    """递归替换 str/dict/list 中的 ${VAR}、${VAR:-默认值} 占位符

    注意：data/infra/env_loader.py 中有一份语义完全相同的实现。
    两份**刻意保留**（硬约束：data/ 不得 import core/，需可整体拷贝到其他项目）；
    修改其中一份时请同步另一份，避免插值行为漂移。
    """

    def _replace(match):
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        # 未设置且无默认值：不抛异常（避免纯 --csv 场景被无关配置卡死），但显式告警
        logger.warning("环境变量 %s 未设置，对应配置项已替换为空串", name)
        return ""

    if isinstance(obj, str):
        return _ENV_VAR_PATTERN.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(v) for v in obj]
    return obj


def load_config(env: str) -> dict:
    """加载指定环境的完整配置：注入 .env → 读 YAML → 环境变量插值

    :param env: 环境名（dev / staging / prod）
    :return: 插值后的配置字典
    """
    load_dotenv()
    config_path = PROJECT_ROOT / "config" / f"{env}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"环境配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return expand_env_vars(raw)

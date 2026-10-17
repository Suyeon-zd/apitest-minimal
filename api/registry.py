# api/registry.py - 接口注册中心加载器
#
# 从 api/endpoints.yaml 加载接口定义，为 YAML 用例提供 "逻辑接口名 → method/path/headers"
# 的唯一解析入口。用例只写 api: system.get_info，路径维护集中在 endpoints.yaml。
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.config_loader import expand_env_vars

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Endpoint:
    """单个接口的注册信息"""

    key: str                        # 完整逻辑名，如 system.get_info
    method: str                     # HTTP 方法，统一大写
    path: str                       # 接口路径（自动拼接 base_url，运行时支持 ${var} 业务变量）
    headers: dict = field(default_factory=dict)   # 接口专属请求头（加载时完成环境变量插值）
    body_type: str = "json"         # 非 GET 请求体发送方式：json（默认）/ data（raw 文本或表单）


class EndpointRegistry:
    """接口注册中心：启动时加载一次，用例收集期即可发现 key 拼写错误"""

    # 允许的请求体发送方式
    _SUPPORTED_BODY_TYPES = ("json", "data")

    def __init__(self, endpoints: dict = None):
        self._endpoints = {}
        for module, api_group in (endpoints or {}).items():
            for name, spec in (api_group or {}).items():
                key = f"{module}.{name}"
                if not isinstance(spec, dict) or not spec.get("path") or not spec.get("method"):
                    raise ValueError(f"接口注册项 '{key}' 配置非法：必须包含 method 与 path")
                body_type = str(spec.get("body_type", "json")).lower()
                if body_type not in self._SUPPORTED_BODY_TYPES:
                    raise ValueError(
                        f"接口注册项 '{key}' 的 body_type='{body_type}' 非法，"
                        f"仅支持 {self._SUPPORTED_BODY_TYPES}"
                    )
                self._endpoints[key] = Endpoint(
                    key=key,
                    method=str(spec["method"]).upper(),
                    path=spec["path"],
                    # 仅 headers 做环境变量插值；path 中的 ${xxx} 是运行时业务变量（如资源 ID），
                    # 由测试执行期的 replace_vars 处理，不能在此被环境插值误替换
                    headers=expand_env_vars(spec.get("headers") or {}),
                    body_type=body_type,
                )
        # 启动后做"同 method+path 不同 key"诊断：避免像 orgList.get_orgList 与 system.org_list
        # 各自登记又指向同一 path 导致用例引用混淆（错误诊断很难定位）。
        # 仅警告：允许历史别名共存不致硬失败，删重需要业务侧确认
        self._diagnose_path_duplicates()

    def _diagnose_path_duplicates(self):
        """同 method+path 被多个 key 登记时输出 warning，并给出建议保留的 key

        - 不抛错：兼容历史遗留（别名、迁移期共存）
        - 建议保留"路径最后出现"的 key（默认策略），便于按文件追加顺序演进
        """
        path_to_keys = defaultdict(list)
        for key, ep in self._endpoints.items():
            path_to_keys[(ep.method, ep.path)].append(key)
        for (method, path), keys in path_to_keys.items():
            if len(keys) <= 1:
                continue
            # 同 path 多 key：按字典序倒序排，保留最后写入的
            keys_sorted = sorted(keys)
            preferred = keys_sorted[-1]
            duplicates = keys_sorted[:-1]
            logger.warning(
                "检测到同 method+path 被多个接口 key 登记: "
                "[%s] %s -> 重复 key=%s; 建议作为别名/历史保留，新增用例请改用: %s",
                method, path, ", ".join(duplicates), preferred,
            )

    @classmethod
    def load(cls, path: Path = None) -> "EndpointRegistry":
        """从 endpoints.yaml 加载注册中心"""
        registry_path = path or (Path(__file__).resolve().parent / "endpoints.yaml")
        if not registry_path.exists():
            raise FileNotFoundError(f"接口注册中心文件不存在: {registry_path}")
        with open(registry_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def get(self, key: str) -> Endpoint:
        """按逻辑名取接口；不存在时抛出带可用列表的友好错误"""
        endpoint = self._endpoints.get(key)
        if endpoint is None:
            available = ", ".join(sorted(self._endpoints)) or "无"
            raise KeyError(f"接口 '{key}' 未在注册中心登记，可用接口: {available}")
        return endpoint

    def __contains__(self, key: str) -> bool:
        return key in self._endpoints

    @property
    def keys(self) -> list:
        """已注册的全部接口逻辑名"""
        return sorted(self._endpoints)

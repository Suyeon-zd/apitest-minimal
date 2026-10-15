# core/case_model.py - 测试用例数据模型（dataclass 定义 + 收集期结构校验）
#
# 用例 YAML 的唯一解析入口：把裸 dict 转成强类型 TestCase。
# 字段非法时在 pytest --collect-only 阶段就报错，避免"配置写错、跑起来才发现"。
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.constants import KNOWN_MARKERS

# 支持的请求体发送方式（与 api/registry.py 中 Endpoint 的取值保持一致）
_BODY_TYPES = ("json", "data")

# 强断言类型：至少出现一种才算"有效断言"。
# status_code 单独存在不算——接口返回 200 但业务码错误、字段缺失都会被放过（假通过）。
STRONG_ASSERT_TYPES = frozenset({
    "header", "json_path", "field_type", "exists", "list_len",
    "contains", "each", "schema", "response_time",
})


def _as_dict(raw: dict, key: str, case_id: str) -> dict:
    """取字典字段并校验类型"""
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"用例 [{case_id}] 的 {key} 必须是字典，实际 {type(value).__name__}")
    return value


def _as_list(raw: dict, key: str, case_id: str) -> list:
    """取列表字段并校验类型"""
    value = raw.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"用例 [{case_id}] 的 {key} 必须是列表，实际 {type(value).__name__}")
    return value


@dataclass
class CleanupStep:
    """用例收尾步骤：用例结束后（不论成败）执行，用于回收本次产生的脏数据"""

    api: Optional[str] = None           # 优先：注册中心逻辑名（method/path 自动解析）
    method: Optional[str] = None        # 或显式指定 METHOD
    path: Optional[str] = None          # 或显式指定 path（支持 ${var} 运行时变量）
    payload: Any = None
    body_type: str = "json"
    headers: Dict[str, Any] = field(default_factory=dict)
    ignore_failure: bool = True         # 收尾失败默认只告警，不影响用例结论

    @classmethod
    def from_dict(cls, raw: Any, case_id: str, index: int) -> "CleanupStep":
        """从 YAML 字典解析单个收尾步骤"""
        if not isinstance(raw, dict):
            raise ValueError(f"用例 [{case_id}] 的 cleanup[{index}] 必须是字典")
        api = raw.get("api")
        method, path = raw.get("method"), raw.get("path")
        if not api and not (method and path):
            raise ValueError(
                f"用例 [{case_id}] 的 cleanup[{index}] 必须提供 api，"
                f"或同时提供 method 与 path"
            )
        body_type = str(raw.get("body_type", "json")).lower()
        if body_type not in _BODY_TYPES:
            raise ValueError(
                f"用例 [{case_id}] 的 cleanup[{index}].body_type='{body_type}' 非法，"
                f"仅支持 {_BODY_TYPES}"
            )
        return cls(
            api=api,
            method=str(method).upper() if method else None,
            path=path,
            payload=raw.get("payload"),
            body_type=body_type,
            headers=raw.get("headers") or {},
            ignore_failure=bool(raw.get("ignore_failure", True)),
        )


def _validate_assertions(assertions: list, case_id: str):
    """校验断言列表：必须非空、每条形如 {type: ...}，且至少一条强断言"""
    if not assertions:
        raise ValueError(
            f"用例 [{case_id}] 缺少 assert 断言块；"
            f"无断言的用例发现不了回归，请至少补充一条（如 json_path / schema）"
        )
    for i, rule in enumerate(assertions):
        if not isinstance(rule, dict) or not rule.get("type"):
            raise ValueError(f"用例 [{case_id}] 的第 {i + 1} 条断言缺少 type 字段: {rule!r}")
    if not any(r["type"] in STRONG_ASSERT_TYPES for r in assertions):
        raise ValueError(
            f"用例 [{case_id}] 只有 status_code 级别的弱断言，"
            f"接口返回 200 但业务失败时无法拦截；请补充至少一条强断言，"
            f"可用类型: {sorted(STRONG_ASSERT_TYPES)}"
        )


@dataclass
class TestCase:
    """单条接口测试用例（对应 test_data/ 下 YAML 的一个列表项）"""

    # 类名以 Test 开头，pytest 会误当成测试类收集，这里显式排除
    __test__ = False

    api: str                                   # 注册中心逻辑名，如 asset.org_top
    api_name: str                              # 用例名（同时作为 pytest 用例 id）
    payload: Any = None                        # 请求参数（GET=query string，其他=body）
    body_type: Optional[str] = None            # json（默认）/ data；None 表示沿用注册中心配置
    headers: Dict[str, Any] = field(default_factory=dict)
    params: Any = None                         # 额外 query 参数（可与 body 并存）
    files: Any = None                          # multipart 文件：{"file": (文件名, 内容, MIME)}
    cookies: Dict[str, Any] = field(default_factory=dict)
    verify: Optional[bool] = None              # 是否校验 TLS 证书（None=用 requests 默认）
    timeout: Any = None                        # 秒数 或 [connect, read]；None=用环境配置
    extract: Dict[str, str] = field(default_factory=dict)   # 响应字段 → 全局变量名
    assertions: List[dict] = field(default_factory=list)    # 对应 YAML 的 assert
    markers: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)     # 依赖的用例 id（前置未过则跳过）
    cleanup: List[CleanupStep] = field(default_factory=list)
    source: str = ""                           # 来源文件名（报错定位用）

    @property
    def id(self) -> str:
        """pytest 用例 id，同时被 depends_on 引用"""
        return self.api_name

    @classmethod
    def from_dict(cls, raw: Any, source: str = "") -> "TestCase":
        """从 YAML 字典构建用例模型，顺带完成全部结构性校验"""
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: 用例必须是字典，实际 {type(raw).__name__}")
        api = raw.get("api")
        if not api:
            raise ValueError(
                f"{source}: 用例缺少 api 字段，"
                f"请填写接口注册中心中的逻辑名（如 user.get_info）"
            )
        case_id = raw.get("api_name") or str(api)

        body_type = raw.get("body_type")
        if body_type is not None:
            body_type = str(body_type).lower()
            if body_type not in _BODY_TYPES:
                raise ValueError(
                    f"用例 [{case_id}] 的 body_type='{body_type}' 非法，仅支持 {_BODY_TYPES}"
                )

        markers = _as_list(raw, "markers", case_id)
        unknown = [m for m in markers if m not in KNOWN_MARKERS]
        if unknown:
            raise ValueError(
                f"用例 [{case_id}] 的 markers 含未注册标记: {unknown}，"
                f"可用标记: {list(KNOWN_MARKERS)}（需与 pytest.ini 注册一致）"
            )

        assertions = _as_list(raw, "assert", case_id)
        _validate_assertions(assertions, case_id)

        depends_on = _as_list(raw, "depends_on", case_id)
        for dep in depends_on:
            if not isinstance(dep, str) or not dep:
                raise ValueError(f"用例 [{case_id}] 的 depends_on 必须是非空字符串列表")

        cleanup = [
            CleanupStep.from_dict(item, case_id, i)
            for i, item in enumerate(_as_list(raw, "cleanup", case_id))
        ]

        return cls(
            api=str(api),
            api_name=str(case_id),
            payload=raw.get("payload"),
            body_type=body_type,
            headers=_as_dict(raw, "headers", case_id),
            params=raw.get("params"),
            files=raw.get("files"),
            cookies=_as_dict(raw, "cookies", case_id),
            verify=raw.get("verify"),
            timeout=raw.get("timeout"),
            extract=_as_dict(raw, "extract", case_id),
            assertions=assertions,
            markers=markers,
            depends_on=depends_on,
            cleanup=cleanup,
            source=source,
        )

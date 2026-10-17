# core/case_extras.py - 跨用例能力端口（核心侧只定义接口，实现由 case_extras 积木注册）
#
# 跨用例能力：depends_on（前置依赖门控）、extract（响应字段提取为全局变量）、
# cleanup（用例收尾回收脏数据），以及配套的跨用例引用校验与并行护栏判断。
# 这些能力不是核心，统一走本端口：
#   - 嵌入积木（plugins/case_extras）时，积木加载即注册真实实现；
#   - 未嵌入时全部降级为空操作，框架照常运行（跨用例能力被"拔出"）。
#
# 注意：用例 YAML 中的 depends_on / extract / cleanup 字段结构仍由 core/case_model.py
# 解析与校验（数据契约属核心）；本端口只承载这些字段的"运行时语义"。
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, List


@dataclass
class CaseRuntime:
    """运行期上下文：把请求构造所需的辅助与依赖打包，供积木使用（避免反向 import 测试模块）"""

    registry: Any                              # 接口注册中心（EndpointRegistry）
    api_client: Any                            # 带鉴权的 BaseClient
    variables: Dict[str, Any]                  # 全局变量上下文
    config: Any                                # 环境配置
    parse_payload: Callable[[Any, str], Any]   # payload 归一化（query string → dict）
    resolve_timeout: Callable[[Any, Any], Any]  # 超时解析（用例级优先，否则环境配置）


class _NullExtras:
    """默认实现：什么也不做（跨用例能力未嵌入）"""

    def validate_cases(self, cases: list, registry: Any) -> None:
        """收集期：跨用例引用校验（depends_on/extract/cleanup）"""

    def precondition(self, case: Any) -> Optional[List[str]]:
        """运行期：返回未满足的前置用例 id 列表（空/None 表示无前置依赖）"""
        return None

    def record(self, case_id: str, outcome: str) -> None:
        """运行期：记录用例结论（passed / failed / skipped），供后续 depends_on 判断"""

    def after_response(self, case: Any, resp: Any, variables: Dict[str, Any]) -> None:
        """运行期：从响应中提取变量写入 variables（extract）"""

    def teardown(self, case: Any, runtime: CaseRuntime) -> None:
        """运行期：执行用例收尾步骤（cleanup）"""

    def has_cross_deps(self, case: Any) -> bool:
        """供并行护栏判断该用例是否含跨用例依赖（extract / depends_on）"""
        return False

    def prepare_variables(self, base: Dict[str, Any]) -> Dict[str, Any]:
        """运行期：把全局变量上下文交给本端口处理（默认按用例深拷贝隔离，--cases-shared 时共享）"""
        return base


_extras = _NullExtras()


def set_extras(impl) -> None:
    """积木加载时注册实现；传 None 表示卸载回空操作"""
    global _extras
    _extras = impl or _NullExtras()


def validate_cases(cases: list, registry: Any) -> None:
    """收集期：跨用例引用校验（由 test_regression 在加载用例后调用）"""
    _extras.validate_cases(cases, registry)


def precondition(case: Any) -> Optional[List[str]]:
    """运行期：前置依赖检查，返回未满足的前置 id 列表"""
    return _extras.precondition(case)


def record(case_id: str, outcome: str) -> None:
    """运行期：记录用例结论"""
    _extras.record(case_id, outcome)


def after_response(case: Any, resp: Any, variables: Dict[str, Any]) -> None:
    """运行期：提取响应变量"""
    _extras.after_response(case, resp, variables)


def teardown(case: Any, runtime: CaseRuntime) -> None:
    """运行期：执行收尾步骤"""
    _extras.teardown(case, runtime)


def has_cross_deps(case: Any) -> bool:
    """该用例是否含跨用例依赖"""
    return _extras.has_cross_deps(case)


def prepare_variables(base: Dict[str, Any]) -> Dict[str, Any]:
    """运行期：准备用例变量上下文"""
    return _extras.prepare_variables(base)

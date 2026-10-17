# plugins/case_extras/plugin.py - 跨用例能力积木的 pytest 插件实现
#
# 实现 core/case_extras.py 端口的全部运行时语义（depends_on / extract / cleanup）
# 与配套的收集期跨用例引用校验。
#
# 加载：pytest.ini addopts 中的 `-p plugins.case_extras.plugin`
import copy
import logging
from pathlib import Path

from core import case_extras, reporting
from core.assertions import get_by_path
from core.utils import replace_vars

logger = logging.getLogger(__name__)

# 用例数据目录（跨用例引用校验需扫描 YAML 文本中的 ${var} 引用）
DATA_DIR = Path(__file__).resolve().parents[2] / "test_data"

# 跨用例结果表：记录每个用例最终状态，供 depends_on 判断（同进程内有效）
_CASE_STATUS = {}

# 跨用例变量共享开关（--cases-shared），在 pytest_configure 时读取
# 默认关闭：每条用例持有独立变量上下文（隔离）；显式开启才跨用例共享
_SHARED = False


def _validate_registry_refs(case, registry) -> None:
    """校验用例 cleanup 步骤引用的接口逻辑名已在注册中心登记"""
    for step in case.cleanup:
        if step.api and step.api not in registry:
            raise ValueError(
                f"用例 [{case.id}]({case.source}) 的 cleanup 引用了未注册的接口 "
                f"'{step.api}'；可用接口: {registry.keys}"
            )


def _validate_cross_references(cases: list) -> None:
    """跨用例引用校验：把 depends_on/extract 的拼接问题在 pytest --collect-only 阶段报出来

    - depends_on 引用不存在的 id → WARNING（只告警不阻塞，便于定位拼写问题）
    - extract 的 key 没有任何用例通过 ${key} 引用 → INFO（提示，非阻塞）
    - 不抛错：现有项目里既有 zero-deps 用例，让 CI 不会因为这条校验红掉
    """
    all_ids = {c.id for c in cases}
    for c in cases:
        # depends_on 全量校验：每个引用必须命中已加载的 case id
        for dep in c.depends_on:
            if dep not in all_ids:
                logger.warning(
                    "[xref] 用例 [%s](%s) depends_on 引用 '%s' 在所有已加载用例中未找到；"
                    "可用 id: %s",
                    c.id, c.source, dep, sorted(all_ids)[:20],
                )
    # 用例 yaml 文本再扫一次 ${var} 出现（真正引用处）—— 简单粗暴的字符串扫描足以，
    # 因为所有用例都是 YAML 文本，${VAR} 形式变量必须闭合；保守起见只提示不阻塞
    yaml_texts = []
    for yaml_file in sorted(DATA_DIR.rglob("*.yaml")):
        try:
            yaml_texts.append(yaml_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[xref] 读取 %s 失败: %s", yaml_file, e)
    for c in cases:
        for var_name in (c.extract or {}).keys():
            if not any(f"${{{var_name}}}" in t for t in yaml_texts):
                logger.info(
                    "[xref] 提示：extract 变量 %s='%s' 在任何用例中未通过 ${%s} 引用，"
                    "可直接清理 extract 字段", c.id, var_name, var_name,
                )
    # 默认隔离下 extract 不跨用例生效，明确告警避免"写了却不生效"的静默问题
    if not _SHARED and any(c.extract for c in cases):
        logger.warning(
            "[xref] 检测到 extract 字段，但未启用 --cases-shared："
            "每条用例变量默认独立，extract 结果不会传给后续用例；"
            "需要跨用例传值时请加 --cases-shared"
        )


class CaseExtras:
    """跨用例能力实现：depends_on 门控 / extract 提取 / cleanup 收尾"""

    # ---- 收集期：跨用例引用校验 ----
    def validate_cases(self, cases: list, registry) -> None:
        for case in cases:
            _validate_registry_refs(case, registry)
        _validate_cross_references(cases)

    # ---- 运行期：前置依赖门控 ----
    def precondition(self, case):
        unmet = [d for d in case.depends_on if _CASE_STATUS.get(d) != "passed"]
        return unmet or None

    def record(self, case_id: str, outcome: str) -> None:
        _CASE_STATUS[case_id] = outcome

    # ---- 运行期：响应变量提取 ----
    def after_response(self, case, resp, variables) -> None:
        if not case.extract:
            return
        try:
            body = resp.json()
            for var_name, resp_path in case.extract.items():
                value = get_by_path(body, resp_path)
                variables[var_name] = value
                logger.info(f"提取变量: {var_name} = {value} (来自 {resp_path})")
                reporting.attach(
                    f"提取变量: {var_name} = {value} (来自 {resp_path})", "变量提取"
                )
        except Exception as e:
            logger.warning(f"[{case.api_name}] 变量提取失败 (extract={case.extract}): {e}")

    # ---- 运行期：用例收尾回收脏数据 ----
    def teardown(self, case, runtime) -> None:
        if not case.cleanup:
            return
        for idx, step in enumerate(case.cleanup, 1):
            try:
                if step.api:
                    endpoint = runtime.registry.get(step.api)
                    method, path, body_type = endpoint.method, endpoint.path, endpoint.body_type
                    headers = {**endpoint.headers, **step.headers}
                else:
                    method, path, body_type = step.method, step.path, step.body_type
                    headers = dict(step.headers)

                path = replace_vars(path, runtime.variables)
                kwargs = {"timeout": runtime.resolve_timeout(case, runtime.config)}
                if headers:
                    kwargs["headers"] = replace_vars(headers, runtime.variables)
                payload = replace_vars(
                    runtime.parse_payload(step.payload, body_type), runtime.variables
                )
                if method == "GET":
                    kwargs["params"] = payload
                elif body_type == "data":
                    kwargs["data"] = payload
                else:
                    kwargs["json"] = payload

                resp = runtime.api_client.request(method, path, **kwargs)
                logger.info("清理 %d/%d: %s %s -> HTTP %s",
                            idx, len(case.cleanup), method, path, resp.status_code)
                reporting.attach(
                    f"{method} {path} -> HTTP {resp.status_code}", f"cleanup[{idx}]"
                )
            except Exception as e:
                # 收尾失败不影响用例结论，但必须留痕，便于人工清理
                logger.warning("清理 %d/%d 失败（不影响用例结果）: %s",
                               idx, len(case.cleanup), e)

    # ---- 并行护栏：判断是否含跨用例依赖 ----
    def has_cross_deps(self, case) -> bool:
        return bool(case.extract or case.depends_on)

    # ---- 运行期：用例变量上下文（默认按用例隔离，--cases-shared 时才共享） ----
    def prepare_variables(self, base):
        return base if _SHARED else copy.deepcopy(base)


# ========== pytest 钩子：注册跨用例相关的命令行开关 ==========
def pytest_addoption(parser):
    # 变量共享开关：默认关闭 → 每条用例用 case_vars 深拷贝打底（互不污染）。
    # 开启后才共享同一个变量上下文，让 extract 提取值跨用例传递（会引入污染风险）
    parser.addoption(
        "--cases-shared",
        action="store_true",
        default=False,
        help="跨用例共享变量上下文（extract 跨用例传值）；默认每条用例变量独立",
    )


def pytest_configure(config):
    global _SHARED
    _SHARED = bool(config.getoption("--cases-shared"))


case_extras.set_extras(CaseExtras())

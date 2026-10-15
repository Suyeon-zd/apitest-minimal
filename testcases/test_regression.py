# testcases/test_regression.py - 数据驱动的接口回归测试
import logging
from pathlib import Path

import allure
import pytest
import yaml
from pytest_html import extras
from requests.exceptions import ConnectionError, Timeout
from urllib.parse import parse_qs

from api.registry import EndpointRegistry
from core.assertions import AssertionEngine, get_by_path
from core.case_model import TestCase
from core.utils import replace_vars

logger = logging.getLogger(__name__)

# ========== 1. 接口注册中心 ==========
# 接口 method/path 的唯一事实来源（api/endpoints.yaml），用例只写 api 逻辑名
REGISTRY = EndpointRegistry.load()

# 跨用例结果表：记录每个用例最终状态，供 depends_on 判断（同进程内有效）
_CASE_STATUS = {}

# ========== 2. 从 YAML 加载测试用例 ==========
# 递归扫描 test_data/ 下所有模块目录（如 asset/、system/），新增模块无需改代码
DATA_DIR = Path(__file__).resolve().parents[1] / "test_data"


def _validate_registry_refs(case: TestCase):
    """校验用例引用的接口逻辑名已在注册中心登记

    拼写错误在 pytest --collect-only 阶段就暴露，不需要等到真正执行。
    """
    if case.api not in REGISTRY:
        raise ValueError(
            f"用例 [{case.id}]({case.source}) 引用的接口 '{case.api}' 未注册，"
            f"请在 api/endpoints.yaml 中登记；可用接口: {REGISTRY.keys}"
        )
    for step in case.cleanup:
        if step.api and step.api not in REGISTRY:
            raise ValueError(
                f"用例 [{case.id}]({case.source}) 的 cleanup 引用了未注册的接口 "
                f"'{step.api}'；可用接口: {REGISTRY.keys}"
            )


def _load_cases() -> list:
    """扫描并校验全部用例 YAML，转成 TestCase 模型"""
    if not DATA_DIR.exists():
        print("⚠️ 警告: 未找到 test_data 目录！")
        return []

    cases = []
    seen_ids = {}
    # sorted 保证跨文件收集顺序确定（文件路径排序），跨用例变量依赖（extract → ${var}）才可靠
    for yaml_file in sorted(DATA_DIR.rglob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            raw_cases = yaml.safe_load(f)
        if not raw_cases:
            continue
        for raw in raw_cases:
            case = TestCase.from_dict(raw, source=yaml_file.name)
            # 用例 id 同时是 pytest 用例名与 depends_on 的引用键，必须全局唯一
            if case.id in seen_ids:
                raise ValueError(
                    f"用例 id 重复: '{case.id}'，同时出现在 "
                    f"{seen_ids[case.id]} 与 {yaml_file.name}；depends_on 无法区分引用目标"
                )
            seen_ids[case.id] = yaml_file.name
            _validate_registry_refs(case)
            cases.append(case)

    # 跨 yaml 引用校验（增量、不破兼容）：只发 WARNING，不抛错
    # - depends_on 必须命中已注册的 case id
    # - extract.* 没人引用时发出轻微提示（CI 累积垃圾最容易）
    _validate_cross_references(cases)
    return cases


def _validate_cross_references(cases: list) -> None:
    """跨用例引用校验：把 depends_on/extract 的拼接问题在 pytest --collect-only 阶段报出来

    - depends_on 引用不存在的 id → WARNING（强不一致，宁可抛错也方便定位）
    - extract 的 key 没有任何用例通过 ${key} 引用 → INFO（提示，非阻塞）
    - 不抛错：现有项目里既有 zero-deps 用例，让 CI 不会因为这条校验红掉
    """
    all_ids = {c.id for c in cases}
    referenced = set()    # 全局被 ${var} 引用过的变量名
    for c in cases:
        # depends_on 全量校验：每个引用必须命中已加载的 case id
        for dep in c.depends_on:
            if dep not in all_ids:
                # 不存在时强告警：让 pytest --collect-only 阶段就能定位
                # （避免执行时才被 depends_on 触发导致整条链跳过的"无下文"）
                logger.warning(
                    "[xref] 用例 [%s](%s) depends_on 引用 '%s' 在所有已加载用例中未找到；"
                    "可用 id: %s",
                    c.id, c.source, dep, sorted(all_ids)[:20],
                )
        # extract 用作「别人读我的变量」时收集全部被引用的 var_name
        for var_name in (c.extract or {}).keys():
            referenced.add(var_name)
    # 用例 yaml 文本再次扫一次 ${var} 出现（真正引用处）—— 简单粗暴的字符串扫描足以
    # 因为所有用例都是 YAML 文本，${VAR} 形式变量必须闭合；非业务变量如 ${PID}（shell）
    # 与业务变量命名约定不同；保守起见只警告不阻塞
    yaml_texts = []
    for yaml_file in sorted(DATA_DIR.rglob("*.yaml")):
        try:
            yaml_texts.append(yaml_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[xref] 读取 %s 失败: %s", yaml_file, e)
    unused_extract = []
    for c in cases:
        for var_name in (c.extract or {}).keys():
            matched = any(f"${{{var_name}}}" in t for t in yaml_texts)
            if not matched:
                unused_extract.append((c.id, var_name))
    if unused_extract:
        # 不阻塞：以前用例多、extract 漏删会造成噪音；只 info 让清单可见
        for cid, vn in unused_extract:
            logger.info(
                "[xref] 提示：extract 变量 %s='%s' 在任何用例中未通过 ${%s} 引用，"
                "可直接清理 extract 字段", cid, vn, vn,
            )


API_TEST_CASES = _load_cases()


def pytest_collection_modifyitems(config, items):
    """并行安全护栏：xdist 多进程下 worker 之间不共享变量与用例状态

    `-n` 由 xdist 提供，未安装 xdist 时 getoption 取默认值 None，本钩子自动失效。
    依赖用例（extract / depends_on）在并行下会"静默跑出错结果"，因此收集期直接拦截。
    """
    if not config.getoption("numprocesses", None):
        return
    dependent = [c.id for c in API_TEST_CASES if c.extract or c.depends_on]
    if not dependent:
        return
    dist = config.getoption("dist", "no")
    if dist == "loadfile":
        # 同文件同进程：仅当依赖关系不跨文件时才安全（无法在此精确判定，统一提示人工确认）
        return
    raise RuntimeError(
        f"检测到 xdist 并行模式（--dist {dist}），但以下用例存在跨用例依赖"
        f"（extract/depends_on）: {dependent}。"
        f"请去掉 -n 参数串行执行；确认依赖均在同一 YAML 文件内时，"
        f"可改用 -n auto --dist loadfile（保证同文件同进程）。"
    )


def parse_payload(payload, body_type="json"):
    """把 YAML 里的 payload 统一转成请求参数：query string → dict，空值 → None

    - body_type 为 data 且 payload 是字符串时原样透传（原始文本，如加密串），
      不做 query string 解析，否则加密串会被 parse_qs 破坏成空 dict。
    - keep_blank_values=True：保留空值参数（如 "a=&b=1" 中的 a）。
    """
    if not payload:
        return None
    if isinstance(payload, str):
        if body_type == "data":
            return payload
        parsed = parse_qs(payload, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}
    return payload


# ========== 3. 数据驱动测试 ==========
def _build_params():
    """把用例模型转为 pytest.param：id 取 api_name，报告中直接显示用例名"""
    params = []
    for case in API_TEST_CASES:
        marks = [getattr(pytest.mark, m) for m in case.markers]
        params.append(pytest.param(case, id=case.id, marks=marks))
    return params


def _resolve_timeout(case: TestCase, config):
    """解析超时：用例级 timeout 优先（秒数或 [connect, read]），否则用环境配置"""
    if case.timeout is None:
        return tuple(config["timeout"])
    if isinstance(case.timeout, (list, tuple)):
        return tuple(case.timeout)
    return float(case.timeout)


def _build_request_kwargs(case: TestCase, endpoint, config, variables):
    """按用例配置组装 requests 参数（含 ${var} 替换）"""
    # body_type 在解析 payload 前确定：data + 原始文本 payload 需要跳过 query 解析
    body_type = str(case.body_type or endpoint.body_type).lower()
    payload = replace_vars(parse_payload(case.payload, body_type), variables)

    kwargs = {"timeout": _resolve_timeout(case, config)}

    # 接口专属头（注册中心）+ 用例级 headers 覆盖，最终与 session 上的 Authorization 合并
    headers = {**endpoint.headers, **case.headers}
    if headers:
        kwargs["headers"] = replace_vars(headers, variables)
    if case.cookies:
        kwargs["cookies"] = replace_vars(case.cookies, variables)
    if case.verify is not None:
        kwargs["verify"] = case.verify

    method = endpoint.method
    if method == "GET":
        # GET：payload 作为 query 参数
        kwargs["params"] = payload
    elif case.files is not None:
        # multipart 上传：files 走文件，payload 作为附带表单字段
        kwargs["files"] = case.files
        if payload:
            kwargs["data"] = payload
    else:
        # 非 GET：body_type 决定请求体发送方式（用例级可覆盖注册中心配置）
        #   json（默认）→ application/json 请求体
        #   data        → 原始文本（如加密串 text/plain）或表单（dict，urlencoded）
        if body_type == "data":
            kwargs["data"] = payload
        else:
            kwargs["json"] = payload

    # 额外 query 参数（与 body 并存；GET 时叠加到 payload 上）
    if case.params:
        extra = replace_vars(case.params, variables)
        existed = kwargs.get("params")
        kwargs["params"] = {**(existed if isinstance(existed, dict) else {}), **extra}
    return kwargs


def _run_cleanup(api_client, case: TestCase, variables, config):
    """执行用例收尾步骤回收数据；失败仅告警，不改变用例结论"""
    for idx, step in enumerate(case.cleanup, 1):
        try:
            if step.api:
                endpoint = REGISTRY.get(step.api)
                method, path, body_type = endpoint.method, endpoint.path, endpoint.body_type
                headers = {**endpoint.headers, **step.headers}
            else:
                method, path, body_type = step.method, step.path, step.body_type
                headers = dict(step.headers)

            path = replace_vars(path, variables)
            kwargs = {"timeout": _resolve_timeout(case, config)}
            if headers:
                kwargs["headers"] = replace_vars(headers, variables)
            payload = replace_vars(parse_payload(step.payload, body_type), variables)
            if method == "GET":
                kwargs["params"] = payload
            elif body_type == "data":
                kwargs["data"] = payload
            else:
                kwargs["json"] = payload

            resp = api_client.request(method, path, **kwargs)
            logger.info("清理 %d/%d: %s %s -> HTTP %s",
                        idx, len(case.cleanup), method, path, resp.status_code)
            allure.attach(
                f"{method} {path} -> HTTP {resp.status_code}",
                name=f"cleanup[{idx}]", attachment_type=allure.attachment_type.TEXT,
            )
        except Exception as e:
            # 收尾失败不影响用例结论，但必须留痕，便于人工清理
            logger.warning("清理 %d/%d 失败（不影响用例结果）: %s", idx, len(case.cleanup), e)


def _run_case(api_client, case: TestCase, config, variables, extra):
    """用例主体：发请求 → 提取变量 → 执行断言（原逻辑保持不变）"""
    endpoint = REGISTRY.get(case.api)
    api_name = case.api_name
    path = replace_vars(endpoint.path, variables)
    request_kwargs = _build_request_kwargs(case, endpoint, config, variables)

    # ---- 发送请求 ----
    with allure.step(f"接口请求: {api_name}"):
        try:
            resp = api_client.request(endpoint.method, path, **request_kwargs)
        except ConnectionError:
            pytest.fail(f"[{api_name}] 网络异常: 接口不可达 (Path: {path})")
        except Timeout:
            pytest.fail(f"[{api_name}] 请求超时 (Path: {path})")

    # ---- 变量提取：把响应中的字段提取到全局变量上下文，供后续用例 ${var} 引用 ----
    if case.extract:
        try:
            body = resp.json()
            for var_name, resp_path in case.extract.items():
                value = get_by_path(body, resp_path)
                variables[var_name] = value
                logger.info(f"提取变量: {var_name} = {value} (来自 {resp_path})")
                allure.attach(
                    f"提取变量: {var_name} = {value} (来自 {resp_path})",
                    name="变量提取", attachment_type=allure.attachment_type.TEXT
                )
        except Exception as e:
            logger.warning(f"[{api_name}] 变量提取失败 (extract={case.extract}): {e}")

    # ---- 执行断言（断言块非空由 TestCase 收集期保证） ----
    assert_list = case.assertions
    engine = AssertionEngine()
    with allure.step(f"执行断言: 共 {len(assert_list)} 条"):
        all_passed, results = engine.run(resp, assert_list)

    # ---- 断言结果报告 ----
    report_text = "\n".join(f"{i}. {r}" for i, r in enumerate(results, 1))
    allure.attach(report_text, name="断言结果明细", attachment_type=allure.attachment_type.TEXT)
    extra.append(extras.text(report_text, name="断言结果明细"))

    # ---- 失败时抛出聚合错误信息 ----
    if not all_passed:
        failed = [r for r in results if not r.passed]
        fail_msg = f"[{api_name}] {len(failed)}/{len(results)} 条断言失败:\n" + "\n".join(
            f"  - {r.message}" for r in failed
        )
        pytest.fail(fail_msg)


@pytest.mark.parametrize("case", _build_params())
def test_api_regression(api_client, case, config, variables, extra):
    # ---- 前置依赖检查：依赖用例未通过（含尚未执行）则跳过，避免级联误报 ----
    unmet = [d for d in case.depends_on if _CASE_STATUS.get(d) != "passed"]
    if unmet:
        _CASE_STATUS[case.id] = "skipped"
        pytest.skip(f"前置用例未通过: {unmet}")

    try:
        _run_case(api_client, case, config, variables, extra)
    except BaseException:
        _CASE_STATUS[case.id] = "failed"
        raise
    else:
        _CASE_STATUS[case.id] = "passed"
    finally:
        # 无论成败都回收本用例产生的脏数据
        if case.cleanup:
            _run_cleanup(api_client, case, variables, config)

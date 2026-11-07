# testcases/test_regression.py - 数据驱动的接口回归测试
from pathlib import Path

import pytest
from requests.exceptions import ConnectionError, RequestException, Timeout
from urllib.parse import parse_qs

from api.registry import EndpointRegistry
from core import case_extras, reporting
from core.assertions import AssertionEngine
from core.case_loader import load_cases
from core.case_model import TestCase
from core.utils import replace_vars

# ========== 1. 接口注册中心 ==========
# 接口 method/path 的唯一事实来源（api/endpoints.yaml），用例只写 api 逻辑名
REGISTRY = EndpointRegistry.load()

# ========== 2. 从 YAML 加载测试用例 ==========
# 递归扫描 test_data/ 下所有模块目录（如 asset/、system/），新增模块无需改代码
# 加载与校验逻辑在 core/case_loader.py（离线自检 selftest/test_assets.py 复用同一入口）
DATA_DIR = Path(__file__).resolve().parents[1] / "test_data"
API_TEST_CASES = load_cases(DATA_DIR, REGISTRY)


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
        timeout = config.get("timeout")
        if timeout is None:
            raise ValueError(
                "环境配置缺少 timeout（config/<env>.yaml），无法确定请求超时；"
                "请补充类似 timeout: [5, 10]"
            )
        return tuple(timeout)
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

    method = endpoint.method
    if method == "GET":
        # GET：payload 作为 query 参数
        kwargs["params"] = payload
    elif body_type == "data":
        # 非 GET：原始文本（如加密串 text/plain）或表单（dict，urlencoded）
        kwargs["data"] = payload
    else:
        # 非 GET 默认：application/json 请求体
        kwargs["json"] = payload
    return kwargs


def _run_case(api_client, case: TestCase, config, variables):
    """用例主体：发请求 → 提取变量 → 执行断言（原逻辑保持不变）"""
    endpoint = REGISTRY.get(case.api)
    api_name = case.api_name
    path = replace_vars(endpoint.path, variables)
    request_kwargs = _build_request_kwargs(case, endpoint, config, variables)

    # ---- 发送请求 ----
    with reporting.step(f"接口请求: {api_name}"):
        try:
            resp = api_client.request(endpoint.method, path, **request_kwargs)
        except ConnectionError:
            pytest.fail(f"[{api_name}] 网络异常: 接口不可达 (Path: {path})")
        except Timeout:
            pytest.fail(f"[{api_name}] 请求超时 (Path: {path})")
        except RequestException as e:
            # 其余 requests 异常（SSL/代理/重定向等）统一归为失败，避免逃逸成 ERROR
            pytest.fail(f"[{api_name}] 请求异常({type(e).__name__}): {e} (Path: {path})")

    # ---- 变量提取：跨用例能力（extract）；由跨用例积木实现，拔出后为空操作 ----
    case_extras.after_response(case, resp, variables)

    # ---- 执行断言（断言块非空由 TestCase 收集期保证） ----
    assert_list = case.assertions
    engine = AssertionEngine()
    with reporting.step(f"执行断言: 共 {len(assert_list)} 条"):
        all_passed, results = engine.run(resp, assert_list)

    # ---- 断言结果报告 ----
    report_text = "\n".join(f"{i}. {r}" for i, r in enumerate(results, 1))
    reporting.attach(report_text, "断言结果明细")
    reporting.extra_text(report_text, "断言结果明细")

    # ---- 失败时抛出聚合错误信息 ----
    if not all_passed:
        failed = [r for r in results if not r.passed]
        fail_msg = f"[{api_name}] {len(failed)}/{len(results)} 条断言失败:\n" + "\n".join(
            f"  - {r.message}" for r in failed
        )
        pytest.fail(fail_msg)


@pytest.mark.parametrize("case", _build_params())
def test_api_regression(api_client, case, config, variables):
    # ---- 前置依赖检查：跨用例能力（depends_on）；拔出积木后为无操作 ----
    unmet = case_extras.precondition(case)
    if unmet:
        case_extras.record(case.id, "skipped")
        pytest.skip(f"前置用例未通过: {unmet}")

    runtime = case_extras.CaseRuntime(
        registry=REGISTRY,
        api_client=api_client,
        variables=variables,
        config=config,
        parse_payload=parse_payload,
        resolve_timeout=_resolve_timeout,
    )
    try:
        _run_case(api_client, case, config, variables)
    except BaseException:
        case_extras.record(case.id, "failed")
        raise
    else:
        case_extras.record(case.id, "passed")
    finally:
        # 无论成败都回收本用例产生的脏数据（cleanup；拔出积木后为无操作）
        case_extras.teardown(case, runtime)

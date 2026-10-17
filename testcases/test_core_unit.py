# testcases/test_core_unit.py - 框架核心逻辑单元测试（纯本地：不联网、不连库）
#
# 覆盖 core/ 下的断言引擎、路径取值、变量替换、配置插值、接口注册中心、
# 请求客户端（脱敏/鉴权日志）以及用例模型校验。
# 重试退避 / 401 重登属「HTTP 自愈」积木（plugins/http_heal），其回归见 test_http_heal_unit.py。
# 这些是框架自身的地基，必须有不依赖被测环境的回归保护。
import json
import os
import re
from datetime import timedelta
from pathlib import Path

import pytest
import requests

from core import assertions, base_client
from core.assertions import AssertionEngine, compare, get_by_path
from core.base_client import BaseClient, _classify, _mask, _mask_body
from core.case_model import STRONG_ASSERT_TYPES, TestCase
from core.config_loader import expand_env_vars
from core.constants import KNOWN_MARKERS
from core.utils import replace_vars
from api.registry import EndpointRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ========== 测试替身 ==========

class FakeResponse:
    """最小化的 requests.Response 替身，用于离线断言引擎测试"""

    def __init__(self, status_code=200, body=None, text=None,
                 headers=None, elapsed=0.12, json_error=False):
        self.status_code = status_code
        self._body = {} if body is None else body
        self._json_error = json_error
        self.text = text if text is not None else json.dumps(self._body, ensure_ascii=False)
        self.headers = headers or {"Content-Type": "application/json"}
        self.elapsed = timedelta(seconds=elapsed)

    def json(self):
        if self._json_error:
            raise ValueError("响应体不是 JSON")
        return self._body


class FakeSession:
    """替换 requests.Session：按脚本依次返回响应或抛异常"""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.responses = []
        self.errors = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if self.errors:
            raise self.errors.pop(0)
        return self.responses.pop(0) if self.responses else FakeResponse()


class FakeAuth:
    """AuthManager 替身：只关心"是否被要求重新登录" """

    def __init__(self):
        self.config = {"clientid": "cid-001"}
        self.login_count = 1
        self.token = "token-1"

    def get_token(self):
        return self.token

    def login(self):
        self.login_count += 1
        self.token = f"token-{self.login_count}"
        return self.token


# ========== 1. 路径取值 ==========

class TestGetByPath:

    BODY = {"a": 1, "data": [{"name": "first"}, {"name": "second"}]}

    @pytest.mark.parametrize("path, expected", [
        ("a", 1),
        ("data.0.name", "first"),
        ("data[0].name", "first"),          # 方括号写法需与点号写法等价
        ("data.1.name", "second"),
        ("data.9.name", None),              # 索引越界 → None
        ("data.0.missing", None),           # 键不存在 → None
        ("a.b.c", None),                    # 中途是标量 → None
    ])
    def test_get_by_path(self, path, expected):
        assert get_by_path(self.BODY, path) == expected, f"路径 {path} 取值不符合预期"

    def test_empty_path_returns_whole_body(self):
        assert get_by_path(self.BODY, "") == self.BODY


# ========== 2. 通用比较器 ==========

class TestCompare:

    @pytest.mark.parametrize("actual, expected, operator, want", [
        (1, 1, "eq", True),
        (1, 2, "ne", True),
        (3, 2, "gt", True),
        (1, 2, "lt", True),
        (2, 2, "gte", True),
        (2, 2, "lte", True),
        ("abc", "b", "contains", True),
        (1, [1, 2], "in", True),
        (9, 1, "eq", False),
    ])
    def test_compare_operators(self, actual, expected, operator, want):
        passed, err = compare(actual, expected, operator)
        assert passed is want and err is None, f"{operator} 比较结果不符: {actual} vs {expected}"

    def test_unknown_operator_returns_error(self):
        passed, err = compare(1, 1, "不存在的比较符")
        assert passed is False, "未知比较符应判定失败而不是静默通过"
        assert "不支持" in err

    def test_incompatible_types_reports_error(self):
        # 类型不可比时应返回明确错误信息，而不是抛出异常中断整个用例
        passed, err = compare("abc", 1, "gt")
        assert passed is False
        assert "比较异常" in err


# ========== 3. 断言引擎 ==========

class TestAssertionEngine:

    def setup_method(self):
        self.engine = AssertionEngine()
        self.body = {
            "code": 200,
            "msg": "操作成功",
            "data": [{"deptId": 100, "leaf": True}, {"deptId": 101, "leaf": False}],
        }
        self.resp = FakeResponse(body=self.body)

    def _run_one(self, rule, resp=None):
        passed, results = self.engine.run(resp or self.resp, [rule])
        return passed, results[0]

    def test_status_code(self):
        passed, r = self._run_one({"type": "status_code", "expected": 200})
        assert passed, r.message
        passed, r = self._run_one({"type": "status_code", "expected": 500})
        assert not passed, "状态码不符必须判失败"

    def test_header(self):
        passed, r = self._run_one(
            {"type": "header", "field": "Content-Type",
             "operator": "contains", "expected": "json"}
        )
        assert passed, r.message

    def test_json_path(self):
        passed, r = self._run_one({"type": "json_path", "path": "code", "expected": 200})
        assert passed, r.message
        passed, r = self._run_one({"type": "json_path", "path": "msg", "expected": "失败"})
        assert not passed
        assert "预期 失败" in r.message, "失败信息必须包含预期值，便于定位"

    def test_field_type_excludes_bool_for_int(self):
        # bool 是 int 的子类，data.0.leaf 为 True 时不应被判成 int
        passed, r = self._run_one(
            {"type": "field_type", "path": "data.0.leaf", "expected": "int"}
        )
        assert not passed, "True 不应被判为 int 类型"
        passed, _ = self._run_one(
            {"type": "field_type", "path": "data.0.deptId", "expected": "int"}
        )
        assert passed

    def test_exists(self):
        passed, r = self._run_one({"type": "exists", "path": "data.0.deptId"})
        assert passed, r.message
        passed, r = self._run_one({"type": "exists", "path": "data.0.notExist"})
        assert not passed

    def test_list_len(self):
        passed, r = self._run_one(
            {"type": "list_len", "path": "data", "operator": "eq", "expected": 2}
        )
        assert passed, r.message
        passed, r = self._run_one(
            {"type": "list_len", "path": "code", "operator": "eq", "expected": 1}
        )
        assert not passed and "不是列表" in r.message

    def test_contains(self):
        passed, r = self._run_one({"type": "contains", "path": "msg", "expected": "成功"})
        assert passed, r.message

    def test_each(self):
        passed, r = self._run_one(
            {"type": "each", "path": "data", "field": "leaf", "operator": "eq", "expected": True}
        )
        assert not passed and "1/2" in r.message, "批量断言需报出不满足的元素个数"
        passed, r = self._run_one(
            {"type": "each", "path": "data", "field": "leaf",
             "operator": "in", "expected": [True, False]}
        )
        assert passed, r.message

    def test_each_empty_list_passes(self):
        resp = FakeResponse(body={"data": []})
        passed, r = self._run_one(
            {"type": "each", "path": "data", "field": "x", "expected": 1}, resp=resp
        )
        assert passed, "空列表没有可校验元素，应视为通过"

    def test_response_time(self):
        passed, r = self._run_one({"type": "response_time", "expected": 1, "operator": "lte"})
        assert passed, r.message
        passed, r = self._run_one({"type": "response_time", "expected": 0.01, "operator": "lte"})
        assert not passed, "超时用例应判失败（SLA 拦截）"

    def test_response_time_without_elapsed(self):
        passed, r = self._run_one({"type": "response_time", "expected": 1}, resp=object())
        assert not passed and "elapsed" in r.message

    def test_schema(self, monkeypatch, tmp_path):
        # 用临时目录里的真 Schema 验证通过/失败两条路径
        monkeypatch.setattr(assertions, "SCHEMA_DIR", tmp_path)
        (tmp_path / "s.json").write_text(json.dumps({
            "type": "object",
            "required": ["code"],
            "properties": {"code": {"type": "integer"}},
        }), encoding="utf-8")

        passed, r = self._run_one({"type": "schema", "schema_file": "s.json"},
                                  resp=FakeResponse(body={"code": 200}))
        assert passed, r.message

        passed, r = self._run_one({"type": "schema", "schema_file": "s.json"},
                                  resp=FakeResponse(body={"code": "不是数字"}))
        assert not passed, "响应结构不符合 Schema 时必须判失败"
        assert "Schema 校验失败" in r.message

    def test_schema_snapshot_file_is_rejected(self, monkeypatch, tmp_path):
        # 响应快照被误当 Schema 用时 jsonschema 会永远通过，必须显式拦截
        monkeypatch.setattr(assertions, "SCHEMA_DIR", tmp_path)
        (tmp_path / "snapshot.json").write_text(
            json.dumps({"code": 200, "msg": "操作成功"}), encoding="utf-8"
        )
        passed, r = self._run_one({"type": "schema", "schema_file": "snapshot.json"})
        assert not passed and "不是合法的 JSON Schema" in r.message

    def test_schema_file_missing(self):
        passed, r = self._run_one({"type": "schema", "schema_file": "不存在的.json"})
        assert not passed and "不存在" in r.message

    def test_unsupported_type(self):
        passed, r = self._run_one({"type": "no_such_type"})
        assert not passed and "不支持" in r.message

    def test_non_json_response(self):
        passed, r = self._run_one(
            {"type": "json_path", "path": "code", "expected": 200},
            resp=FakeResponse(text="<html>502</html>", json_error=True),
        )
        assert not passed and "不是合法 JSON" in r.message

    def test_empty_assert_list_passes(self):
        passed, results = self.engine.run(self.resp, [])
        assert passed and results == [], "无断言时应返回通过且不产生明细"

    def test_multiple_assertions_all_must_pass(self):
        passed, results = self.engine.run(self.resp, [
            {"type": "status_code", "expected": 200},
            {"type": "json_path", "path": "code", "expected": 500},
        ])
        assert not passed and len(results) == 2


# ========== 4. 运行时变量替换 ==========

class TestReplaceVars:

    def test_basic_types(self):
        variables = {"ID": 100, "NAME": "资产"}
        assert replace_vars("id=${ID}&n=${NAME}", variables) == "id=100&n=资产"
        assert replace_vars({"a": "${ID}"}, variables) == {"a": "100"}
        assert replace_vars(["${ID}", 1], variables) == ["100", 1]
        assert replace_vars(None, variables) is None

    def test_undefined_var_kept_as_is(self):
        assert replace_vars("id=${UNKNOWN}", {}) == "id=${UNKNOWN}", (
            "未定义变量应原样保留，便于从日志看出漏了哪个变量"
        )


# ========== 5. 配置环境变量插值 ==========

class TestExpandEnvVars:

    VARS = ("APITEST_UNIT_VAR", "APITEST_UNIT_MISSING")

    def setup_method(self):
        # 清掉可能被 .env 注入的同名变量，保证用例结果与本地环境无关
        for name in self.VARS:
            os.environ.pop(name, None)

    def test_env_hit(self, monkeypatch):
        monkeypatch.setenv("APITEST_UNIT_VAR", "real")
        assert expand_env_vars("${APITEST_UNIT_VAR}") == "real"
        assert expand_env_vars({"pwd": "${APITEST_UNIT_VAR}"}) == {"pwd": "real"}

    def test_default_value(self):
        assert expand_env_vars("${APITEST_UNIT_MISSING:-fallback}") == "fallback"

    def test_env_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("APITEST_UNIT_VAR", "real")
        assert expand_env_vars("${APITEST_UNIT_VAR:-fallback}") == "real"

    def test_missing_without_default_becomes_empty(self):
        assert expand_env_vars("${APITEST_UNIT_MISSING}") == ""

    def test_nested_list(self, monkeypatch):
        monkeypatch.setenv("APITEST_UNIT_VAR", "v")
        assert expand_env_vars({"a": ["${APITEST_UNIT_VAR}", 2]}) == {"a": ["v", 2]}


# ========== 6. 接口注册中心 ==========

class TestEndpointRegistry:

    def test_loads_and_normalizes(self, monkeypatch):
        monkeypatch.setenv("APITEST_UNIT_VAR", "hdr")
        registry = EndpointRegistry({
            "user": {"get_info": {"method": "get", "path": "/u/info",
                                  "headers": {"X-T": "${APITEST_UNIT_VAR}"}}},
        })
        assert "user.get_info" in registry
        endpoint = registry.get("user.get_info")
        assert endpoint.method == "GET", "method 应统一转大写"
        assert endpoint.body_type == "json", "body_type 默认应为 json"
        assert endpoint.headers == {"X-T": "hdr"}, "headers 应完成环境变量插值"
        assert registry.keys == ["user.get_info"]

    def test_path_var_is_not_env_expanded(self, monkeypatch):
        # path 里的 ${xxx} 是运行时业务变量，不能被环境变量插值误替换
        monkeypatch.setenv("RES_ID", "should-not-appear")
        registry = EndpointRegistry({
            "asset": {"detail": {"method": "GET", "path": "/asset/${RES_ID}"}}
        })
        assert registry.get("asset.detail").path == "/asset/${RES_ID}"

    def test_missing_method_or_path(self):
        with pytest.raises(ValueError, match="method 与 path"):
            EndpointRegistry({"user": {"bad": {"method": "GET"}}})

    def test_illegal_body_type(self):
        with pytest.raises(ValueError, match="body_type"):
            EndpointRegistry({"user": {"bad": {"method": "GET", "path": "/x",
                                               "body_type": "xml"}}})

    def test_get_unknown_key(self):
        registry = EndpointRegistry({"user": {"a": {"method": "GET", "path": "/a"}}})
        with pytest.raises(KeyError, match="未在注册中心登记"):
            registry.get("user.not_exist")

    def test_real_registry_file_loads(self):
        # 项目自带的 endpoints.yaml 必须可正常解析
        registry = EndpointRegistry.load()
        assert registry.keys, "endpoints.yaml 至少应登记一个接口"
        for key in registry.keys:
            assert registry.get(key).method in ("GET", "POST", "PUT", "DELETE", "PATCH"), \
                f"{key} 的 method 非法"


# ========== 7. 请求客户端：脱敏 ==========

class TestBaseClientMasking:

    @pytest.mark.parametrize("raw, want", [
        (None, None),
        ("", ""),
        ("short", "***"),                       # 短值整体隐藏，避免被完整还原
        ("abcdefghijklmnop", "abcd***mnop"),
    ])
    def test_mask_value(self, raw, want):
        assert _mask(raw) == want

    def test_mask_body_hides_token(self):
        raw = ('{"code":200,"data":{"access_token":"eyJhbGciOiJIUzI1NiJ9.abc.def",'
               '"refreshToken":"refresh-token-value"}}')
        masked = _mask_body(raw)
        assert "eyJhbGciOiJIUzI1NiJ9" not in masked, "JWT 不允许出现在日志文本里"
        assert "***" in masked
        assert '"code":200' in masked, "非敏感字段应保留，便于排查"

    def test_mask_body_truncates_long_text(self):
        masked = _mask_body("x" * 5000)
        assert "已截断" in masked

    def test_classify_exception_categories(self):
        assert _classify(requests.exceptions.ConnectTimeout()) == "超时-连接建立"
        assert _classify(requests.exceptions.ReadTimeout()) == "超时-读取响应"
        assert _classify(requests.exceptions.Timeout()) == "超时"
        assert _classify(requests.exceptions.SSLError()) == "SSL 握手失败"
        assert _classify(requests.exceptions.ConnectionError()) == "连接错误"
        assert _classify(ValueError()) == "未知异常"

    def test_request_log_hides_authorization(self, monkeypatch, caplog):
        session = FakeSession()
        session.responses = [FakeResponse()]
        monkeypatch.setattr(base_client.requests, "Session", lambda: session)
        client = BaseClient("http://unit-test", auth=FakeAuth())

        with caplog.at_level("INFO"):
            client.request("GET", "/x")

        log_text = "\n".join(r.getMessage() for r in caplog.records if isinstance(r.getMessage(), str))
        assert "token-1" not in log_text, "Authorization 中的 token 不允许明文进日志"


# ========== 8. 用例模型校验 ==========

class TestCaseModel:

    BASE = {
        "api": "system.org_list",
        "api_name": "组织树-示例",
        "assert": [{"type": "json_path", "path": "code", "expected": 200}],
    }

    def _case(self, **overrides):
        raw = {**self.BASE, **overrides}
        return TestCase.from_dict(raw, source="unit.yaml")

    def test_valid_case(self):
        case = self._case(markers=["smoke", "p0"], payload="parentId=0&type=org")
        assert case.id == "组织树-示例"
        assert case.api == "system.org_list"
        assert case.markers == ["smoke", "p0"]
        assert case.payload == "parentId=0&type=org"

    def test_missing_api(self):
        with pytest.raises(ValueError, match="缺少 api 字段"):
            TestCase.from_dict({"api_name": "x", "assert": self.BASE["assert"]})

    def test_unknown_marker(self):
        with pytest.raises(ValueError, match="未注册标记"):
            self._case(markers=["smoke", "nightly"])

    def test_assert_block_required(self):
        with pytest.raises(ValueError, match="缺少 assert"):
            TestCase.from_dict(
                {"api": "a.b", "api_name": "无断言用例"}, source="unit.yaml"
            )

    def test_weak_assertion_rejected(self):
        with pytest.raises(ValueError, match="弱断言"):
            TestCase.from_dict(
                {"api": "a.b", "api_name": "只有状态码",
                 "assert": [{"type": "status_code", "expected": 200}]},
                source="unit.yaml",
            )

    def test_assertion_without_type(self):
        with pytest.raises(ValueError, match="缺少 type"):
            TestCase.from_dict(
                {"api": "a.b", "api_name": "坏断言", "assert": [{"path": "code"}]},
                source="unit.yaml",
            )

    def test_illegal_body_type(self):
        with pytest.raises(ValueError, match="body_type"):
            self._case(body_type="xml")

    def test_depends_on_must_be_str_list(self):
        with pytest.raises(ValueError, match="depends_on"):
            self._case(depends_on=[123])

    def test_cleanup_requires_api_or_method_path(self):
        with pytest.raises(ValueError, match="cleanup"):
            self._case(cleanup=[{"payload": "id=1"}])

    def test_cleanup_parsed(self):
        case = self._case(cleanup=[
            {"api": "asset.delete", "payload": "id=${NEW_ID}"},
            {"method": "delete", "path": "/asset/${NEW_ID}"},
        ])
        assert case.cleanup[0].api == "asset.delete"
        assert case.cleanup[0].ignore_failure is True
        assert case.cleanup[1].method == "DELETE", "method 应统一转大写"

    def test_strong_assert_types_include_schema(self):
        assert "schema" in STRONG_ASSERT_TYPES and "status_code" not in STRONG_ASSERT_TYPES


# ========== 10. test_data/schemas/ 契约文件有效性 ==========

class TestSchemaFiles:

    def test_all_schema_files_are_real_schemas(self):
        """test_data/schemas/ 下每个文件都必须是真 JSON Schema

        曾出现：org_list.json 实际是响应快照，被当作 Schema 使用时 jsonschema
        视为空 schema 永远通过 —— 断言形同虚设。
        """
        files = sorted(assertions.SCHEMA_DIR.glob("*.json"))
        assert files, "test_data/schemas/ 目录下应至少有一个 Schema 文件"
        for path in files:
            schema = json.loads(path.read_text(encoding="utf-8"))
            assert assertions._SCHEMA_KEYWORDS & set(schema), (
                f"{path.name} 不含任何 JSON Schema 关键字，schema 断言会空转"
            )


# ========== 11. markers 单一来源与 pytest.ini 同步 ==========

class TestMarkers:

    def test_markers_match_pytest_ini(self):
        ini = (PROJECT_ROOT / "pytest.ini").read_text(encoding="utf-8")
        for marker in KNOWN_MARKERS:
            assert re.search(rf"(?m)^\s*{marker}:\s", ini), (
                f"标记 '{marker}' 未在 pytest.ini 中注册，实际执行 -m {marker} 会报错"
            )

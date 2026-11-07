# selftest/test_casegen_unit.py - casegen 生成器的离线单测（不联网、不写业务目录）
import pytest

pytest.importorskip("plugins.casegen.gen_cases")

from plugins.casegen import gen_cases as g  # noqa: E402


class FakeResponse:
    def __init__(self, body, status=200, content_type="application/json"):
        self._body = body
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, body=None):
        self.calls = []
        self._body = body if body is not None else {"code": 200, "msg": "ok", "data": None}

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return FakeResponse(self._body)


def _entry(**kwargs):
    base = dict(key="system.demo", module="system", name="demo", method="GET", path="/system/demo")
    base.update(kwargs)
    return g.ApiEntry(**base)


# ========== 类型推断：null 不能退化成 str ==========
def test_type_name_null():
    assert g._type_name(None) == "null"


@pytest.mark.parametrize("value,expected", [
    (True, "bool"), (1, "int"), (1.5, "float"), ("x", "str"),
    ([], "list"), ({}, "dict"),
])
def test_type_name_basic(value, expected):
    assert g._type_name(value) == expected


def test_null_data_assertion_is_typed_null(tmp_path):
    body = {"code": 200, "msg": "isExist", "data": None}
    struct, _, schema_file = g.build_assertions(
        FakeResponse(body), body, "system.check", tmp_path, write=True,
    )
    assert {"type": "field_type", "path": "data", "expected": "null"} in struct
    # schema 断言默认生效，且文件确实落盘
    assert schema_file and (tmp_path / schema_file).exists()
    assert {"type": "schema", "schema_file": schema_file} in struct


# ========== 环境耦合：能对上 case_vars 的自动替换 ==========
def test_substitute_case_vars_in_path_and_query():
    entry = _entry(path="/system/dept/100/detail", payload="deptNo=100&pageNum=1")
    count = g.substitute_case_vars(entry, {"DEPT_ID": "100"})
    assert count == 2
    assert entry.path == "/system/dept/${DEPT_ID}/detail"
    assert entry.payload == "deptNo=${DEPT_ID}&pageNum=1"


def test_find_hardcoded_flags_long_id_only():
    entry = _entry(path="/asset/instock/v2/2072261774178902016", payload="id=2072261774178902016&pageNum=1")
    hits = g.find_hardcoded(entry)
    assert len(hits) == 2
    assert any("2072261774178902016" in h for h in hits)

    clean = _entry(path="/system/orgList", payload="parentId=0&pageNum=1")
    assert g.find_hardcoded(clean) == []


# ========== 安全策略：默认只读，危险接口只生成骨架 ==========
def test_call_interface_skips_write_by_default():
    client = FakeClient()
    _, _, reason = g.call_interface(client, _entry(method="POST", payload='{"a": 1}'), (5, 10))
    assert reason and "--allow-write" in reason
    assert client.calls == []


def test_call_interface_never_calls_delete():
    client = FakeClient()
    _, _, reason = g.call_interface(client, _entry(method="DELETE"), (5, 10), allow_write=True)
    assert reason and "DELETE" in reason
    assert client.calls == []


def test_call_interface_resolves_vars_for_get():
    client = FakeClient()
    entry = _entry(path="/system/dept/${DEPT_ID}", payload="deptNo=${DEPT_ID}")
    g.call_interface(client, entry, (5, 10), case_vars={"DEPT_ID": "100"})
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "/system/dept/100")
    assert kwargs["params"] == {"deptNo": "100"}


# ========== 骨架用例自动 skip ==========
def test_emit_case_skeleton_has_skip_marker():
    entry = _entry(method="POST")
    text = g.emit_case(
        entry, [{"type": "status_code", "expected": 200}], [],
        ["POST 接口默认不调用（只读模式）"], default_markers=("auto",),
        extra_markers=("skip",),
    )
    assert "markers: [auto, skip]" in text
    assert "# TODO" in text

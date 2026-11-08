# selftest/test_casegen_unit.py - casegen 生成器的离线单测（不联网、不写业务目录）
import json

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


def test_schema_replaces_per_field_assertions(tmp_path):
    """字段存在性/类型交给 schema，不再逐字段展开 exists/field_type"""
    body = {"code": 200, "msg": "ok",
            "data": [{"id": 1, "name": "a", "parentId": None}]}
    struct, _, schema_file = g.build_assertions(
        FakeResponse(body), body, "system.list", tmp_path, write=True,
    )
    # 除 null 字段外，不再有 data.0.* 的 exists / field_type
    per_field = {a["path"] for a in struct
                 if a["type"] in ("exists", "field_type")
                 and a.get("path", "").startswith("data.0")}
    assert per_field == {"data.0.parentId"}   # parentId 为 null，需要成对断言
    # 非空列表由 list_len 保证（schema 的 items 管不到空数组）
    assert {"type": "list_len", "path": "data", "operator": "gte", "expected": 1} in struct
    # null 字段 schema 不约束类型，仍需显式断言
    assert {"type": "exists", "path": "data.0.parentId", "expected": True} in struct
    assert {"type": "field_type", "path": "data.0.parentId", "expected": "null"} in struct
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


# ========== 离线：只有响应 JSON ==========
def test_generate_from_json_is_offline(tmp_path):
    """接口暂时调不通时，用开发给的响应样例直接生成 schema"""
    resp = tmp_path / "resp.json"
    resp.write_text(
        '{"code": 200, "msg": "ok", "data": [{"id": 1, "name": "a", "extra": null}]}',
        encoding="utf-8",
    )
    out = tmp_path / "schemas"
    out.mkdir()

    name = g.generate_from_json(resp, "asset.demo_detail", out)

    assert name == "gen_asset_demo_detail.json"
    schema = json.loads((out / name).read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    assert "data" in schema["required"]


def test_generate_from_json_rejects_key_without_module(tmp_path):
    resp = tmp_path / "resp.json"
    resp.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="模块.接口名"):
        g.generate_from_json(resp, "NoModule", tmp_path)


def test_from_json_requires_key():
    with pytest.raises(SystemExit):
        g._parse_args(["--from-json", "resp.json"])


def test_from_json_does_not_need_list():
    args = g._parse_args(["--from-json", "resp.json", "--key", "asset.demo"])
    assert (args.from_json, args.key, args.list) == ("resp.json", "asset.demo", None)

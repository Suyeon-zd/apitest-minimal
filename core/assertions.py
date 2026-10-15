# core/assertions.py - 系统化断言引擎
import json
from pathlib import Path
import jsonschema
from core.logger import logger
import re


# ========== 断言结果数据结构 ==========
class AssertionResult:
    """单条断言的执行结果"""

    def __init__(self, assert_type, passed, message, actual=None, expected=None):
        self.type = assert_type
        self.passed = passed
        self.message = message
        self.actual = actual
        self.expected = expected

    def __str__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"[{self.type}] {status}: {self.message}"



# ========== 工具函数 ==========

def _normalize_path(path: str) -> str:
    """把 data[0].name 归一化为 data.0.name，兼容两种写法"""
    return re.sub(r"\[(\d+)\]", r".\1", path)


# Schema 文件目录（模块级常量，便于单测替换为临时目录）
SCHEMA_DIR = Path(__file__).parent.parent / "schemas"


def get_by_path(data, path):
    """按点路径从嵌套结构中取值，支持列表索引：data.0.name"""
    if not path:
        return data
    path = _normalize_path(path)
    keys = path.split(".")
    current = data
    for key in keys:
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


# JSON Schema 关键字：用于识别"文件其实是响应快照、不是 Schema"的伪断言
_SCHEMA_KEYWORDS = frozenset({
    "$schema", "$ref", "$defs", "definitions", "type", "properties", "required",
    "items", "enum", "const", "anyOf", "allOf", "oneOf", "not",
    "additionalProperties", "patternProperties", "pattern", "format",
    "minimum", "maximum", "minLength", "maxLength",
})


def compare(actual, expected, operator="eq"):
    """通用比较器，支持 eq/ne/gt/lt/gte/lte/contains/in"""
    ops = {
        "eq": lambda a, e: a == e,
        "ne": lambda a, e: a != e,
        "gt": lambda a, e: a > e,
        "lt": lambda a, e: a < e,
        "gte": lambda a, e: a >= e,
        "lte": lambda a, e: a <= e,
        "contains": lambda a, e: e in (a if a is not None else ""),
        "in": lambda a, e: a in (e if isinstance(e, (list, str)) else [e]),
    }
    func = ops.get(operator)
    if not func:
        return False, f"不支持的比较符: {operator}"
    try:
        return func(actual, expected), None
    except Exception as e:
        return False, f"比较异常: {e}"


# ========== 断言引擎 ==========
class AssertionEngine:
    """系统化断言引擎：执行一组断言规则，返回结果列表"""

    def run(self, response, assert_list):
        if not assert_list:
            return True, []

        results = []
        total = len(assert_list)
        for idx, rule in enumerate(assert_list, 1):
            assert_type = rule.get("type")
            handler = getattr(self, f"_assert_{assert_type}", None)
            if not handler:
                result = AssertionResult(
                    assert_type, False,
                    f"不支持的断言类型: {assert_type}"
                )
            else:
                try:
                    result = handler(response, rule)
                except Exception as e:
                    result = AssertionResult(assert_type, False, f"断言执行异常: {e}")

            results.append(result)
            logger.info("[%d/%d] %s", idx, total, result)

        all_passed = all(r.passed for r in results)
        return all_passed, results

    # ---- 1. 状态码断言 ----
    def _assert_status_code(self, resp, rule):
        expected = rule.get("expected")
        actual = resp.status_code
        passed = actual == expected
        return AssertionResult(
            "status_code", passed,
            f"预期状态码 {expected}, 实际 {actual}",
            actual=actual, expected=expected
        )

    # ---- 2. 响应头断言 ----
    def _assert_header(self, resp, rule):
        field = rule.get("field")
        expected = rule.get("expected")
        operator = rule.get("operator", "eq")
        actual = resp.headers.get(field)
        passed, err = compare(actual, expected, operator)
        msg = f"响应头 {field}: 预期 {expected}({operator}), 实际 {actual}"
        if err:
            msg += f" ({err})"
        return AssertionResult("header", passed, msg, actual=actual, expected=expected)

    # ---- 3. JSON 路径值断言 ----
    def _assert_json_path(self, resp, rule):
        path = rule.get("path")
        expected = rule.get("expected")
        operator = rule.get("operator", "eq")
        try:
            body = resp.json()
        except Exception:
            return AssertionResult("json_path", False, f"响应体不是合法 JSON: {resp.text[:200]}")
        actual = get_by_path(body, path)
        passed, err = compare(actual, expected, operator)
        msg = f"字段 {path}: 预期 {expected}({operator}), 实际 {actual}"
        if err:
            msg += f" ({err})"
        return AssertionResult("json_path", passed, msg, actual=actual, expected=expected)

    # ---- 4. 字段类型断言 ----
    def _assert_field_type(self, resp, rule):
        path = rule.get("path")
        expected = rule.get("expected")  # str/int/list/dict/bool/float/null
        try:
            body = resp.json()
        except Exception:
            return AssertionResult("field_type", False, f"响应体不是合法 JSON: {resp.text[:200]}")
        actual_val = get_by_path(body, path)
        type_map = {
            "str": str, "int": int, "list": list, "dict": dict,
            "bool": bool, "float": float, "null": type(None)
        }
        actual_type = type(actual_val).__name__
        # bool 是 int 的子类：断言 int 时需排除布尔值，避免 True/False 误判为 int
        if expected == "int":
            passed = isinstance(actual_val, int) and not isinstance(actual_val, bool)
        else:
            passed = isinstance(actual_val, type_map.get(expected, object))
        return AssertionResult(
            "field_type", passed,
            f"字段 {path}: 预期类型 {expected}, 实际 {actual_type}",
            actual=actual_type, expected=expected
        )

    # ---- 5. 字段存在性断言 ----
    def _assert_exists(self, resp, rule):
        path = rule.get("path")
        expected = rule.get("expected", True)
        try:
            body = resp.json()
        except Exception:
            return AssertionResult("exists", False, f"响应体不是合法 JSON: {resp.text[:200]}")
        # 检查路径上每一层是否存在（最后一层允许值为 None）
        keys = _normalize_path(path).split(".")
        current = body
        exists = True
        for key in keys:
            if isinstance(current, list):
                try:
                    current = current[int(key)]
                except (ValueError, IndexError):
                    exists = False
                    break
            elif isinstance(current, dict):
                if key not in current:
                    exists = False
                    break
                current = current[key]
            else:
                exists = False
                break
        passed = exists == expected
        return AssertionResult(
            "exists", passed,
            f"字段 {path}: 预期存在={expected}, 实际存在={exists}",
            actual=exists, expected=expected
        )

    # ---- 6. 列表长度断言 ----
    def _assert_list_len(self, resp, rule):
        path = rule.get("path")
        expected = rule.get("expected")
        operator = rule.get("operator", "eq")
        try:
            body = resp.json()
        except Exception:
            return AssertionResult("list_len", False, f"响应体不是合法 JSON: {resp.text[:200]}")
        actual_val = get_by_path(body, path)
        if not isinstance(actual_val, list):
            return AssertionResult(
                "list_len", False,
                f"字段 {path} 不是列表，实际类型 {type(actual_val).__name__}"
            )
        actual = len(actual_val)
        passed, err = compare(actual, expected, operator)
        msg = f"列表 {path} 长度: 预期 {expected}({operator}), 实际 {actual}"
        if err:
            msg += f" ({err})"
        return AssertionResult("list_len", passed, msg, actual=actual, expected=expected)

    # ---- 7. 字符串包含断言 ----
    def _assert_contains(self, resp, rule):
        path = rule.get("path")
        expected = rule.get("expected")
        try:
            body = resp.json()
        except Exception:
            return AssertionResult("contains", False, f"响应体不是合法 JSON: {resp.text[:200]}")
        actual = get_by_path(body, path)
        passed = expected in (actual if actual is not None else "")
        return AssertionResult(
            "contains", passed,
            f"字段 {path}: 预期包含 {expected!r}, 实际 {actual!r}",
            actual=actual, expected=expected
        )

    # ---- 9. JSON Schema 结构校验 ----
    def _assert_schema(self, resp, rule):
        """用 JSON Schema 一次性校验整个响应体的结构（必填字段、类型、嵌套结构）"""
        schema_file = rule.get("schema_file")
        if not schema_file:
            return AssertionResult("schema", False, "缺少 schema_file 配置")

        # 从项目根目录的 schemas/ 下加载 schema 文件
        schema_path = SCHEMA_DIR / schema_file
        if not schema_path.exists():
            return AssertionResult("schema", False, f"Schema 文件不存在: {schema_path}")

        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            # 防空转：若文件里没有任何 JSON Schema 关键字（例如误放了响应快照），
            # jsonschema 会视为空 schema 而永远通过，这里必须显式判失败
            if not _SCHEMA_KEYWORDS & set(schema or {}):
                return AssertionResult(
                    "schema", False,
                    f"{schema_file} 不是合法的 JSON Schema（缺少 type/properties/required 等关键字），"
                    f"该断言无法拦截任何结构变化，请改用 tools/gen_cases.py 生成的 gen_*.json"
                )
            body = resp.json()
            jsonschema.validate(instance=body, schema=schema)
            return AssertionResult("schema", True, f"响应结构符合 Schema: {schema_file}")
        except jsonschema.ValidationError as e:
            return AssertionResult(
                "schema", False,
                f"Schema 校验失败: 字段 {'.'.join(str(p) for p in e.path)} -> {e.message}"
            )
        except Exception as e:
            return AssertionResult("schema", False, f"Schema 校验异常: {e}")

    # ---- 10. 数组批量断言（每个元素都满足条件） ----
    def _assert_each(self, resp, rule):
        """遍历列表中每个元素，校验指定字段是否都满足条件"""
        path = rule.get("path")          # 列表路径，如 "data.list"
        field = rule.get("field")        # 每个元素的字段，如 "status"
        expected = rule.get("expected")  # 期望值
        operator = rule.get("operator", "eq")

        try:
            body = resp.json()
        except Exception:
            return AssertionResult("each", False, f"响应体不是合法 JSON: {resp.text[:200]}")

        items = get_by_path(body, path)
        if not isinstance(items, list):
            return AssertionResult("each", False, f"字段 {path} 不是列表，实际类型 {type(items).__name__}")

        if len(items) == 0:
            return AssertionResult("each", True, f"列表 {path} 为空，跳过批量校验")

        failed = []
        for i, item in enumerate(items):
            actual = item.get(field) if isinstance(item, dict) else None
            passed, err = compare(actual, expected, operator)
            if not passed:
                failed.append(f"第{i}项 {field}={actual}")

        if not failed:
            return AssertionResult(
                "each", True,
                f"列表 {path} 的所有 {len(items)} 个元素 {field} 均 {operator} {expected}"
            )
        else:
            return AssertionResult(
                "each", False,
                f"列表 {path} 有 {len(failed)}/{len(items)} 个元素不满足: {'; '.join(failed[:5])}"
            )

    # ---- 11. 响应时间断言（SLA） ----
    def _assert_response_time(self, resp, rule):
        """校验接口耗时：expected 单位秒，operator 默认 lte（不超过），可用 lt/gt/gte"""
        expected = rule.get("expected")
        operator = rule.get("operator", "lte")
        elapsed = getattr(resp, "elapsed", None)
        if elapsed is None:
            return AssertionResult(
                "response_time", False, "响应对象缺少 elapsed 属性，无法获取接口耗时"
            )
        actual = round(elapsed.total_seconds(), 3)
        passed, err = compare(actual, expected, operator)
        msg = f"接口耗时: 预期 {operator} {expected}s, 实际 {actual}s"
        if err:
            msg += f" ({err})"
        return AssertionResult("response_time", passed, msg, actual=actual, expected=expected)

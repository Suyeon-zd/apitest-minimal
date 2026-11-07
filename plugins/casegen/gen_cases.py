# plugins/casegen/gen_cases.py - 测试用例自动生成器（契约快照式）
#
# 思路：接口清单 txt → 逐个真实调用 → 解析响应结构 → 自动生成基线用例 YAML。
# 与手写用例（如 test_data/asset/asset_summary.yaml）互补：生成器负责"结构层"基线，
# 值层断言以注释保留，人工确认后放开。
#
# 用法：
#   python -m plugins.casegen.gen_cases --list plugins/casegen/case_list.txt            # 正式生成
#   python -m plugins.casegen.gen_cases --list plugins/casegen/case_list.txt --dry-run  # 只预览不写文件
#   python -m plugins.casegen.gen_cases --list plugins/casegen/case_list.txt --force    # 整文件覆盖重新生成
#   python -m plugins.casegen.gen_cases --list plugins/casegen/case_list.txt --env staging  # 指定环境
#
# 清单格式（每行一个接口，# 开头为注释）：
#   格式1（推荐）：已在 api/endpoints.yaml 登记的接口：  模块.名称 [| payload]
#   格式2（新接口）：                                    模块.名称 | METHOD | /path [| payload]
#
# 安全策略（分级处理）：
#   GET          → 真实调用（只读，安全）
#   非 GET       → 默认只读模式不调用，只生成带 TODO 的骨架用例；
#                  确认接口无副作用后加 --allow-write 才真实调用
#   POST/PUT     → 即使允许写，清单没给 payload 也仍只生成骨架用例
#   DELETE       → 一律只生成骨架用例，绝不调用（防止污染业务数据）
#   响应异常     → HTTP 非 200 / 响应非 JSON / 业务 code != 200 时降级为骨架用例 + TODO，
#                  避免把错误响应的结构固化成断言
#   骨架用例     → 自动打 skip 标记，留在套件里不会误跑成红灯
import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import yaml

# 积木位置：plugins/casegen/（非核心，需要时从项目根目录用 -m 运行）
# 嵌回方式：本目录可整体保留在 plugins/ 下运行，也可拷回项目根 tools/ 目录
# （拷回根目录 tools/ 时把本行的 parents[2] 改回 parents[1]）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

from core.config_loader import load_config                       # noqa: E402
from core.auth_manager import AuthManager                        # noqa: E402
from core.base_client import BaseClient                          # noqa: E402
from api.registry import EndpointRegistry                        # noqa: E402
import logging                                                   # noqa: E402

# CLI 工具的日志配置（独立于 pytest：控制台输出即可，不写 output/logs 文件）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)                             # noqa: E402

TODAY = date.today().isoformat()
HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")

# 值层断言注释块标题（数据敏感，默认注释保留）
VALUE_LAYER_TITLE = "值层断言（数据敏感，人工确认后放开）"


@dataclass
class ApiEntry:
    """清单中的一条接口（key 来源或全格式来源归一化后统一使用）"""

    key: str                                    # 逻辑名：模块.名称
    module: str                                 # 业务模块名
    name: str                                   # 接口名
    method: str = ""                            # HTTP 方法（大写）
    path: str = ""                              # 接口路径
    payload: str = None                         # 可选请求参数（GET=query string，POST=json/原文）
    source: str = "key"                         # key=注册中心已有；full=清单全格式


# ==================== 清单解析 ====================

def parse_list_file(list_path: Path) -> list:
    """解析清单文件 → [ApiEntry]，格式非法立即报错并给出行号"""
    entries = []
    for lineno, raw in enumerate(list_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        # 格式2：模块.名称 | METHOD | /path [| payload]
        if len(parts) >= 3 and parts[1].upper() in HTTP_METHODS:
            if "." not in parts[0]:
                raise ValueError(f"清单第 {lineno} 行: '{parts[0]}' 缺少模块前缀（应为 模块.名称）")
            mod, name = parts[0].split(".", 1)
            entries.append(ApiEntry(
                key=parts[0], module=mod, name=name,
                method=parts[1].upper(), path=parts[2],
                # payload 自身可能含竖线（如 query 参数值），第 4 段起重新拼回
                payload="|".join(parts[3:]) if len(parts) > 3 else None,
                source="full",
            ))
        # 格式1：模块.名称 [| payload]（payload 含竖线时第 2 段起拼回；第 2 段是
        # HTTP 方法但缺 path 的情况不进此分支，由下方报错提示）
        elif "." in parts[0] and (len(parts) == 1 or parts[1].upper() not in HTTP_METHODS):
            mod, name = parts[0].split(".", 1)
            entries.append(ApiEntry(
                key=parts[0], module=mod, name=name,
                payload="|".join(parts[1:]) if len(parts) > 1 else None,
                source="key",
            ))
        else:
            raise ValueError(
                f"清单第 {lineno} 行无法识别: {raw!r}\n"
                f"  格式1: 模块.名称 [| payload]\n"
                f"  格式2: 模块.名称 | METHOD | /path [| payload]"
            )
    if not entries:
        raise ValueError(f"清单为空（仅注释/空行）: {list_path}")
    return entries


def _parse_query(payload: str) -> dict:
    """query string → dict（保留空值参数，与 testcases.parse_payload 同规则）"""
    return {k: v[0] for k, v in parse_qs(payload, keep_blank_values=True).items()}


# ==================== 环境耦合检查 ====================

VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# 疑似环境耦合值：4 位以上数字 ID、UUID、16 位以上十六进制
_HARDCODED_RE = re.compile(
    r"^(?:\d{4,}|[0-9a-fA-F]{16,}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def _resolve_case_vars(text: str, case_vars: dict) -> str:
    """把 ${VAR} 还原成 case_vars 中的真实值（真实调用用；变量不存在则原样保留）"""
    if not isinstance(text, str) or "${" not in text:
        return text
    return VAR_RE.sub(lambda m: str(case_vars.get(m.group(1), m.group(0))), text)


def substitute_case_vars(entry: ApiEntry, case_vars: dict) -> int:
    """把与 case_vars 值完全一致的 query 参数 / 路径段替换成 ${KEY}

    只替换配置里已有同名变量的值，不猜变量名；返回替换处数。
    """
    if not case_vars:
        return 0
    value_to_key = {}
    for key, value in case_vars.items():
        if isinstance(value, bool) or value in (None, ""):
            continue
        value_to_key.setdefault(str(value), str(key))

    count = 0
    segments = entry.path.split("/")
    for i, seg in enumerate(segments):
        if seg in value_to_key:
            segments[i] = "${" + value_to_key[seg] + "}"
            count += 1
    entry.path = "/".join(segments)

    if entry.payload and entry.method == "GET":
        pairs = []
        for pair in entry.payload.split("&"):
            key, sep, value = pair.partition("=")
            if sep and value in value_to_key:
                value = "${" + value_to_key[value] + "}"
                count += 1
            pairs.append(f"{key}{sep}{value}" if sep else pair)
        entry.payload = "&".join(pairs)
    return count


def find_hardcoded(entry: ApiEntry) -> list:
    """列出仍疑似写死环境数据的参数/路径段（已被 case_vars 替换的不算）"""
    hits = []
    for seg in entry.path.split("/"):
        if _HARDCODED_RE.match(seg):
            hits.append(f"路径段 {seg}")
    if entry.payload and entry.method == "GET":
        for pair in entry.payload.split("&"):
            key, sep, value = pair.partition("=")
            if sep and _HARDCODED_RE.match(value):
                hits.append(f"参数 {key}={value}")
    return hits


# ==================== 注册中心追加 ====================

def _find_block_end(lines: list, module: str) -> int:
    """定位模块 YAML 块的结束行号（下一个顶级条目之前的最后一个非空行）"""
    pat = re.compile(rf"^{re.escape(module)}\s*:\s*$")
    start = next((i for i, l in enumerate(lines) if pat.match(l)), None)
    if start is None:
        return -1
    i = start + 1
    while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
        i += 1
    # i = 下一个顶级条目（顶格）或 EOF；回退尾随空行，让插入点紧贴块内容
    end = i
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def append_endpoints(new_apis: list, ep_path: Path = None) -> Path:
    """把未登记接口文本追加进 endpoints.yaml（先备份 .bak；只做文本插入，保留手工注释）

    - 模块已存在 → 定位块末尾插入接口条目
    - 模块不存在 → 文件末尾追加新模块块（带来源注释）
    :return: 备份文件路径
    """
    ep_path = ep_path or (PROJECT_ROOT / "api" / "endpoints.yaml")
    if not ep_path.exists():
        raise FileNotFoundError(f"接口注册中心不存在: {ep_path}")

    backup = ep_path.with_suffix(".yaml.bak")
    shutil.copy2(ep_path, backup)

    lines = ep_path.read_text(encoding="utf-8").splitlines()
    existing_modules = {l.split(":", 1)[0].strip()
                        for l in lines if l and not l.startswith((" ", "\t", "#")) and l.endswith(":")}

    insertions = []   # (插入位置, 新行列表)
    for mod in sorted({a.module for a in new_apis}):
        apis = [a for a in new_apis if a.module == mod]
        block = []
        for i, api in enumerate(apis):
            if i:
                block.append("")     # 条目间空行（匹配文件既有风格）
            block += [f"  {api.name}:", f"    method: {api.method}", f"    path: {api.path}"]
        if mod in existing_modules:
            idx = _find_block_end(lines, mod)
            if idx < 0:
                raise ValueError(f"模块 '{mod}' 在 endpoints.yaml 中定位失败，请手工登记")
            if lines[idx - 1].strip():
                block = [""] + block  # 前置空行分隔既有条目
            insertions.append((idx, block))
        else:
            header = ["", f"# ========== {mod}（gen_cases 自动登记 {TODAY}）==========", f"{mod}:"]
            insertions.append((len(lines), header + block))

    # 按位置升序插入并累计偏移量：同位置（如都在文件末尾）时先插入的排前面，
    # 避免降序插入时后插内容反插到前面模块块内部
    offset = 0
    for idx, new_lines in sorted(insertions, key=lambda x: x[0]):
        lines[idx + offset:idx + offset] = new_lines
        offset += len(new_lines)

    ep_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("已登记 %d 个新接口到 %s（备份: %s）", len(new_apis), ep_path.name, backup.name)
    return backup


# ==================== 接口调用 ====================

def call_interface(client: BaseClient, entry: ApiEntry, timeout,
                   allow_write: bool = False, case_vars: dict = None):
    """按分级策略调用接口；返回 (resp, body_dict_or_None, skip_reason_or_None)

    skip_reason 非 None 表示本次未真实调用（危险接口 / 只读模式 / 写接口缺 payload）。
    """
    if entry.method == "DELETE":
        return None, None, "DELETE 危险接口未自动调用，请人工补充 payload 与断言"
    if entry.method != "GET" and not allow_write:
        return None, None, (
            f"{entry.method} 接口默认不调用（只读模式）；确认无副作用后加 --allow-write 重跑"
        )
    if entry.method != "GET" and not entry.payload:
        return None, None, f"{entry.method} 接口缺少 payload，请人工补充后重跑"

    case_vars = case_vars or {}
    path = _resolve_case_vars(entry.path, case_vars)
    payload = _resolve_case_vars(entry.payload, case_vars) if entry.payload else None

    kwargs = {"timeout": tuple(timeout)}
    if entry.method == "GET":
        kwargs["params"] = _parse_query(payload) if payload else None
    else:
        # 写接口：以 { 开头按 JSON body 发送，否则按原始文本（text/plain）发送
        if payload.lstrip().startswith("{"):
            try:
                kwargs["json"] = json.loads(payload)
            except json.JSONDecodeError as e:
                return None, None, f"payload 不是合法 JSON（{e}），请修正后重跑"
        else:
            kwargs["data"] = payload
            kwargs["headers"] = {"Content-Type": "text/plain"}

    resp = client.request(entry.method, path, **kwargs)
    if resp.status_code != 200:
        return resp, None, f"HTTP 状态码 {resp.status_code}（非 200），响应已截断记录在 TODO 中"
    try:
        body = resp.json()
    except ValueError:
        return resp, None, "响应体不是合法 JSON，无法解析结构"
    if isinstance(body, dict) and body.get("code") not in (None, 200):
        return resp, body, f"业务响应 code={body.get('code')}（非 200），可能缺少请求参数"
    return resp, body, None


# ==================== 断言生成 ====================

def _type_name(value) -> str:
    """响应值 → field_type 断言的类型名（bool 必须先于 int 判断，bool 是 int 子类）"""
    if value is None:
        return "null"      # 不能落到 str：null 会变成"永远通过"或"必然失败"的错误断言
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "str"


def to_schema(value, strict=False):
    """响应值 → JSON Schema（结构契约：所有观察到的字段 required；null 字段不约束类型）

    - 默认 strict=False：additionalProperties=True（向后兼容，不破现有生成的 schema）
      不升级默认；存量已生成的 gen_*.json 仍按旧合同通过校验
    - strict=True 时 additionalProperties=False（更严格结构约束，CI 可加 --strict-schema
      一次回归看新生成的 schema 是否会因字段差异被拒）
    """
    if isinstance(value, dict):
        props = {k: to_schema(v, strict) for k, v in value.items()}
        node = {
            "type": "object",
            "properties": props,
            "required": list(value.keys()),
        }
        # strict 模式才强制 additionalProperties=False；否则 True（兼容）
        node["additionalProperties"] = (not strict)
        return node
    if isinstance(value, list):
        item = to_schema(value[0], strict) if value else ({"type": "object", "additionalProperties": (not strict)})
        return {"type": "array", "items": item}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    return {}   # 观察到 null 或未知类型：仅要求字段存在，不约束类型（避免数据变化误报）


def _meta_block(source_key: str) -> dict:
    """每个生成的 schema 顶部加 _meta 块：固化时间 + 来源 key + strict 开关

    便于在巡检时快速回答"这是哪条用例、什么时候生成的"；_ 前缀保证
    业务端不会把它当成被测字段暴露。
    """
    import datetime
    return {
        "_meta": {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source_key": source_key,
            "generator": "plugins/casegen/gen_cases.py",
        }
    }


def build_schema_file(key: str, body, schemas_dir: Path, strict=False, write: bool = True):
    """生成 <schemas_dir>/gen_<key>.json，返回文件名（dry-run 不落盘）"""
    fname = f"gen_{re.sub(r'[^0-9A-Za-z]+', '_', key)}.json"
    if not write:
        return fname    # dry-run 时只算名字，不写
    schema = to_schema(body, strict=strict)
    if not schema:
        return None
    # 顶部注入 _meta（与业务字段用 _ 前缀区分，不会污染断言目标）
    final = {**schema, **(_meta_block(key) if isinstance(schema, dict) else {})} \
        if isinstance(schema, dict) and schema else schema
    (schemas_dir / fname).write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return fname


def build_assertions(resp, body, key: str, schemas_dir: Path,
                     write: bool = True, strict: bool = False):
    """结构层断言（生效）+ 值层断言（注释保留）；返回 (struct_list, value_comment_list, schema_file)

    - strict: 透传给 build_schema_file，控制生成 schema 的 additionalProperties 默认值
    """
    struct, value_comments = [], []
    struct.append({"type": "status_code", "expected": 200})
    if "json" in (resp.headers.get("Content-Type") or "").lower():
        struct.append({"type": "header", "field": "Content-Type",
                       "operator": "contains", "expected": "application/json"})

    schema_file = None
    if isinstance(body, dict):
        if "code" in body:
            struct.append({"type": "json_path", "path": "code", "expected": body["code"]})
        if "msg" in body:
            struct.append({"type": "json_path", "path": "msg", "expected": body["msg"]})
        if "data" in body:
            data = body["data"]
            struct.append({"type": "exists", "path": "data", "expected": True})
            struct.append({"type": "field_type", "path": "data", "expected": _type_name(data)})

            # 取样本对象展开第一层字段（列表取首个元素，字典取自身）
            if isinstance(data, list) and data and isinstance(data[0], dict):
                sample, prefix = data[0], "data.0"
                struct.append({"type": "field_type", "path": prefix, "expected": "dict"})
                if write:
                    value_comments.append({"type": "list_len", "path": "data",
                                           "operator": "gte", "expected": len(data)})
            elif isinstance(data, dict):
                sample, prefix = data, "data"
            else:
                sample, prefix = None, None

            if sample is not None:
                for k, v in sample.items():
                    p = f"{prefix}.{k}"
                    struct.append({"type": "exists", "path": p, "expected": True})
                    if v is None:
                        # null 值类型不稳定：结构层只断言存在，类型/null 断言下沉到值层
                        value_comments.append({"type": "json_path", "path": p, "expected": None})
                    else:
                        struct.append({"type": "field_type", "path": p, "expected": _type_name(v)})
        if write:
            schema_file = build_schema_file(key, body, schemas_dir, strict=strict, write=write)
            if schema_file:
                struct.append({"type": "schema", "schema_file": schema_file})
    else:
        struct.append({"type": "field_type", "path": "", "expected": _type_name(body)})
    return struct, value_comments, schema_file


# ==================== YAML 输出 ====================

def _yaml_inline(value) -> str:
    """值 → 单行流式 YAML 字符串（安全处理引号/特殊字符）"""
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=True,
                          width=4096).replace("\n...", "").strip()


def emit_case(entry: ApiEntry, struct: list, value_comments: list,
              todo_lines: list = None, default_markers=None,
              extra_markers=()) -> str:
    """单条用例 → YAML 文本块（紧凑内联风格，与手写模板一致）

    - default_markers: 自动用例默认 markers（如 ['auto']）
    - extra_markers: 追加标记（骨架用例传 ('skip',)，避免未完成的用例跑成红灯）
    """
    lines = [
        f"- api_name: \"{entry.key}（自动生成基线 {TODAY}）\"",
        f"  api: {entry.key}",
    ]
    if entry.payload:
        lines.append(f"  payload: {_yaml_inline(entry.payload)}")
    # 自动用例默认打 'auto' 标记（pytest.ini 已注册该 marker）：
    # 便于 CI 通过 -m "not auto" 单独跑手写用例，或反之
    markers = list(default_markers or [])
    for m in extra_markers:
        if m not in markers:
            markers.append(m)
    if markers:
        lines.append(f"  markers: {_yaml_inline(markers)}")
    for todo in (todo_lines or []):
        lines.append(f"  # TODO: {todo}")
    lines.append("  assert:")
    for a in struct:
        lines.append(f"    - {_yaml_inline(a)}")
    if value_comments:
        lines.append(f"    # ---- {VALUE_LAYER_TITLE} ----")
        for a in value_comments:
            lines.append(f"    # - {_yaml_inline(a)}")
    return "\n".join(lines)


def save_module_cases(module: str, blocks: list, out_dir: Path, force: bool) -> dict:
    """按模块写出 <module>_gen.yaml；默认跳过已存在的 api（保护人工修改），--force 整文件覆盖

    --force 路径现在会强制把旧 _gen.yaml 备份到 <APITEST_OUTPUT_DIR>/gen_cases_backups/<时间戳>/ 下再整文件覆盖，
    避免历史一句"force 一下"造成的人工调过的断言全部丢失。
    """
    path = out_dir / module / f"{module}_gen.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys = set()
    if path.exists() and not force:
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(existing, list):
            raise ValueError(f"既有生成文件不是用例列表，拒绝覆盖: {path}")
        existing_keys = {c.get("api") for c in existing if isinstance(c, dict)}

    fresh = [b for b in blocks if b["entry"].key not in existing_keys]
    skipped = len(blocks) - len(fresh)
    if force:
        # ---- 强制整文件覆盖前的安全备份（避免一行 --force 抹掉手写调过的断言）----
        if path.exists():
            OUTPUT_ROOT = Path(os.getenv("APITEST_OUTPUT_DIR", PROJECT_ROOT / "output"))
            backup_dir = OUTPUT_ROOT / "gen_cases_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            import datetime
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"{module}_gen.{stamp}.yaml"
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            logger.warning(
                "--force 整文件覆盖: %s -> 已备份到 %s",
                path.relative_to(PROJECT_ROOT), backup_path.relative_to(PROJECT_ROOT),
            )
        body = "\n\n".join(b["text"] for b in blocks)
        path.write_text(_file_header() + "\n" + body + "\n", encoding="utf-8")
    elif fresh:
        new_text = "\n\n".join(b["text"] for b in fresh)
        if path.exists():
            old = path.read_text(encoding="utf-8").rstrip() + "\n"
            path.write_text(old + "\n" + new_text + "\n", encoding="utf-8")
        else:
            path.write_text(_file_header() + "\n" + new_text + "\n", encoding="utf-8")
    return {"written": len(fresh), "skipped": skipped, "path": path}


def _file_header() -> str:
    return (f"# {TODAY} 由 plugins/casegen/gen_cases.py 自动生成（契约快照基线用例）\n"
            f"# 结构层断言已生效；值层断言以注释保留，确认后放开\n"
            f"# 重新生成默认跳过已存在的 api；--force 整文件覆盖（会丢弃人工修改）")


# ==================== 主流程 ====================

def _parse_args():
    parser = argparse.ArgumentParser(description="契约快照式测试用例生成器：清单→调用→解析→生成 YAML")
    parser.add_argument("--list", required=True, help="接口清单 txt 路径")
    parser.add_argument("--env", default="dev", help="环境：dev/staging/prod，默认 dev")
    parser.add_argument("--out", default="test_data", help="用例输出根目录，默认 test_data")
    parser.add_argument("--schemas-dir", default="test_data/schemas",
                        help="Schema 输出目录，默认 test_data/schemas")
    parser.add_argument("--force", action="store_true",
                        help="重新生成时整文件覆盖 <module>_gen.yaml（会自动备份到 <APITEST_OUTPUT_DIR>/gen_cases_backups/）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只调用与预览，不写任何文件（endpoints.yaml/用例/schema 均不落盘）")
    parser.add_argument("--allow-write", action="store_true",
                        help="允许真实调用 POST/PUT/PATCH（默认只读：非 GET 只生成骨架用例）")
    parser.add_argument("--strict-schema", action="store_true",
                        help="生成 schema 时把 additionalProperties 设为 False（更严格结构约束；"
                             "默认 False 保持向后兼容）")
    parser.add_argument("--default-markers", default="auto",
                        help="自动生成用例默认追加的 markers（逗号分隔，默认 'auto'；"
                             "设为空字符串则不打默认 marker）")
    return parser.parse_args()


def main():
    args = _parse_args()
    list_path = Path(args.list)
    if not list_path.is_absolute():
        list_path = PROJECT_ROOT / list_path
    out_dir = Path(args.out)
    out_dir = out_dir if out_dir.is_absolute() else PROJECT_ROOT / out_dir
    schemas_dir = Path(args.schemas_dir)
    schemas_dir = schemas_dir if schemas_dir.is_absolute() else PROJECT_ROOT / schemas_dir

    entries = parse_list_file(list_path)
    registry = EndpointRegistry.load()
    config = load_config(args.env)

    # ---- ① key 来源必须已登记；全格式新接口先登记 ----
    missing = [e.key for e in entries if e.source == "key" and e.key not in registry]
    if missing:
        raise ValueError(
            f"以下接口未在 api/endpoints.yaml 登记，请在清单中改用格式2（模块.名称 | METHOD | /path）:\n  "
            + "\n  ".join(missing)
        )
    new_apis = [e for e in entries if e.source == "full" and e.key not in registry]
    if new_apis:
        if args.dry_run:
            logger.info("[dry-run] 跳过登记 %d 个新接口: %s",
                        len(new_apis), ", ".join(a.key for a in new_apis))
        else:
            append_endpoints(new_apis)
            registry = EndpointRegistry.load()   # 重新加载并验证
            still_missing = [a.key for a in new_apis if a.key not in registry]
            if still_missing:
                raise ValueError(f"接口登记后仍未生效，请检查 endpoints.yaml: {still_missing}")

    # ---- ② 补全 method/path（key 来源从注册中心取；全格式已登记的以注册中心为准）----
    for e in entries:
        if e.key in registry:
            ep = registry.get(e.key)
            if e.source == "full" and (ep.method != e.method or ep.path != e.path):
                logger.warning("[%s] 清单中 method/path 与注册中心不一致，以注册中心为准", e.key)
            e.method, e.path = ep.method, ep.path

    # ---- ②.5 参数环境耦合：能对上 case_vars 的自动换成 ${VAR}，其余告警 ----
    case_vars = config.get("case_vars") or {}
    substituted = 0
    for e in entries:
        substituted += substitute_case_vars(e, case_vars)
        for hit in find_hardcoded(e):
            logger.warning(
                "[%s] 疑似写死环境数据（%s），建议改为 ${VAR} 并在 config/%s.yaml 的 case_vars 中定义",
                e.key, hit, args.env,
            )
    if substituted:
        logger.info("已将 %d 处与 case_vars 匹配的参数/路径段替换为 ${VAR}", substituted)

    # ---- ③ 登录 + 逐个调用 + 生成 ----
    auth = AuthManager(config)
    auth.login()
    client = BaseClient(base_url=config["base_url"], auth=auth)
    timeout = config.get("timeout", [5, 10])

    module_blocks = {}    # module → [ {entry, text} ]
    stats = {"generated": 0, "skeleton": 0, "failed": 0}
    for e in entries:
        logger.info("处理 %s %s %s", e.method, e.path, f"(payload={e.payload})" if e.payload else "")
        try:
            resp, body, skip_reason = call_interface(
                client, e, timeout, allow_write=args.allow_write, case_vars=case_vars,
            )
        except Exception as exc:   # 网络异常等：降级为骨架，不中断整批
            resp, body, skip_reason = None, None, f"请求异常: {exc}"

        todo_lines = []
        if skip_reason:
            todo_lines.append(skip_reason)
            if resp is not None:
                todo_lines.append(f"实际响应预览: {resp.text[:200]}")
            # 骨架用例：只保留意图断言（补充 payload 前会在报告中可见地失败）
            struct = [{"type": "status_code", "expected": 200}]
            value_comments = []
            if e.method != "DELETE":
                struct.append({"type": "json_path", "path": "code", "expected": 200})
            stats["skeleton"] += 1
        else:
            struct, value_comments, schema_file = build_assertions(
                resp, body, e.key, schemas_dir, write=not args.dry_run,
                strict=args.strict_schema,
            )
            if schema_file:
                logger.info("已生成 Schema: %s/%s", schemas_dir.name, schema_file)
            stats["generated"] += 1

        # 自动用例默认 markers：emitted 透传给 emit_case
        default_markers = (
            tuple(m for m in args.default_markers.split(",") if m.strip())
            if args.default_markers else None
        )
        block = {
            "entry": e,
            "text": emit_case(
                e, struct, value_comments, todo_lines, default_markers=default_markers,
                # 骨架用例打 skip：人工补完前不会把套件跑成红灯
                extra_markers=("skip",) if skip_reason else (),
            ),
        }
        module_blocks.setdefault(e.module, []).append(block)

    # ---- ④ 按模块写文件 ----
    if args.dry_run:
        for mod, blocks in module_blocks.items():
            print(f"\n{'=' * 60}\n将写入: {out_dir / mod / (mod + '_gen.yaml')}\n{'=' * 60}")
            for b in blocks:
                print(b["text"] + "\n")
        logger.info("[dry-run] 未写入任何文件")
    else:
        for mod, blocks in module_blocks.items():
            result = save_module_cases(mod, blocks, out_dir, args.force)
            rel = result["path"].relative_to(PROJECT_ROOT)
            logger.info("[%s] 新增 %d 条 / 跳过已存在 %d 条 → %s",
                        mod, result["written"], result["skipped"], rel)

    print("\n" + "=" * 50)
    print(f"生成完成：结构基线 {stats['generated']} 条 | 骨架(TODO) {stats['skeleton']} 条 | "
          f"涉及模块 {len(module_blocks)} 个")
    if stats["skeleton"]:
        print("骨架用例已标记 skip（补完 payload/断言后删除 markers 中的 skip）")
    print("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError) as e:
        logger.error("[ERROR] %s", e)
        sys.exit(1)

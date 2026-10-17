# plugins/casegen/har2list.py - HAR → 接口清单转换器（gen_cases.py 前置工具）
#
# 把 Chrome/Edge F12 Network 面板导出的 HAR 文件，一键转换为 gen_cases.py 的接口清单
# （格式2全格式行），避免近百个接口逐个手工复制。
#
# 用法：
#   1. F12 → Network → 勾选 Preserve log → 把各模块页面点一遍（让接口都发出来）
#   2. Network 面板空白处右键 → "Save all as HAR with content" 保存为 xxx.har
#   3. python -m plugins.casegen.har2list --har xxx.har                # base_url 自动读 config/dev.yaml
#      python -m plugins.casegen.har2list --har xxx.har --env staging  # 指定环境的 base_url
#      python -m plugins.casegen.har2list --har xxx.har --out my_list.txt
#   4. 人工审阅生成的清单（删掉不想测的行）→ python -m plugins.casegen.gen_cases --list <清单>
#
# 转换规则：
#   - 只保留 base_url 前缀下的请求（其余域名的静态/第三方请求全部过滤）
#   - 过滤静态资源（.js/.css/图片/字体）与登录接口（/auth/login，config 已有）
#   - 同 method+path 多次请求去重：保留参数信息量最大的一条（有 body 优先、query 更多优先）
#   - 模块名 = path 第一段；接口名 = path 末段转 snake_case（v2 等版本段自动带前一段，
#     模块内重名自动加长前缀消歧）
#   - GET 的 query 串、POST/PUT 的 JSON/表单 body 自动写成 payload
#   - multipart 文件上传接口无法离线还原 body：以注释行输出，人工确认后补 payload
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# 积木位置：plugins/casegen/（非核心，需要时从项目根目录用 -m 运行）
# 嵌回方式：本目录可整体保留在 plugins/ 下运行，也可拷回项目根 tools/ 目录
# （拷回根目录 tools/ 时把本行的 parents[2] 改回 parents[1]）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

from core.config_loader import load_config                       # noqa: E402
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
STATIC_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
                   ".woff", ".woff2", ".ttf", ".eot", ".map", ".html", ".htm")
LOGIN_PATH = "/auth/login"          # 登录接口在 config 中，无需生成用例


# ==================== HAR 解析 ====================

def load_entries(har_path: Path) -> list:
    """读取 HAR 文件 → request 列表（HAR 结构：log.entries[].request）"""
    har = json.loads(har_path.read_text(encoding="utf-8"))
    requests = [e.get("request", {}) for e in har.get("log", {}).get("entries", [])]
    if not requests:
        raise ValueError(f"HAR 中没有请求记录，请确认导出的是 Network 面板 HAR 文件: {har_path}")
    return requests


def extract_path(url: str, base_url: str) -> str:
    """完整 URL → 以 / 开头的接口路径（剥离 base_url 前缀与 query）；不匹配返回空串"""
    if not url.startswith(base_url):
        return ""
    path = url[len(base_url):].split("?", 1)[0]
    return path if path.startswith("/") else f"/{path}"


def is_static(path: str) -> bool:
    """静态资源判断（按后缀）"""
    return path.lower().endswith(STATIC_SUFFIXES)


def build_payload(req: dict) -> tuple:
    """HAR request → (payload_str 或 None, multipart_hint 或 None)

    - GET: 从 URL query 还原（调用方先把 query 拼进 req['_query']）
    - POST/PUT: JSON 原文 / x-www-form-urlencoded 由 params 数组还原
    - multipart: 文件内容无法离线还原 → 返回提示
    """
    method = req.get("method", "").upper()
    if method == "GET":
        qs = "_query" in req and req["_query"] or None
        return (qs, None) if qs else (None, None)
    post = req.get("postData") or {}
    mime = (post.get("mimeType") or "").lower()
    if mime.startswith("multipart/"):
        return None, "multipart 文件上传接口，body 无法从 HAR 还原，请人工补 payload"
    if "json" in mime or (not mime and post.get("text")):
        text = (post.get("text") or "").strip()
        return (text or None), None
    if "x-www-form-urlencoded" in mime:
        params = post.get("params") or []
        from urllib.parse import urlencode
        pairs = [(p.get("name", ""), p.get("value", "")) for p in params]
        return (urlencode(pairs) or None), None
    # 其他类型（如纯文本）：有原文就带上
    text = (post.get("text") or "").strip()
    return (text or None), None


# ==================== 命名与去重 ====================

def to_snake(name: str) -> str:
    """接口末段 → snake_case（驼峰/中划线统一转下划线小写）"""
    name = re.sub(r"[{}]", "", name)                    # 路径变量 {id} 去花括号
    s = re.sub(r"[-\s]+", "_", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)       # camelCase → camel_case
    return s.lower() or "root"


def make_name(path: str, module: str, dup_last: set, used: set) -> str:
    """path 末段 → 模块内唯一接口名

    - 末段是版本号（v2）或与模块内其他接口末段重名（如多个 list）→ 带前一段
      （版本段自动跳过）；仍重名逐级加长前缀，路径用尽后加序号兜底
    """
    segs = [s for s in path.split("/") if s]
    idx = len(segs) - 1
    name = to_snake(segs[idx])
    need_prefix = re.fullmatch(r"v\d+|\d+", segs[idx]) or (module, name) in dup_last
    while need_prefix and idx > 0:
        idx -= 1
        while idx > 0 and re.fullmatch(r"v\d+|\d+", segs[idx]):
            idx -= 1                              # 跳过版本段（instock/v2/list → instock_list）
        name = f"{to_snake(segs[idx])}_{name}"
        need_prefix = name in used                # 新名字仍冲突 → 继续往前带
    if name in used:                              # 兜底：路径全用尽仍重名 → 加序号
        n = 2
        while f"{name}_{n}" in used:
            n += 1
        name = f"{name}_{n}"
    used.add(name)
    return name


def _score(req: dict) -> tuple:
    """去重打分：有 body 优先、query 参数更多优先（保留参数信息量最大的一条）"""
    body = bool((req.get("postData") or {}).get("text"))
    return (body, len(req.get("queryString") or []))


# ==================== 主流程 ====================

def convert(har_path: Path, base_url: str) -> tuple:
    """HAR → (清单文本, 统计 dict)"""
    requests = load_entries(har_path)
    by_key = {}    # (method, path) → 参数信息量最大的 request（重复请求原地去重）
    stats = {"total": len(requests), "external": 0, "static": 0, "login": 0, "kept": 0}
    for req in requests:
        method = (req.get("method") or "").upper()
        if method not in HTTP_METHODS:
            stats["external"] += 1
            continue
        url = req.get("url", "")
        path = extract_path(url, base_url)
        if not path:                                    # 非 base_url 前缀（第三方/其他域名）
            stats["external"] += 1
            continue
        if is_static(path):
            stats["static"] += 1
            continue
        if path == LOGIN_PATH:
            stats["login"] += 1
            continue
        # GET 请求把 URL query 解析后挂到 request 上，供 build_payload 使用
        if method == "GET":
            pairs = parse_qs(urlsplit(url).query, keep_blank_values=True)
            req["_query"] = "&".join(
                f"{k}={v[0]}" for k, v in pairs.items()) or None
        key = (method, path)
        if key not in by_key or _score(req) > _score(by_key[key]):
            by_key[key] = req
        stats["kept"] += 1

    lines_by_module = {}
    used_names = {}
    # 预计算模块内末段重名集合：(module, 末段snake) → 出现次数 ≥2 需带前缀
    last_counter = {}
    for method, path in by_key:
        segs = [s for s in path.split("/") if s]
        if segs:
            k = (segs[0], to_snake(segs[-1]))
            last_counter[k] = last_counter.get(k, 0) + 1
    dup_last = {k for k, n in last_counter.items() if n >= 2}
    for (method, path), req in sorted(by_key.items(), key=lambda kv: kv[0]):
        module = path.strip("/").split("/")[0]
        if not module:                                  # 根路径等异常请求跳过
            continue
        used = used_names.setdefault(module, set())
        name = make_name(path, module, dup_last, used)
        key = f"{module}.{name}"
        payload, hint = build_payload(req)
        if hint:
            lines_by_module.setdefault(module, []).append(
                f"# TODO: {key} | {method} | {path}   ← {hint}")
        else:
            # 有参数带 payload（GET=query 串，POST/PUT=body），无参数只写三段
            line = f"{key} | {method} | {path} | {payload}" if payload \
                else f"{key} | {method} | {path}"
            lines_by_module.setdefault(module, []).append(line)

    modules = sorted(lines_by_module)
    header = (f"# {TODAY} 由 plugins/casegen/har2list.py 自动生成（来源: {har_path.name}）\n"
              f"# 请人工审阅：删除不想测的接口行（文件上传/导出/危险写接口），\n"
              f"# 再交给 gen_cases.py 生成用例: python -m plugins.casegen.gen_cases --list 本文件\n")
    text = header + "\n" + "\n".join(
        line for mod in modules for line in lines_by_module[mod]) + "\n"
    return text, stats


def _parse_args():
    parser = argparse.ArgumentParser(
        description="HAR → 接口清单转换器：F12 一次导出，批量生成 gen_cases 清单")
    parser.add_argument("--har", required=True, help="HAR 文件路径（F12 → Save all as HAR）")
    parser.add_argument("--env", default="dev", help="读取 config/<env>.yaml 的 base_url，默认 dev")
    parser.add_argument("--base-url", default=None,
                        help="直接指定 base_url（优先级高于 --env 配置）")
    parser.add_argument("--out", default="plugins/casegen/case_list_from_har.txt",
                        help="清单输出路径（默认 plugins/casegen/case_list_from_har.txt）")
    return parser.parse_args()


def main():
    args = _parse_args()
    base_url = args.base_url or load_config(args.env).get("base_url")
    if not base_url:
        raise ValueError(f"未取得 base_url：请在 config/{args.env}.yaml 配置或用 --base-url 指定")
    base_url = base_url.rstrip("/")

    har_path = Path(args.har)
    har_path = har_path if har_path.is_absolute() else PROJECT_ROOT / har_path
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else PROJECT_ROOT / out_path

    text, stats = convert(har_path, base_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    logger.info("HAR 转换完成 → %s", out_path.relative_to(PROJECT_ROOT))
    print(f"  总请求 {stats['total']} 条 | 保留 {stats['kept']} 条 | "
          f"非 base_url 前缀/其他方法过滤 {stats['external']} 条 | "
          f"静态资源过滤 {stats['static']} 条 | 登录接口排除 {stats['login']} 条")
    print("下一步：人工审阅清单后执行  python -m plugins.casegen.gen_cases --list "
          + str(out_path.relative_to(PROJECT_ROOT)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError) as e:
        logger.error("[ERROR] %s", e)
        sys.exit(1)

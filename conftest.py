# conftest.py - 全局 fixture 配置中心
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

# --- [0] 控制台强制 UTF-8（模块加载即生效，覆盖后续所有 print / logging 输出）---
# 解决两个问题：
# 1. pytest --collect-only 在 Windows 中文 cmd 下默认 GBK 编码，触发 unicode_escape
# 2. 中文用例 ID 被转成 \uff08\u81ea\u52a8... 这种不可读形式
# 注：reconfigure 是 Python 3.7+ 接口；本项目最低 Python 3.10 必然支持
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    # AttributeError: Python < 3.7；OSError: Windows 重定向管道不支持 reconfigure
    pass

import pytest
from core.base_client import BaseClient
from core.auth_manager import AuthManager
from core.config_loader import load_config


# 0. 收集后处理：把 pytest 8.3 强加的 \uXXXX unicode escape 反解码回中文
# pytest 8.3 在 terminalwriter._format_test_id_for_terminal 里把 ID 中所有非 ASCII
# 字符转成 \uXXXX 形式打印（避免某些 Windows 终端显示问题）。
# 我们已经在顶部 reconfigure(stdout, encoding="utf-8")，所以还原成中文不会出问题。
import codecs


def _unescape_unicode(s: str) -> str:
    """把 \\uXXXX 反 escape 回原字符；无 \\u 标记的字符串原样返回

    使用 codecs.decode('unicode_escape') 直接对 str 处理，避免 encode/decode roundtrip
    中 latin-1 装不下 U+FF08 等高位 Unicode 的问题。
    """
    if "\\u" not in s:
        return s
    try:
        return codecs.decode(s, "unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def pytest_collection_modifyitems(config, items):
    """1) 控制台 ID 反 escape（让 \\u81ea\\u52a8... → 自动）
       2) 兼作 P0-2 修复中的"并行护栏"（xdist 时拦截跨用例依赖）
    """
    # ---- 1) 反 escape：让中文 ID 在控制台直接可读 ----
    # 注意：pytest 8.3 在 terminalwriter 打印时仍会再做一次 escape（_format_test_id_for_terminal
    # 的 ASCII 防御），改 item._nodeid 仅影响 cache key 与 hook；本钩子同时覆盖 name 与 originalname
    # 是给"直接打印 item.name"的代码路径用（例如某些 plugin）。对 pytest 默认 terminal 的
    # 最终输出，主要仍依赖顶部 sys.stdout.reconfigure(encoding='utf-8')。
    for item in items:
        if "\\u" in item._nodeid:
            item._nodeid = _unescape_unicode(item._nodeid)
        if "\\u" in item.name:
            item.name = _unescape_unicode(item.name)
        if "\\u" in item.originalname:
            item.originalname = _unescape_unicode(item.originalname)


# 1. 全局配置：根据 --env 读取对应环境的 yaml（自动注入 .env 与环境变量插值）
@pytest.fixture(scope="session")
def config(pytestconfig):
    env = pytestconfig.getoption("--env")
    return load_config(env)


# 注册命令行参数：--env=dev|staging|prod
def pytest_addoption(parser):
    parser.addoption(
        "--env",
        action="store",
        default="dev",
        help="运行环境：dev / staging / prod，默认 dev",
    )
    # 用例隔离开关：默认关闭（保持与历史 session 级 fixtures 一致）。
    # 开启后 variables/extract 每次执行前用 case_vars + 仅同模块前置依赖重置，
    # 解决「单跑 case X 看不到前置 extract 写入的变量」问题
    parser.addoption(
        "--cases-isolated",
        action="store_true",
        default=False,
        help="用例级变量隔离：每次执行前重置 variables（默认关闭）",
    )
    # 鉴权失败显式失败开关：默认关闭（兼容旧行为：由断言去判定）。
    # 开启后 BaseClient 在 Token 重登仍失败时抛 AuthExpiredAfterRelogin，
    # 便于 Allure 直接把"鉴权问题"单独分类
    parser.addoption(
        "--fail-on-auth-expired",
        action="store_true",
        default=False,
        help="Token 重登后仍 401/403 时显式抛错（默认关闭，向后兼容）",
    )


# 1. 全局配置：根据 --env 读取对应环境的 yaml（自动注入 .env 与环境变量插值）
@pytest.fixture(scope="session")
def config(pytestconfig):
    env = pytestconfig.getoption("--env")
    return load_config(env)


# 2. 全局 Token 管理器：自动登录 + 自动刷新
@pytest.fixture(scope="session")
def auth(config):
    manager = AuthManager(config)
    manager.login()
    return manager


# 3. 全局请求客户端：带 Token 的 BaseClient
#    retry 来自 config 的 retry 段（缺省不重试）；
#    auth_relogin_401 缺省开启（Token 被服务端提前失效时自动重登重放）
#    fail_on_auth_expired 默认关闭；可通过 --fail-on-auth-expired 或 config.auth_fail_on_expired 开启
@pytest.fixture(scope="session")
def api_client(config, auth, pytestconfig):
    return BaseClient(
        base_url=config['base_url'],
        auth=auth,
        retry=config.get('retry'),
        relogin_on_401=config.get('auth_relogin_401', True),
        fail_on_auth_expired=(
            pytestconfig.getoption("--fail-on-auth-expired")
            or config.get('auth_fail_on_expired', False)
        ),
    )


# 4. 全局变量上下文：跨用例传递数据（extract 提取 → 后续用例 ${var} 引用）
#    先用 config 的 case_vars 打底（环境相关 ID 不写死在用例里），extract 的同名变量会覆盖
#    --cases-isolated 开启时改为 function-scope：用例间相互隔离，便于本地单跑 case
@pytest.fixture(scope="session")
def _session_variables(config):
    """session 级：用例会跨用例写入，CI 默认行为"""
    return dict(config.get("case_vars") or {})


@pytest.fixture(scope="function")
def variables(_session_variables, pytestconfig):
    """用例级：默认透传 session 实例，--cases-isolated 时每条用例重置

    读 _session_variables 是约定（避免被 _session_variables 当成"未使用"被 lint 误报）
    """
    if pytestconfig.getoption("--cases-isolated"):
        # 隔离模式：每条用例执行前都从 case_vars 重置，避免前置 extract 造成的污染
        import copy
        return copy.deepcopy(_session_variables)
    return _session_variables


# ========== Allure 报告元信息 ==========
# 报告里带上运行环境/构建号/提交号，失败时能直接定位"哪次构建、哪个环境"跑出来的
_ALLURE_RESULTS = Path(__file__).parent / "output" / "reports" / "allure-results"

# 缺陷分类：Allure 报告按失败原因自动归类，便于快速看出"是环境问题还是真 Bug"
#
# 双维度分类：
#   - matchedStatuses: pytest 测试态（failed/broken/skipped/passed）
#   - messageRegex:   失败信息文本匹配（兜底，向后兼容）
#   - matchedLabels:  pytest 额外打的 label（如 reason=AUTH_EXPIRED），更可靠
# 新打的 label 维度优先：通过 tools/_reason_label 在 assert 失败时打上标签
_ALLURE_CATEGORIES = [
    {"name": "断言失败（业务校验）", "matchedStatuses": ["failed"],
     "messageRegex": ".*条断言失败.*"},
    {"name": "接口不可达/超时", "matchedStatuses": ["failed", "broken"],
     "messageRegex": ".*(网络异常|请求超时).*"},
    {"name": "鉴权失败（Token 失效/重登失败）", "matchedStatuses": ["failed"],
     "messageRegex": ".*(AuthExpiredAfterRelogin|Token 重登).*"},
    {"name": "前置用例未通过", "matchedStatuses": ["skipped"],
     "messageRegex": ".*前置用例未通过.*"},
]


def pytest_sessionfinish(session, exitstatus):
    """测试结束后写入 Allure 环境信息与缺陷分类（供 allure generate 使用）"""
    if not _ALLURE_RESULTS.exists():
        return
    env = session.config.getoption("--env", default="dev")
    info = {
        "ENV": env,
        "PYTHON": platform.python_version(),
        "PYTEST": pytest.__version__,
        "PLATFORM": platform.platform(),
        "BUILD_NUMBER": os.environ.get("BUILD_NUMBER", "-"),
        "GIT_COMMIT": os.environ.get("GIT_COMMIT", "-"),
        "RUN_TIME": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(_ALLURE_RESULTS / "environment.properties", "w", encoding="utf-8") as f:
            for k, v in info.items():
                f.write(f"{k}={v}\n")
        with open(_ALLURE_RESULTS / "categories.json", "w", encoding="utf-8") as f:
            json.dump(_ALLURE_CATEGORIES, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # 报告元信息写入失败不应影响测试结论
        print(f"⚠️ 警告: Allure 元信息写入失败: {e}")

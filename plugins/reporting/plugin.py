# plugins/reporting/plugin.py - 报告积木的 pytest 插件实现
#
# 职责：
#   1. 实现 core/reporting.py 的上报端口（step / attach / extra_text）
#   2. 注入报告输出路径与 Allure 元信息（核心 pytest.ini 不再含任何报告参数）
#
# 加载：pytest.ini addopts 中的 `-p plugins.reporting.plugin`
import json
import os
import platform
from datetime import datetime
from contextlib import nullcontext
from pathlib import Path

import pytest

from core import reporting

# ---- 可选依赖：缺失时对应能力自动降级，不影响测试执行 ----
try:
    import allure
    from allure_commons.types import AttachmentType
except ImportError:  # 未安装 allure-pytest
    allure = None
    AttachmentType = None

try:
    from pytest_html import extras as html_extras
except ImportError:  # 未安装 pytest-html
    html_extras = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 输出根目录可通过 APITEST_OUTPUT_DIR 覆盖（默认项目根目录 output/）
OUTPUT_ROOT = Path(os.getenv("APITEST_OUTPUT_DIR", PROJECT_ROOT / "output"))
REPORT_DIR = OUTPUT_ROOT / "reports"
ALLURE_RESULTS = REPORT_DIR / "allure-results"

# 缺陷分类：Allure 报告按失败原因自动归类，便于快速看出"是环境问题还是真 Bug"
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


class Reporter:
    """上报实现：step/attach 走 allure；断言明细同时附到 pytest-html 报告"""

    def __init__(self):
        self._extras = []

    def step(self, title):
        return nullcontext() if allure is None else allure.step(title)

    def attach(self, content, name=None):
        if allure is None:
            return
        allure.attach(content, name=name, attachment_type=AttachmentType.TEXT)

    def extra_text(self, content, name=None):
        if html_extras is None:
            return
        self._extras.append(html_extras.text(content, name=name))

    def drain_extras(self):
        """取走并清空当前用例待附明细"""
        extras, self._extras = self._extras, []
        return extras


_reporter = Reporter()
reporting.set_reporter(_reporter)


# ========== 1. 注入报告输出路径与运行元信息 ==========
# tryfirst 保证在 pytest-html / allure 各自的 pytest_configure 之前写入选项，
# 它们才能读到我们设的默认值并真正产出报告。
@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if html_extras is not None and not getattr(config.option, "htmlpath", None):
        config.option.htmlpath = str(REPORT_DIR / "report.html")
        config.option.self_contained_html = True
    if allure is not None and not getattr(config.option, "allure_report_dir", None):
        config.option.allure_report_dir = str(ALLURE_RESULTS)
        config.option.clean_alluredir = True


# ========== 2. 断言明细附到 pytest-html 报告 ==========
# 用 wrapper 形式（非 tryfirst）执行在 pytest_html 的 makereport 内部，
# 它随后读取 report.extras 时即可拿到我们写入的明细。
def pytest_runtest_setup(item):
    _reporter.drain_extras()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.when == "call":
        extras = _reporter.drain_extras()
        if extras:
            report.extras = list(getattr(report, "extras", []) or []) + extras
    return report


# ========== 3. Allure 元信息与缺陷分类 ==========
def pytest_sessionfinish(session, exitstatus):
    """测试结束后写入 Allure 环境信息与缺陷分类（供 allure generate 使用）"""
    if allure is None:
        return
    results_dir = Path(session.config.option.allure_report_dir or ALLURE_RESULTS)
    if not results_dir.exists():
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
        with open(results_dir / "environment.properties", "w", encoding="utf-8") as f:
            for k, v in info.items():
                f.write(f"{k}={v}\n")
        with open(results_dir / "categories.json", "w", encoding="utf-8") as f:
            json.dump(_ALLURE_CATEGORIES, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # 报告元信息写入失败不应影响测试结论
        print(f"⚠️ 警告: Allure 元信息写入失败: {e}")

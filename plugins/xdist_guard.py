# plugins/xdist_guard.py - 并行护栏积木
#
# 依赖用例（extract / depends_on）依赖同进程内的变量与状态表，
# xdist 多进程下 worker 之间不共享，会"静默跑出错结果"，因此收集期直接拦截。
#
# 嵌入方式：pytest.ini addopts 中的 `-p plugins.xdist_guard`
# 拔出方式：删掉该 -p 参数（本文件可删除）。注意：拔出后并行跑跨用例依赖会失去保护。
import pytest

from core import case_extras


def pytest_collection_modifyitems(config, items):
    """仅当启用 xdist 多进程时校验；未安装 xdist 时自动失效"""
    if not config.getoption("numprocesses", None):
        return
    dist = config.getoption("dist", "no")
    if dist == "loadfile":
        # 同文件同进程：仅当依赖关系不跨文件时才安全（无法在此精确判定，交人工确认）
        return

    dependent = []
    for item in items:
        callspec = getattr(item, "callspec", None)
        case = (callspec.params or {}).get("case") if callspec else None
        # 是否含跨用例依赖由跨用例积木判定（拔出该积木后恒为 False）
        if case is not None and case_extras.has_cross_deps(case):
            dependent.append(case.id)
    if not dependent:
        return

    raise RuntimeError(
        f"检测到 xdist 并行模式（--dist {dist}），但以下用例存在跨用例依赖"
        f"（extract/depends_on）: {dependent}。"
        f"请去掉 -n 参数串行执行；确认依赖均在同一 YAML 文件内时，"
        f"可改用 -n auto --dist loadfile（保证同文件同进程）。"
    )

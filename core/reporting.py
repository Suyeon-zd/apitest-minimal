# core/reporting.py - 报告上报端口（核心侧只定义接口，实现由报告积木注册）
#
# 核心代码不直接 import allure / pytest-html，统一走本端口：
#   - 嵌入报告积木（plugins/reporting）时，积木加载即注册真实实现；
#   - 未嵌入时全部降级为空操作，框架照常运行（报告能力被"拔出"）。
from contextlib import nullcontext


class _NullReporter:
    """默认实现：什么也不做"""

    def step(self, title):
        return nullcontext()

    def attach(self, content, name=None):
        pass

    def extra_text(self, content, name=None):
        pass


_reporter = _NullReporter()


def set_reporter(impl):
    """报告积木加载时注册实现；传 None 表示卸载回空操作"""
    global _reporter
    _reporter = impl or _NullReporter()


def step(title):
    """上报一个步骤，配合 with 使用：with reporting.step("接口请求"):"""
    return _reporter.step(title)


def attach(content, name=None):
    """上报一段文本明细（如断言结果、变量提取）"""
    _reporter.attach(content, name)


def extra_text(content, name=None):
    """上报一段文本明细到 pytest-html 报告（未嵌入报告积木时忽略）"""
    _reporter.extra_text(content, name)

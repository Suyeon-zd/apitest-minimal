# plugins/terminal_cn/plugin.py - 终端中文 ID 显示的 pytest 插件实现
#
# 从 conftest.py 迁出：模块加载即 reconfigure stdout/stderr 为 UTF-8，
# 并在收集后把 pytest 8.3 强加的 \uXXXX unicode escape 反解码回中文。
#
# 加载：pytest.ini addopts 中的 `-p plugins.terminal_cn.plugin`
import codecs
import sys


# --- 控制台强制 UTF-8（模块加载即生效，早于用例收集）---
# 注：reconfigure 是 Python 3.7+ 接口；本项目最低 Python 3.10 必然支持
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    # AttributeError: Python < 3.7；OSError: Windows 重定向管道不支持 reconfigure
    pass


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
    """控制台 ID 反 escape（让 \\u81ea\\u52a8... → 自动）

    注意：pytest 8.3 在 terminalwriter 打印时仍会再做一次 escape，改 item._nodeid 仅影响
    cache key 与 hook；这里同时覆盖 name 与 originalname 供"直接打印 item.name"的代码路径用。
    对 pytest 默认 terminal 的最终输出，主要仍依赖上方 sys.stdout.reconfigure(encoding='utf-8')。
    """
    for item in items:
        if "\\u" in item._nodeid:
            item._nodeid = _unescape_unicode(item._nodeid)
        if "\\u" in item.name:
            item.name = _unescape_unicode(item.name)
        if "\\u" in item.originalname:
            item.originalname = _unescape_unicode(item.originalname)

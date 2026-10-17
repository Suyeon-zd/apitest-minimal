# core/http_heal.py - HTTP 自愈/重试端口（核心侧只定义接口，实现由 http_heal 积木注册）
#
# "HTTP 自愈"能力：超时/连接类异常退避重试、收到 401/403 自动重登并重放、
# 重登后仍失败时显式抛鉴权错误。这些都不是核心骨架的必需能力，统一走本端口：
#   - 嵌入积木（plugins/http_heal）时，积木加载即注册真实实现（并从 config 读取配置）；
#   - 未嵌入时全部降级为空操作，BaseClient 即"裸请求"（一次发送，异常原样抛出）。
from typing import Any, Callable, Dict


class _NullHeal:
    """默认实现：不做任何自愈/重试"""

    def configure(self, client: Any, config: Any) -> None:
        """让积木把自愈/重试配置挂载到 client 上（未嵌入时无操作）"""

    def request(self, client: Any, send: Callable, method: str, path: str,
                kwargs: Dict[str, Any]) -> Any:
        """包装单次发送：默认直接发一次（send 为 client._send）"""
        return send(method, path, **kwargs)


_heal = _NullHeal()


def set_heal(impl) -> None:
    """积木加载时注册实现；传 None 表示卸载回空操作"""
    global _heal
    _heal = impl or _NullHeal()


def configure(client: Any, config: Any) -> None:
    """把自愈/重试配置挂载到客户端（BaseClient 构造时调用）"""
    _heal.configure(client, config)


def request(client: Any, send: Callable, method: str, path: str, kwargs: Dict[str, Any]) -> Any:
    """发送请求（BaseClient.request 调用）；默认裸发一次"""
    return _heal.request(client, send, method, path, kwargs)

# plugins/http_heal/plugin.py - HTTP 自愈/重试积木的 pytest 插件实现
#
# 实现 core/http_heal.py 端口：
#   - 超时/连接类异常按 retry 配置退避重试
#   - 收到 401/403 自动重新登录并重放一次（Token 被服务端提前失效时自愈）
#   - 重登后仍失败且开启 fail_on_auth_expired 时抛 AuthExpiredAfterRelogin
#
# 加载：pytest.ini addopts 中的 `-p plugins.http_heal.plugin`
import logging
import time

import requests

from core import http_heal
from core.base_client import _classify

logger = logging.getLogger(__name__)

# 默认参与重试的类别（对应 config 里 retry.retry_on 的取值）
# 注意：YAML 1.1 会把裸键 on/off/yes/no 解析成布尔值，故这里用 retry_on 而非 on
_RETRYABLE = ("timeout", "connection")

# 401 自愈累计计数（per-process，避免隐藏性"连 401"导致断言误判业务错误）
# 用单调计数即可：本进程内串行/并发对结果展示都足够；不持久化
_AUTH_RELOGIN_COUNT = 0

# 命令行开关 --fail-on-auth-expired，pytest_configure 时读取
_FAIL_ON_AUTH_EXPIRED = False


class AuthExpiredAfterRelogin(RuntimeError):
    """Token 重登后仍返回 401/403：明确告诉调用方「这是鉴权问题，不是业务问题」

    默认不抛出：保留向后兼容（业务侧想明确鉴权失败语义时可显式捕获）。
    启用方式：命令行 --fail-on-auth-expired，或 config 配 `auth_fail_on_expired: true`
    """


def _is_retryable(exc, retry_on) -> bool:
    """按 retry.retry_on 判定该异常是否值得重试（仅超时/连接类，业务 4xx/5xx 不在此列）"""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout" in retry_on
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection" in retry_on
    return False


class HttpHeal:
    """HTTP 自愈实现：重试退避 + 401 重登重放 + 鉴权失败显式抛错"""

    # ---- 构造期：把自愈/重试配置挂载到 client 上 ----
    def configure(self, client, config) -> None:
        config = config or {}
        retry = config.get("retry")
        client.retry = {"times": 0, "backoff": 0.5, "retry_on": list(_RETRYABLE)}
        if retry:
            client.retry.update(retry)
        client.relogin_on_401 = config.get("auth_relogin_401", True)
        client.fail_on_auth_expired = bool(
            _FAIL_ON_AUTH_EXPIRED or config.get("auth_fail_on_expired", False)
        )

    # ---- 运行期：包裹单次发送，做重试与 401 自愈 ----
    def request(self, client, send, method, path, kwargs):
        global _AUTH_RELOGIN_COUNT
        response = self._send_with_retry(client, send, method, path, kwargs)
        if client.relogin_on_401 and client.auth and response.status_code in (401, 403):
            _AUTH_RELOGIN_COUNT += 1
            logger.warning(
                "! 收到 %d (Token 可能已失效)，重新登录后重放一次: %s %s "
                "[本进程累计自愈次数=%d]",
                response.status_code, method, path, _AUTH_RELOGIN_COUNT,
            )
            client.auth.login()
            response = self._send_with_retry(client, send, method, path, kwargs)
            if client.fail_on_auth_expired and response.status_code in (401, 403):
                # 让"鉴权问题"被显式识别，避免被业务断言静默吞掉
                raise AuthExpiredAfterRelogin(
                    f"Token 重登后仍返回 {response.status_code}: "
                    f"{method} {client.base_url}{path}；请检查登录凭据与 .env 配置"
                )
        return response

    def _send_with_retry(self, client, send, method, path, kwargs):
        """按 retry 配置对超时/连接类异常做退避重试，其余异常原样抛出"""
        attempts = int(client.retry.get("times", 0) or 0) + 1
        backoff = float(client.retry.get("backoff", 0.5) or 0)
        retry_on = {str(x).lower() for x in (client.retry.get("retry_on") or _RETRYABLE)}
        url = f"{client.base_url}{path}"
        for attempt in range(1, attempts + 1):
            try:
                return send(method, path, **kwargs)
            except requests.exceptions.RequestException as e:
                if attempt < attempts and _is_retryable(e, retry_on):
                    wait = backoff * attempt
                    logger.warning(
                        "! 请求失败(%s)，%.1fs 后进行第 %d/%d 次重试: %s %s -> %s",
                        _classify(e), wait, attempt, attempts - 1, method, url, e,
                    )
                    time.sleep(wait)
                    continue
                raise


# ========== pytest 钩子：注册鉴权失败开关 ==========
def pytest_addoption(parser):
    parser.addoption(
        "--fail-on-auth-expired",
        action="store_true",
        default=False,
        help="Token 重登后仍 401/403 时显式抛错（默认关闭，向后兼容）",
    )


def pytest_configure(config):
    global _FAIL_ON_AUTH_EXPIRED
    _FAIL_ON_AUTH_EXPIRED = bool(config.getoption("--fail-on-auth-expired"))


http_heal.set_heal(HttpHeal())

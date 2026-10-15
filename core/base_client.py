# core/base_client.py - HTTP 请求封装基类
import json
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

_MAX_LOG = 800   # 日志里单个字段的最大长度（防止 token/大 body 刷屏）

# ---- 401 自愈累计计数（per-process，避免隐藏性"连 401"导致断言误判业务错误）----
# 用 thread-safe 单调计数即可：本进程内串行/并发对结果展示都足够；不持久化
_AUTH_RELOGIN_COUNT = 0


class AuthExpiredAfterRelogin(RuntimeError):
    """Token 重登后仍返回 401/403：明确告诉调用方「这是鉴权问题，不是业务问题」

    默认不抛出：保留向后兼容（业务侧想明确鉴权失败语义时可显式捕获）
    启用方式：构造 BaseClient 时把 fail_on_auth_expired=True，或 config 配
    `auth_relogin_401: true` + `auth_fail_on_expired: true`
    """

# ---- 异常分类（细到类别，便于日志定位与按类别重试）----
# 注意顺序：子类必须排在父类前面（如 SSLError/ProxyError 是 ConnectionError 的子类）
_ERROR_CATEGORY = (
    (requests.exceptions.ConnectTimeout, "超时-连接建立"),
    (requests.exceptions.ReadTimeout, "超时-读取响应"),
    (requests.exceptions.Timeout, "超时"),
    (requests.exceptions.SSLError, "SSL 握手失败"),
    (requests.exceptions.ProxyError, "代理错误"),
    (requests.exceptions.TooManyRedirects, "重定向次数过多"),
    (requests.exceptions.ConnectionError, "连接错误"),
    (requests.exceptions.RequestException, "请求异常"),
)

# 默认参与重试的类别（对应 config 里 retry.on 的取值）
_RETRYABLE = ("timeout", "connection")

# 脱敏字段：请求头里的鉴权项 + 响应体里的 token/密码类字段
_SENSITIVE_HEADERS = ("authorization", "clientid", "encrypt-key", "cookie")
_SENSITIVE_BODY_PATTERN = re.compile(
    r'("(?:access_token|refresh_token|token|password|passwd|secret|'
    r'client_secret|authorization|encrypt_key|encrypt-key)"\s*:\s*")([^"]*)(")',
    re.IGNORECASE,
)


def _classify(exc) -> str:
    """把 requests 异常归纳为稳定的中文类别（用于日志与重试判定）"""
    for exc_type, label in _ERROR_CATEGORY:
        if isinstance(exc, exc_type):
            return label
    return "未知异常"


def _is_retryable(exc, retry_on) -> bool:
    """按 retry.on 判定该异常是否值得重试（仅超时/连接类，业务 4xx/5xx 不在此列）"""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout" in retry_on
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection" in retry_on
    return False


def _short(obj, limit=_MAX_LOG):
    """把任意对象截断为日志友好字符串"""
    if obj is None:
        return "None"
    if isinstance(obj, (dict, list)):
        s = json.dumps(obj, ensure_ascii=False, default=str)
    else:
        s = str(obj)
    if len(s) <= limit:
        return s
    return f"{s[:limit]}...(共{len(s)}字符，已截断)"


def _mask(v):
    """脱敏单个值：只保留首尾少量字符，中间用 *** 代替

    长度 ≤ 8 时整体替换为 ***（避免短 token/短密钥被完整还原）。
    """
    if not isinstance(v, str) or not v:
        return v
    if len(v) <= 8:
        return "***"
    return f"{v[:4]}***{v[-4:]}"


def _mask_body(text, limit=_MAX_LOG):
    """响应体脱敏后再落日志

    登录/刷新接口的响应体里带 access_token，原样写日志（并随 CI 归档）等于把
    有效凭证泄漏到磁盘；这里按字段名做正则替换，只保留首尾少量字符。
    """
    if not text:
        return text
    masked = _SENSITIVE_BODY_PATTERN.sub(
        lambda m: f"{m.group(1)}{_mask(m.group(2))}{m.group(3)}", text
    )
    return _short(masked, limit)


class BaseClient:
    def __init__(self, base_url, auth=None, retry=None, relogin_on_401=True,
                 fail_on_auth_expired=False):
        """
        :param base_url: 服务根地址
        :param auth: AuthManager 实例（提供 get_token() / login()）
        :param retry: 重试配置字典，取自 config 的 retry 段，可选：
                      {times: 2, backoff: 0.5, on: [timeout, connection]}
                      缺省 times=0 即不重试，保持与历史行为一致
        :param relogin_on_401: 收到 401 时是否强制重新登录并重放一次请求
                      （Token 被服务端提前失效时自愈，避免后续用例全线 401）
        :param fail_on_auth_expired: 重登后仍返回 401/403 时是否抛
                      AuthExpiredAfterRelogin（默认 False：保持旧行为，
                      也就是返回 Response 让断言去判；想明确区分鉴权/业务问题时
                      设 True 让 BaseClient 主动抛错）
        """
        self.base_url = base_url
        self.auth = auth
        self.session = requests.Session()
        self.retry = {"times": 0, "backoff": 0.5, "on": list(_RETRYABLE)}
        if retry:
            self.retry.update(retry)
        self.relogin_on_401 = relogin_on_401
        self.fail_on_auth_expired = fail_on_auth_expired

    def request(self, method, path, **kwargs):
        """发送请求；401 且配置了 auth 时强制重登并重放一次

        重登后若仍 401/403：默认返回 Response（保持向后兼容，断言仍能 fail），
        当 fail_on_auth_expired=True 时抛 AuthExpiredAfterRelogin 让业务方
        能明确区分"鉴权问题 vs 业务问题"。
        """
        global _AUTH_RELOGIN_COUNT
        response = self._send(method, path, **kwargs)
        if self.relogin_on_401 and self.auth and response.status_code in (401, 403):
            _AUTH_RELOGIN_COUNT += 1
            logger.warning(
                "! 收到 %d (Token 可能已失效)，重新登录后重放一次: %s %s "
                "[本进程累计自愈次数=%d]",
                response.status_code, method, path, _AUTH_RELOGIN_COUNT,
            )
            self.auth.login()
            response = self._send(method, path, **kwargs)
            if self.fail_on_auth_expired and response.status_code in (401, 403):
                # 让"鉴权问题"被显式识别，避免被业务断言静默吞掉
                raise AuthExpiredAfterRelogin(
                    f"Token 重登后仍返回 {response.status_code}: "
                    f"{method} {self.base_url}{path}；请检查登录凭据与 .env 配置"
                )
        return response

    def _send(self, method, path, **kwargs):
        """单次发送：注入 Token → 记请求日志 → 退避重试 → 记响应日志（脱敏）"""
        url = f"{self.base_url}{path}"

        # 每次请求从 AuthManager 获取最新 token（支持自动刷新）
        if self.auth:
            headers_update = {"Authorization": f"Bearer {self.auth.get_token()}"}
            clientid = self.auth.config.get("clientid")
            if clientid:
                headers_update["clientid"] = clientid
            self.session.headers.update(headers_update)

        # ---- 请求日志（合并 session 头 + 本次请求头，本次优先；敏感头脱敏） ----
        merged_headers = {**dict(self.session.headers), **(kwargs.get("headers") or {})}
        req_body = kwargs.get("json") or kwargs.get("data") or kwargs.get("params")
        merged_headers = {
            k: (_mask(v) if k.lower() in _SENSITIVE_HEADERS else v)
            for k, v in merged_headers.items()
        }
        logger.info(
            "→ 请求: %s %s\n  headers: %s\n  body: %s",
            method, url, _short(merged_headers), _short(req_body),
        )

        # ---- 发送请求：按 retry 配置对超时/连接类异常做退避重试 ----
        attempts = int(self.retry.get("times", 0) or 0) + 1
        backoff = float(self.retry.get("backoff", 0.5) or 0)
        retry_on = {str(x).lower() for x in (self.retry.get("on") or _RETRYABLE)}
        response = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(method, url, **kwargs)
                break
            except requests.exceptions.RequestException as e:
                category = _classify(e)
                if attempt < attempts and _is_retryable(e, retry_on):
                    wait = backoff * attempt
                    logger.warning(
                        "! 请求失败(%s)，%.1fs 后进行第 %d/%d 次重试: %s %s -> %s",
                        category, wait, attempt, attempts - 1, method, url, e,
                    )
                    time.sleep(wait)
                    continue
                logger.error("✗ 请求失败(%s): %s %s -> %s", category, method, url, e)
                raise

        # ---- 响应日志（响应体脱敏：token/密码类字段不落盘） ----
        logger.info(
            "← 响应: %s %s\n  headers: %s\n  body: %s",
            response.status_code, url,
            _short(dict(response.headers), 300),
            _mask_body(response.text),
        )
        return response

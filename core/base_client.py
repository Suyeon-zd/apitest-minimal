# core/base_client.py - HTTP 请求封装基类
#
# 核心只做"裸请求"：URL 拼接 + Token 注入 + 脱敏日志；
# 重试退避 / 401 自动重登 / 鉴权失败显式抛错属"HTTP 自愈"能力，走 core/http_heal.py 端口
# （由 plugins/http_heal 积木实现；未嵌入积木时即裸请求）。
import json
import logging
import re

import requests

from core import http_heal

logger = logging.getLogger(__name__)

_MAX_LOG = 800   # 日志里单个字段的最大长度（防止 token/大 body 刷屏）

# ---- 异常分类（细到类别，便于日志定位）----
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
    def __init__(self, base_url, auth=None, config=None):
        """
        :param base_url: 服务根地址
        :param auth: AuthManager 实例（提供 get_token() / login()）
        :param config: 环境配置字典；交给 http_heal 端口读取"自愈/重试"配置
                       （retry / auth_relogin_401 / auth_fail_on_expired）。未嵌入积木时被忽略。
        """
        self.base_url = base_url
        self.auth = auth
        self.session = requests.Session()
        # 自愈/重试配置由积木挂载（未嵌入积木时无该能力，即裸请求）
        http_heal.configure(self, config)

    def request(self, method, path, **kwargs):
        """发送请求；重试退避 / 401 重登等自愈行为由 http_heal 端口决定（默认裸发一次）"""
        return http_heal.request(self, self._send, method, path, kwargs)

    def _send(self, method, path, **kwargs):
        """单次发送：注入 Token → 记请求日志 → 发送 → 记响应日志（脱敏）"""
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

        # ---- 发送请求：异常分类后原样抛出，是否重试由上层 http_heal 端口决定 ----
        try:
            response = self.session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.error("✗ 请求失败(%s): %s %s -> %s", _classify(e), method, url, e)
            raise

        # ---- 响应日志（响应体脱敏：token/密码类字段不落盘） ----
        logger.info(
            "← 响应: %s %s\n  headers: %s\n  body: %s",
            response.status_code, url,
            _short(dict(response.headers), 300),
            _mask_body(response.text),
        )
        return response

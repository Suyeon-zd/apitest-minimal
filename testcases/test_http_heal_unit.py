# testcases/test_http_heal_unit.py - 「HTTP 自愈/重试」积木的回归测试
#
# 这些用例只针对 plugins/http_heal 积木（重试退避 / 401 自动重登 / 鉴权失败开关）。
# 积木未嵌入时（pytest.ini 的 addopts 去掉对应 -p）整文件自动跳过，
# 从而保证"拔出积木后核心测试仍全绿"。
#
# 说明：核心 BaseClient 只做裸请求，自愈配置通过 config=... 传给积木读取。
import pytest

# 积木未嵌入时整文件跳过（importorskip 在收集期生效）
pytest.importorskip("plugins.http_heal.plugin")

import requests

from core import base_client
from core.base_client import BaseClient
from plugins.http_heal.plugin import _is_retryable


# ========== 测试替身（本文件自包含，不依赖其它测试文件） ==========

class FakeResponse:
    """最小化的 requests.Response 替身"""

    def __init__(self, status_code=200, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = text


class FakeSession:
    """替换 requests.Session：按脚本依次返回响应或抛异常"""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.responses = []
        self.errors = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if self.errors:
            raise self.errors.pop(0)
        return self.responses.pop(0) if self.responses else FakeResponse()


class FakeAuth:
    """AuthManager 替身：只关心"是否被要求重新登录"""

    def __init__(self):
        self.config = {"clientid": "cid-001"}
        self.login_count = 1
        self.token = "token-1"

    def get_token(self):
        return self.token

    def login(self):
        self.login_count += 1
        self.token = f"token-{self.login_count}"
        return self.token


# ========== 自愈/重试积木回归 ==========

class TestHttpHeal:

    def _client(self, monkeypatch, session, auth=None, **config):
        monkeypatch.setattr(base_client.requests, "Session", lambda: session)
        return BaseClient("http://unit-test", auth=auth, config=config or None)

    def test_is_retryable_only_timeout_and_connection(self):
        assert _is_retryable(requests.exceptions.Timeout(), {"timeout"})
        assert not _is_retryable(requests.exceptions.Timeout(), {"connection"})
        assert _is_retryable(requests.exceptions.ConnectionError(), {"connection"})
        assert not _is_retryable(ValueError(), {"timeout", "connection"})

    def test_no_retry_by_default(self, monkeypatch):
        session = FakeSession()
        session.errors = [requests.exceptions.ConnectionError("boom")]
        client = self._client(monkeypatch, session)
        with pytest.raises(requests.exceptions.ConnectionError):
            client.request("GET", "/x")
        assert len(session.calls) == 1, "默认 retry.times=0，不应重试"

    def test_retry_on_connection_error(self, monkeypatch):
        session = FakeSession()
        session.errors = [requests.exceptions.ConnectionError("boom")]
        session.responses = [FakeResponse(200)]
        client = self._client(monkeypatch, session,
                              retry={"times": 1, "backoff": 0, "retry_on": ["connection"]})
        resp = client.request("GET", "/x")
        assert resp.status_code == 200
        assert len(session.calls) == 2, "连接类异常应重试一次"

    def test_no_retry_when_category_not_enabled(self, monkeypatch):
        session = FakeSession()
        session.errors = [requests.exceptions.Timeout("slow")]
        client = self._client(monkeypatch, session,
                              retry={"times": 2, "backoff": 0, "retry_on": ["connection"]})
        with pytest.raises(requests.exceptions.Timeout):
            client.request("GET", "/x")
        assert len(session.calls) == 1, "未开启 timeout 重试时不应重试"

    def test_relogin_and_replay_on_401(self, monkeypatch):
        session = FakeSession()
        session.responses = [FakeResponse(401), FakeResponse(200)]
        auth = FakeAuth()
        client = self._client(monkeypatch, session, auth=auth)

        resp = client.request("GET", "/x")

        assert resp.status_code == 200, "401 后重放应返回真实结果"
        assert auth.login_count == 2, "收到 401 必须重新登录一次"
        assert len(session.calls) == 2, "401 后应重放一次请求"
        assert session.headers["Authorization"] == "Bearer token-2", "重放需用新 Token"

    def test_relogin_disabled(self, monkeypatch):
        session = FakeSession()
        session.responses = [FakeResponse(401)]
        auth = FakeAuth()
        client = self._client(monkeypatch, session, auth=auth, auth_relogin_401=False)

        resp = client.request("GET", "/x")

        assert resp.status_code == 401
        assert auth.login_count == 1, "关闭开关后不应触发重登"
        assert len(session.calls) == 1

    def test_no_relogin_without_auth(self, monkeypatch):
        session = FakeSession()
        session.responses = [FakeResponse(401)]
        client = self._client(monkeypatch, session)
        assert client.request("GET", "/x").status_code == 401

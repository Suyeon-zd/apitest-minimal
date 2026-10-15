# core/auth_manager.py - Token 管理：自动登录 + 自动刷新
import time
import logging

from core.base_client import BaseClient

logger = logging.getLogger(__name__)


class AuthManager:
    """Token 管理器：整个测试期间只登录一次，快过期时自动刷新"""

    def __init__(self, config):
        self.config = config
        self._token = None
        self._login_time = 0
        # 用无 token 的裸 client 登录（登录接口本身不需要 token）
        self._login_client = BaseClient(base_url=config['base_url'])

    def login(self):
        """执行登录，缓存 token 和登录时间"""
        headers = {
            "clientid": self.config['clientid'],
            "encrypt-key": self.config['encrypt-key'],
            "Content-Type": self.config['Content-Type']
        }
        # 登录路径可在环境配置中通过 login_path 覆盖，默认 /auth/login
        login_path = self.config.get("login_path", "/auth/login")
        resp = self._login_client.request(
            "POST", login_path, data=self.config['payload'], headers=headers
        )
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError(f"登录失败: 响应不是 JSON (status={resp.status_code}, body={resp.text[:200]})")
        # token 位置兼容两种响应：{access_token: ...} 或 {data: {access_token: ...}}
        # （与 data/http_auth.py 的登录实现保持一致，避免同一后端两处登录行为不同）
        token = body.get("access_token")
        if not token and isinstance(body.get("data"), dict):
            token = body["data"].get("access_token")
        if not token:
            raise RuntimeError(f"登录失败: 响应中无 access_token (status={resp.status_code}, body={str(body)[:200]})")
        self._token = token
        self._login_time = time.time()
        logger.info(f"登录成功，Token 已缓存（{self._remain_seconds():.0f}秒后过期）")
        return self._token

    def get_token(self):
        """获取有效 token，快过期时自动刷新"""
        if self._token is None:
            return self.login()
        if self._need_refresh():
            logger.info("Token 即将过期，自动刷新...")
            self.login()
        return self._token

    def _need_refresh(self):
        """判断是否需要刷新：剩余时间 < 刷新缓冲时间"""
        remain = self._remain_seconds()
        buffer = self.config.get('token_refresh_buffer', 60)
        return remain < buffer

    def _remain_seconds(self):
        """计算 token 剩余有效秒数"""
        expire = self.config.get('token_expire_seconds', 1800)
        elapsed = time.time() - self._login_time
        return expire - elapsed

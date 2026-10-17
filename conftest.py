# conftest.py - 全局 fixture 配置中心
import pytest

from core import case_extras
from core.base_client import BaseClient
from core.auth_manager import AuthManager
from core.config_loader import load_config


# 注册命令行参数：--env=dev|staging|prod
def pytest_addoption(parser):
    parser.addoption(
        "--env",
        action="store",
        default="dev",
        help="运行环境：dev / staging / prod，默认 dev",
    )
    # 跨用例变量共享开关（--cases-shared）由跨用例积木（plugins/case_extras）注册；
    # 鉴权失败显式失败开关（--fail-on-auth-expired）由 HTTP 自愈积木（plugins/http_heal）注册。
    # 未嵌入对应积木时这些开关不存在，核心 fixture 也不需要读取它们。


# 1. 全局配置：根据 --env 读取对应环境的 yaml（自动注入 .env 与环境变量插值）
@pytest.fixture(scope="session")
def config(pytestconfig):
    env = pytestconfig.getoption("--env")
    return load_config(env)


# 2. 全局 Token 管理器：自动登录 + 自动刷新
@pytest.fixture(scope="session")
def auth(config):
    manager = AuthManager(config)
    manager.login()
    return manager


# 3. 全局请求客户端：带 Token 的 BaseClient
#    重试（config.retry）/ 401 自动重登（config.auth_relogin_401）/ 鉴权失败显式抛错
#    均由「HTTP 自愈」积木（plugins/http_heal）读取 config 后挂载；未嵌入积木时即裸请求
@pytest.fixture(scope="session")
def api_client(config, auth):
    return BaseClient(base_url=config['base_url'], auth=auth, config=config)


# 4. 全局变量上下文：跨用例传递数据（extract 提取 → 后续用例 ${var} 引用）
#    先用 config 的 case_vars 打底（环境相关 ID 不写死在用例里），extract 的同名变量会覆盖
#    默认每条用例独立（按用例深拷贝隔离），由跨用例积木的 --cases-shared 决定是否改为共享
@pytest.fixture(scope="session")
def _session_variables(config):
    """session 级：只存放 case_vars 打底值（默认被逐用例深拷贝，--cases-shared 时被直接写入）"""
    return dict(config.get("case_vars") or {})


@pytest.fixture(scope="function")
def variables(_session_variables):
    """用例级：默认按用例深拷贝隔离；启用 --cases-shared（跨用例积木）时共享同一实例

    具体隔离策略交给 core.case_extras 端口，未嵌入积木时原样返回 session 实例
    """
    return case_extras.prepare_variables(_session_variables)

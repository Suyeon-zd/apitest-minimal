# selftest/test_assets.py - 用例资产自检（离线：不登录、不发请求）
#
# 在真正跑业务用例之前，先确认「框架能不能用」中不依赖网络的部分：
#   1. 接口注册中心可加载（api/endpoints.yaml 可解析且非空）
#   2. 每个环境配置可加载且必填项齐全（config/*.yaml 的 base_url / timeout）
#   3. test_data/**/*.yaml 全部能转成 TestCase 并通过收集期校验
#      （api 已登记、api_name 唯一、断言非空且至少一条强断言）
#   4. schema 断言引用的文件确实存在于 test_data/schemas/
#   5. plugins.yaml 中启用的积木模块文件都存在
#
# 与业务执行入口共用 core/case_loader.load_cases，保证自检口径与真实加载一致。
import pytest
import yaml

from api.registry import EndpointRegistry
from core.assertions import SCHEMA_DIR
from core.case_loader import DEFAULT_DATA_DIR, load_cases
from core.config_loader import PROJECT_ROOT, load_config

CONFIG_DIR = PROJECT_ROOT / "config"
MANIFEST = PROJECT_ROOT / "plugins.yaml"


# ========== 1. 接口注册中心 ==========
def test_registry_loadable():
    """api/endpoints.yaml 可解析，且至少登记了一个接口"""
    registry = EndpointRegistry.load()
    assert registry.keys, "接口注册中心为空：api/endpoints.yaml 至少需要登记一个接口"


# ========== 2. 环境配置 ==========
@pytest.mark.parametrize(
    "config_file",
    sorted(CONFIG_DIR.glob("*.yaml")),
    ids=lambda p: p.stem,
)
def test_env_config_valid(config_file):
    """每个环境配置都能加载，且请求必需项齐全（凭据缺失不在此校验，属联网预检）"""
    config = load_config(config_file.stem)
    assert config.get("base_url"), f"{config_file.name} 缺少 base_url"
    assert config.get("timeout"), f"{config_file.name} 缺少 timeout（秒数或 [connect, read]）"


# ========== 3. 业务用例资产 ==========
def test_business_cases_loadable():
    """test_data 下全部 YAML 可加载并通过收集期校验（与 pytest 收集口径一致）"""
    cases = load_cases(DEFAULT_DATA_DIR, EndpointRegistry.load())
    assert cases, f"{DEFAULT_DATA_DIR} 下没有可执行用例（检查 *.yaml 与顶层列表结构）"


def test_schema_files_exist():
    """用例里 {type: schema, schema_file: xxx} 引用的文件必须存在"""
    cases = load_cases(DEFAULT_DATA_DIR, EndpointRegistry.load())
    missing = [
        f"{case.id} -> {rule.get('schema_file')}"
        for case in cases
        for rule in case.assertions
        if rule.get("type") == "schema"
        and not (SCHEMA_DIR / str(rule.get("schema_file"))).exists()
    ]
    assert not missing, "以下 schema 断言引用的文件不存在（应放在 test_data/schemas/）:\n" + "\n".join(missing)


# ========== 4. 积木清单 ==========
def _module_file(module_name: str):
    """把 a.b.c 映射为 a/b/c.py 或 a/b/c/__init__.py（只查文件，不 import）"""
    base = PROJECT_ROOT.joinpath(*module_name.split("."))
    py_file = base.with_suffix(".py")
    if py_file.exists():
        return py_file
    return base / "__init__.py"


def test_plugin_manifest_modules_exist():
    """plugins.yaml 中启用的积木必须存在对应模块文件；清单缺失视为纯核心模式"""
    if not MANIFEST.exists():
        pytest.skip("未找到 plugins.yaml，纯核心模式")
    plugins = (yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}).get("plugins") or []
    missing = [name for name in plugins if not _module_file(str(name)).exists()]
    assert not missing, f"以下积木在清单中启用但找不到模块文件: {missing}"

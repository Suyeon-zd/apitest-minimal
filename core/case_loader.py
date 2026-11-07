# core/case_loader.py - 用例资产加载与收集期校验
#
# 业务执行入口（testcases/test_regression.py）与离线自检（selftest/test_assets.py）共用，
# 保证「自检通过的资产」与「真正执行时加载的资产」完全一致，避免两套校验逻辑漂移。
#
# 只做加载与校验，不发请求、不登录：可在无网络环境下运行。
from pathlib import Path

import yaml

from core import case_extras
from core.case_model import TestCase

# 用例资产根目录（test_data/**/*.yaml）
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "test_data"


def validate_registry_refs(case: TestCase, registry) -> None:
    """校验用例引用的接口逻辑名已在注册中心登记

    拼写错误在 pytest --collect-only 阶段就暴露，不需要等到真正执行。
    （cleanup 步骤引用的接口由跨用例积木的 validate_cases 校验）
    """
    if case.api not in registry:
        raise ValueError(
            f"用例 [{case.id}]({case.source}) 引用的接口 '{case.api}' 未登记，"
            f"请在 api/endpoints.yaml 中登记；可用接口: {registry.keys}"
        )


def load_cases(data_dir: Path = None, registry=None) -> list:
    """扫描并校验全部用例 YAML，转成 TestCase 模型

    :param data_dir: 用例根目录，默认项目根目录下的 test_data/
    :param registry: 接口注册中心（EndpointRegistry），用于校验 api 引用
    """
    if registry is None:
        raise ValueError("load_cases 需要传入接口注册中心 registry")

    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    if not data_dir.exists():
        print(f"⚠️ 警告: 未找到用例目录 {data_dir}！")
        return []

    cases = []
    seen_ids = {}
    # sorted 保证跨文件收集顺序确定（文件路径排序），跨用例变量依赖（extract → ${var}）才可靠
    for yaml_file in sorted(data_dir.rglob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            raw_cases = yaml.safe_load(f)
        if not raw_cases:
            continue
        for raw in raw_cases:
            case = TestCase.from_dict(raw, source=yaml_file.name)
            # 用例 id 同时是 pytest 用例名与 depends_on 的引用键，必须全局唯一
            if case.id in seen_ids:
                raise ValueError(
                    f"用例 id 重复: '{case.id}'，同时出现在 "
                    f"{seen_ids[case.id]} 与 {yaml_file.name}；depends_on 无法区分引用目标"
                )
            seen_ids[case.id] = yaml_file.name
            validate_registry_refs(case, registry)
            cases.append(case)

    # 跨 yaml 引用校验（depends_on 命中 / extract 是否被 ${var} 引用）
    # 由跨用例积木实现；拔出积木后为空操作，不影响收集
    case_extras.validate_cases(cases, registry)
    return cases

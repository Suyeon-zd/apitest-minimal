# casegen 积木：用例自动生成工具链（非核心骨架，需要时从项目根目录用 -m 运行）
#   python -m plugins.casegen.har2list  --har xxx.har        # HAR → 接口清单
#   python -m plugins.casegen.gen_cases --list xxx.txt       # 清单 → 基线用例 + Schema
# 依赖 core/ 与 api/（登录、注册中心），与 pytest 运行时共享同一套配置。
#
# generated/ 目录是本积木此前在业务项目中产出的样例（基线用例 YAML + 契约 Schema）。
# 嵌入方式：把 generated/test_data/ 下的 YAML 拷回项目 test_data/ 对应模块目录、
#           generated/schemas/ 下的 gen_*.json 拷回项目 test_data/schemas/，即恢复自动用例。

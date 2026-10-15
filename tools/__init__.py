# tools 包：场景①（接口测试）的开发期工具链
# 入口统一用 python -m 运行：
#   python -m tools.har2list  --har xxx.har        # HAR → 接口清单
#   python -m tools.gen_cases --list xxx.txt       # 清单 → 基线用例 + Schema
# 依赖 core/ 与 api/（登录、注册中心），与 pytest 运行时共享同一套配置。

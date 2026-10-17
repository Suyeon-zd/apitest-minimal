# case_extras 积木：跨用例能力（depends_on / extract / cleanup）的可插拔实现
#
# 核心只定义端口（core/case_extras.py），本积木注册真实实现：
#   - 前置依赖门控（depends_on）：依赖用例未通过则跳过，避免级联误报
#   - 响应变量提取（extract）：响应字段写入全局变量，供后续用例 ${var} 引用
#   - 用例收尾（cleanup）：用例结束后回收脏数据，失败仅告警
#   - 配套：跨用例引用校验（收集期）、并行护栏判断（xdist_guard 使用）
#
# 嵌入方式：pytest.ini 的 addopts 中保留 `-p plugins.case_extras.plugin`
# 拔出方式：删掉该 -p 参数（本目录可整体删除），跨用例能力自动降级为空操作
#           （用例 YAML 里若仍写 depends_on/extract/cleanup 将被忽略，不再生效）

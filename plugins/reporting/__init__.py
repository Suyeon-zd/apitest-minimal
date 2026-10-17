# reporting 积木：测试报告（allure + pytest-html）的可插拔实现
#
# 核心只定义上报端口（core/reporting.py），本积木负责把真实实现注册进去，
# 并把报告输出路径（--html / --alluredir）注入默认值，使核心 pytest.ini 保持干净。
#
# 嵌入方式：pytest.ini 的 addopts 中保留 `-p plugins.reporting.plugin`
# 拔出方式：删掉该 -p 参数（本目录可整体删除），核心报告调用自动降级为空操作
#
# 依赖：allure-pytest（必要）、pytest-html（可选，缺失时仅少 HTML 报告中的明细块）

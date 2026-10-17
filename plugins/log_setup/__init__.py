# log_setup 积木：统一日志配置（标准库 logging，无额外依赖）
#
# 从 core/logger.py 迁出：配置 root logger（控制台 + output/logs/run.log 按天滚动）。
# 核心代码不再承担日志配置，只用 logging.getLogger(__name__) 产出日志。
#
# 嵌入方式：pytest.ini 的 addopts 中保留 `-p plugins.log_setup.plugin`
# 拔出方式：删掉该 -p 参数（本目录可整体删除），日志改由 pytest 自身 log_cli 输出，
#           不再写入 output/logs/run.log

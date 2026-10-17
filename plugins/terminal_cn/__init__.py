# terminal_cn 积木：终端中文 ID 友好显示（Windows 兼容 hack）
#
# 解决两个纯显示问题（不影响测试执行与结论）：
#   1. pytest --collect-only 在 Windows 中文 cmd 下默认 GBK 编码，输出会乱码/触发 unicode_escape
#   2. 中文用例 ID 被 pytest 8.3 转成 \uXXXX 形式，控制台不可读
#
# 嵌入方式：pytest.ini 的 addopts 中保留 `-p plugins.terminal_cn.plugin`
# 拔出方式：删掉该 -p 参数（本目录可整体删除），回到 pytest 默认的 ID 显示行为

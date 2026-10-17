# http_heal 积木：HTTP 自愈/重试（重试退避 + 401 自动重登 + 鉴权失败显式抛错）
#
# 核心 BaseClient 只做"裸请求"，本积木注册 core/http_heal.py 端口，
# 配置来源：环境配置的 retry / auth_relogin_401 / auth_fail_on_expired，
# 以及命令行开关 --fail-on-auth-expired。
#
# 嵌入方式：pytest.ini 的 addopts 中保留 `-p plugins.http_heal.plugin`
# 拔出方式：删掉该 -p 参数（本目录可整体删除），BaseClient 回到"发一次、异常原样抛出"

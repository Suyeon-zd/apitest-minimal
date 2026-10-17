# 接口测试框架使用手册

数据驱动（YAML 写用例）+ 接口注册中心（method/path 统一登记）+ 积木化（非核心能力按需插拔）的 pytest 接口测试框架。
写一条接口测试 = 登记接口 → 写一个 YAML → 跑 pytest，**不需要写 Python 代码**。

---

## 1. 当前框架结构

框架已精简为「**最小核心 + 可插拔积木**」：非核心能力全部移到 `plugins/`，需要时在 [pytest.ini](pytest.ini) 里加一个 `-p` 参数插入，删掉即拔出。

```
apitest-minimal/
├── core/                  # 核心骨架（跑通框架的最小集合，不含任何可选能力）
│   ├── config_loader.py   #   配置加载：.env 注入 + ${VAR} 插值
│   ├── base_client.py     #   HTTP 裸请求：URL 拼接 / Token 注入 / 脱敏日志
│   ├── auth_manager.py    #   登录鉴权：自动登录、Token 缓存与到期刷新
│   ├── assertions.py      #   断言引擎：10 种断言类型
│   ├── case_model.py      #   用例模型：YAML → TestCase，收集期强校验
│   ├── constants.py       #   markers 白名单
│   ├── utils.py           #   ${var} 运行时变量替换
│   ├── reporting.py       #   报告端口（空实现，真实实现在积木里）
│   ├── case_extras.py     #   跨用例能力端口（depends_on/extract/cleanup）
│   └── http_heal.py       #   HTTP 自愈端口（重试/401 重登）
├── api/
│   ├── endpoints.yaml     # ★ 接口注册中心：模块.接口 → method/path
│   └── registry.py
├── config/                # 多环境配置：dev.yaml / staging.yaml / prod.yaml
├── test_data/             # ★ 测试用例 YAML（递归扫描，当前有 1 条冒烟用例）
│   └── schemas/           #   schema 断言用的 JSON Schema 文件
├── testcases/
│   ├── test_regression.py #   数据驱动执行入口
│   ├── test_core_unit.py  #   核心单测（71 条，纯本地）
│   └── test_http_heal_unit.py # HTTP 自愈积木单测（7 条）
├── plugins/               # 积木层（见第 6 节）
│   ├── loader.py          #   积木装载器：读 plugins.yaml 清单动态加载积木（pytest.ini 中唯一的 -p）
│   ├── reporting/         #   报告积木（pytest-html + Allure）
│   ├── case_extras/       #   跨用例积木（depends_on/extract/cleanup/变量隔离）
│   ├── http_heal/         #   HTTP 自愈积木（重试退避/401 自动重登）
│   ├── log_setup/         #   日志积木（控制台 + output/logs/run.log，可用 APITEST_OUTPUT_DIR 改路径）
│   ├── terminal_cn/       #   Windows 中文显示积木（UTF-8/中文用例名）
│   ├── xdist_guard.py     #   并行护栏积木（装了 pytest-xdist 才生效，防止跨用例依赖被多进程跑坏）
│   └── casegen/           #   用例生成工具（命令行，非 pytest 积木）
├── conftest.py            # 全局 fixture：config / auth / api_client / variables
├── pytest.ini             # pytest 配置（addopts 仅挂积木装载器，积木启停改 plugins.yaml）
├── plugins.yaml           # ★ 积木清单：注释一行 = 拔出该积木
├── requirements.txt       # 核心依赖
├── requirements-reporting.txt # 报告积木依赖（可选）
└── .env.example           # 凭据模板（复制为 .env 填真实值）
```

运行产物默认在 `output/`（自动创建，已 gitignore）：`output/reports/` 报告、`output/logs/run.log` 日志。可通过环境变量 `APITEST_OUTPUT_DIR` 改到其他位置。

### 1.1 分层架构

框架自上而下分四层，**依赖只从上层指向下层**，非核心能力全部沉在积木层：

```
数据层    api/endpoints.yaml · test_data/**/*.yaml · config/*.yaml · test_data/schemas/*.json
          （只改数据即改行为，不碰代码）
   │
核心层    conftest.py（fixture）· testcases/test_regression.py（执行入口）
          core/：config_loader / base_client / auth_manager / assertions /
                 case_model / utils / constants
          api/registry.py：接口注册中心加载器
          （永远存在，不含任何可选能力）
   │  通过「端口」调用可选能力
端口层    core/reporting.py（报告）· core/case_extras.py（跨用例+变量隔离）· core/http_heal.py（自愈）
          （只定义接口 + 空操作默认实现 + set_xxx(impl) 注册函数）
   │  积木加载即注册真实实现
积木层    plugins/ + plugins.yaml：reporting · case_extras · http_heal · log_setup · terminal_cn · xdist_guard
          （可插拔：注释 plugins.yaml 对应行即拔出，核心照常运行）
```

### 1.2 核心模块职责

| 文件 | 职责 |
|---|---|
| [conftest.py](conftest.py) | 只注册 `--env`，提供 5 个 fixture：`config` / `auth` / `api_client` / `_session_variables` / `variables` |
| [testcases/test_regression.py](testcases/test_regression.py) | 数据驱动执行入口：加载用例 → 参数化 → 构造请求 → 断言 |
| [core/config_loader.py](core/config_loader.py) | 读 `config/<env>.yaml`，注入 `.env` 并做 `${VAR}` 插值 |
| [core/base_client.py](core/base_client.py) | HTTP 裸请求：URL 拼接 / Token 注入 / 脱敏日志（不自愈） |
| [core/auth_manager.py](core/auth_manager.py) | 登录鉴权、Token 缓存与到期刷新 |
| [core/assertions.py](core/assertions.py) | 10 种断言类型实现 |
| [core/case_model.py](core/case_model.py) | `TestCase` 模型：YAML → 对象，收集期强校验（字段结构属核心契约） |
| [core/utils.py](core/utils.py) | `${var}` 运行时替换 |
| [core/reporting.py](core/reporting.py) | **端口**：报告能力（空实现） |
| [core/case_extras.py](core/case_extras.py) | **端口**：跨用例能力 `depends_on`/`extract`/`cleanup` + 变量隔离策略（空实现） |
| [core/http_heal.py](core/http_heal.py) | **端口**：重试退避 / 401 自动重登（空实现） |
| [api/registry.py](api/registry.py) | 加载 `endpoints.yaml`，提供 `模块.接口` → `Endpoint(method/path/headers/body_type)` |

### 1.3 端口 ↔ 积木对应表

核心只留「接口 + 空操作」，真实实现由积木在 import 时通过 `set_xxx(impl)` 注册：

| 端口（核心，空实现） | 积木（真实实现） | 拔出后的行为 |
|---|---|---|
| [core/reporting.py](core/reporting.py) | `plugins/reporting` | 不产报告，只打印 pytest 原始输出 |
| [core/case_extras.py](core/case_extras.py) | `plugins/case_extras` | `depends_on`/`extract`/`cleanup` 退化为空操作 |
| [core/http_heal.py](core/http_heal.py) | `plugins/http_heal` | 裸请求：不重试、401 不自动重登 |
| 无端口，直接挂 pytest hook | `plugins/log_setup` / `terminal_cn` / `xdist_guard` | 无文件日志 / 中文名不美化 / 并行不设护栏 |

### 1.4 代码执行逻辑

**收集期**（`python -m pytest --collect-only` 就会走完，不登录、不发请求）：

1. pytest 启动 → `conftest.pytest_addoption` 注册 `--env`；装载器按 `plugins.yaml` 清单逐个导入积木，积木按需注册自己的开关（`pytest_addoption`）、并在 `pytest_configure` 把真实实现注册进对应端口；
2. 导入 [test_regression.py](testcases/test_regression.py) → `EndpointRegistry.load()` 读 `api/endpoints.yaml`；
3. `_load_cases()` 递归扫描 `test_data/**/*.yaml` → `TestCase.from_dict` 校验 → 用例 id 唯一性 → `api` 是否已登记；
4. `case_extras.validate_cases()` 做跨用例引用校验（`depends_on` 是否命中 / `cleanup` 接口是否登记 / `extract` 是否被引用）；
5. 每条用例转成一个 `pytest.param`，形成测试项。

**运行期**（真正发请求）：

1. session fixture 链：`config`（按 `--env` 读配置）→ `auth`（登录换 Token）→ `api_client`（带 Token 的裸客户端）→ `_session_variables`（`case_vars` 打底）；
2. 每条用例的 `variables` fixture 把打底值交给 `case_extras.prepare_variables` 决定「隔离/共享」（见第 7 节「变量隔离机制」）；
3. 逐条用例：`precondition` 检查 `depends_on` → 构造请求（`payload`/`headers`/`timeout` 归一化）→ `api_client.request`（自愈积木在此拦截重试与 401）→ 断言引擎逐条断言 → `after_response` 提取变量 → `record` 记录结论 → `teardown` 执行 `cleanup`；
4. 会话结束 → 报告积木落盘 `<APITEST_OUTPUT_DIR>/reports/`（默认 `output/reports/`）。

---

## 2. 环境准备

要求 Python 3.10+。在项目根目录打开 PowerShell：

```powershell
# 1)（建议）建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 装核心依赖
pip install -r requirements.txt

# 3) 需要测试报告时再装报告依赖
pip install -r requirements-reporting.txt

# 4) 配置登录凭据
copy .env.example .env
# 然后编辑 .env，填入 CLIENT_ID / ENCRYPT_KEY / LOGIN_PAYLOAD
```

`.gitignore` 已忽略 `.env`，密钥不会被提交。

---

## 3. 写第一条接口测试（三步走）

### 第一步：登记接口

在 [api/endpoints.yaml](api/endpoints.yaml) 登记 method/path（只登记一次，所有用例共用）：

```yaml
system:                     # 业务模块
  get_info:                 # 接口名 → 引用 key 为 system.get_info
    method: GET
    path: /system/user/getInfo
    # body_type: json       # 可选：json（默认）或 data（原始文本/表单），被用例的 body_type 覆盖
```

path 支持运行时变量，如 `/asset/${asset_id}`。

### 第二步：写用例 YAML

在 `test_data/` 下任意子目录新建 yaml（会递归扫描），一个列表项 = 一条用例：

```yaml
- api_name: "查询用户信息-冒烟"        # 用例名，必须全局唯一
  api: system.get_info                # 引用注册中心的 key
  markers: [smoke, p0]                # 可选：分级标记
  assert:                             # 必填：至少一条强断言
    - {type: status_code, expected: 200}
    - {type: json_path, path: code, expected: 200}
    - {type: exists, path: data.user, expected: true}
```

GET 请求参数写在 `payload`（字符串 query 或 dict 均可）；POST 默认发 JSON：

```yaml
- api_name: "新增资产"
  api: asset.create
  payload:
    name: "测试设备"
    type: "server"
  assert:
    - {type: json_path, path: code, expected: 200}
```

### 第三步：运行

```powershell
python -m pytest                      # 跑 test_data 下全部用例
python -m pytest -m smoke             # 只跑冒烟
```

就这样。用例写错（api 没登记、断言为空、marker 拼错等）在 `--collect-only` 阶段就会报错，不用等跑起来：

```powershell
python -m pytest --collect-only       # 不登录、不发请求，只校验用例能不能被收集
```

---

## 4. 用例 YAML 字段参考

| 字段 | 说明 |
|---|---|
| `api_name` | 用例名（同时是 pytest 用例 id，全局唯一） |
| `api` | 注册中心 key，如 `system.get_info` |
| `payload` | 请求参数：GET=query string；其他=JSON body（字符串 `"a=1&b=2"` 会自动转 dict） |
| `body_type` | `json`（默认）或 `data`（原始文本/表单），可覆盖注册中心配置；注册中心未配置时默认 `json` |
| `headers` | 用例级额外请求头（与注册中心的接口专属头合并） |
| `markers` | 分级：`smoke` `regression` `p0` `p1` `auto` |
| `assert` | 断言列表（必填，至少一条强断言） |
| `timeout` | 单用例超时（秒数或 `[连接超时, 读取超时]`），覆盖环境配置 |
| `extract` | 【跨用例积木】响应字段存为全局变量：`token: data.accessToken`，后续用 `${token}` 引用（默认每条用例变量独立，需加 `--cases-shared` 才跨用例生效，详见第 7 节） |
| `depends_on` | 【跨用例积木】前置用例名列表，前置没过则跳过，避免级联误报 |
| `cleanup` | 【跨用例积木】用例结束后调用的清理接口（失败只告警，不影响结论） |

### 10 种断言类型

| type | 作用 | 典型写法 |
|---|---|---|
| `status_code` | HTTP 状态码（弱断言，不能单独使用） | `{type: status_code, expected: 200}` |
| `json_path` | JSON 路径值比对（点号/方括号/数字索引） | `{type: json_path, path: data.0.id, expected: 100}` |
| `exists` | 字段是否存在 | `{type: exists, path: data.user, expected: true}` |
| `field_type` | 字段类型：int/str/bool/list/dict/float/null | `{type: field_type, path: data.id, expected: int}` |
| `list_len` | 数组长度（支持 gte/lte/eq 等 operator） | `{type: list_len, path: data, operator: gte, expected: 1}` |
| `contains` | 字符串包含 | `{type: contains, path: msg, expected: 成功}` |
| `header` | 响应头 | `{type: header, field: Content-Type, operator: contains, expected: json}` |
| `each` | 数组每一项的某字段都等于期望值 | `{type: each, path: data, field: type, expected: org}` |
| `schema` | JSON Schema 结构校验（文件放 `test_data/schemas/`） | `{type: schema, schema_file: user.json}` |
| `response_time` | 响应耗时 SLA（秒） | `{type: response_time, operator: lte, expected: 2}` |

完整范例见 [test_data/asset/asset_summary.yaml](test_data/asset/asset_summary.yaml)（一条用例覆盖全部断言类型，可当模板抄）。

---

## 5. 常用命令

```powershell
python -m pytest                                    # 全量执行（按 plugins.yaml 清单加载积木）
python -m pytest -m smoke                           # 只跑冒烟
python -m pytest -m "p0 and smoke"                  # 标记组合
python -m pytest -k "查询"                           # 按用例名过滤
python -m pytest --env=staging                      # 切环境（默认 dev）
python -m pytest --collect-only                     # 只校验+收集，不发请求
python -m pytest -v                                 # 详细输出
python -m pytest testcases/test_core_unit.py testcases/test_http_heal_unit.py   # 框架自身 78 条本地单测（71 核心 + 7 自愈）

# 纯核心模式（拔出全部积木，验证核心独立可运行）
$env:APITEST_PLUGINS_OFF="*"; python -m pytest testcases/test_core_unit.py -q
Remove-Item Env:APITEST_PLUGINS_OFF
```

报告产物（装了报告依赖时自动生成）：

- HTML：`<APITEST_OUTPUT_DIR>/reports/report.html`（默认 `output/reports/report.html`），浏览器直接打开
- Allure 原始数据：`<APITEST_OUTPUT_DIR>/reports/allure-results/`（默认 `output/reports/allure-results/`），用 allure 命令行工具生成网页报告

---

## 6. 积木机制（非核心能力的插拔）

积木清单在 [plugins.yaml](plugins.yaml)：**注释掉某行 = 拔出该积木**，取消注释 = 插入；不用动任何代码，核心照常运行：

```yaml
plugins:
  - plugins.reporting.plugin     # 测试报告（pytest-html + Allure）
  - plugins.case_extras.plugin   # extract 变量提取 / depends_on 依赖 / cleanup 清理 / --cases-shared
  - plugins.xdist_guard          # xdist 并行护栏（装了 pytest-xdist 才生效）
  - plugins.terminal_cn.plugin   # Windows 终端 UTF-8 + 中文用例名显示
  - plugins.log_setup.plugin     # 日志写文件 <APITEST_OUTPUT_DIR>/logs/run.log（默认 output/logs/run.log，按天滚动）
  - plugins.http_heal.plugin     # 超时重试 / 401 自动重登 / --fail-on-auth-expired
```

装载原理：[pytest.ini](pytest.ini) 只挂一个 `-p plugins.loader`（[装载器](plugins/loader.py)），它在 pytest 启动早期读清单并逐个注册积木，效果等同手写一排 `-p`，但启停不用碰 pytest.ini。

临时禁用（不改清单，适合 CI 或排查问题；短名逗号分隔）：

```powershell
$env:APITEST_PLUGINS_OFF="reporting"; python -m pytest    # 本次不加载报告积木
$env:APITEST_PLUGINS_OFF="*"; python -m pytest            # 全部拔出：纯核心模式
Remove-Item Env:APITEST_PLUGINS_OFF                        # 用完清掉
```

积木提供的可选命令行开关：

| 开关 | 所属积木 | 作用 |
|---|---|---|
| `--cases-shared` | case_extras | 跨用例共享变量上下文（extract 才能传给后续用例）；默认每条用例独立隔离，详见第 7 节「变量隔离机制」 |
| `--fail-on-auth-expired` | http_heal | 重登后仍 401/403 时直接抛错（默认关闭，交给断言判定） |

HTTP 自愈的参数在环境配置里调（`config/*.yaml` 已内置，默认不重试）：

```yaml
retry: {times: 0, backoff: 0.5, retry_on: [timeout, connection]}  # times=0 不重试，需要时改成正整数
auth_relogin_401: true         # 收到 401/403 自动重登并重放（默认开）
auth_fail_on_expired: false    # 重登后仍 401/403 是否直接抛错
```

### 开发一个新积木（5 步）

以后有新功能就按这个套路插入框架，不需要时从清单拔掉：

1. **是否需要被核心调用？** 需要（如报告、自愈这类被 core 代码主动调用的能力）→ 先在 `core/` 建端口文件：接口 + `_Null` 空实现 + `set_xxx(impl)` 注册函数，照抄 [core/reporting.py](core/reporting.py) 的模式；纯挂 pytest 钩子的能力（如日志、终端显示）跳过这步；
2. **实现**：新建 `plugins/<name>/plugin.py`——实现端口并在模块末尾 `set_xxx(...)` 注册，或纯写 pytest hook；可选第三方依赖一律 `try/except ImportError` 降级，缺依赖不影响测试执行；
3. **登记**：`plugins.yaml` 清单加一行 `plugins.<name>.plugin`；
4. **依赖**：新增依赖写进独立的 `requirements-<name>.txt`，不进核心 `requirements.txt`；
5. **单测**：放 `testcases/test_<name>_unit.py`，文件头加 `pytest.importorskip("plugins.<name>.plugin")`，积木被拔出时测试自动跳过。

红线：积木可以 import core；**core/ 与 conftest.py 永远不 import plugins**——单向依赖是「拔出零污染」的根本保证。

---

## 7. 多环境与全局变量

- 环境配置：`config/dev.yaml`、`staging.yaml`、`prod.yaml`，用 `--env` 切换。
- 配置里的 `${CLIENT_ID}` 等从 `.env` / 系统环境变量注入，敏感信息不入库。
- `case_vars` 是全局变量默认值，用例的 payload/path 中可用 `${DEPT_ID}` 引用，`extract` 提取的同名变量会覆盖它：

```yaml
case_vars:
  DEPT_ID: "100"
```

### 变量隔离机制（默认隔离，按需共享）

变量上下文有三级来源：`config.case_vars` 打底 → `extract` 提取覆盖 → 用例 YAML 里 `${VAR}` 运行时引用。核心只负责「打底 + 引用」，**每条用例看到的到底是哪一份上下文，由跨用例积木 `case_extras` 决定**：

| 模式 | 触发方式 | 行为 | 适用场景 |
|---|---|---|---|
| **默认：隔离** | 直接 `python -m pytest` | `variables`（function 级）对打底值 `copy.deepcopy` → 每条用例独立，互不污染 | 绝大多数场景：用例之间无数据依赖 |
| **共享** | 追加 `--cases-shared` | 所有用例共用同一个 session 变量 dict，`extract` 写回后跨用例可见 | 需要「用例 A 提取 → 用例 B 引用」的串联场景 |

要点：

- **`extract` 默认不跨用例生效**：写回的是本用例的深拷贝副本。若检测到 `extract` 却没开 `--cases-shared`，框架会在收集期打一条 WARNING 提示，避免「写了却不生效」的静默问题；
- **`depends_on` 不受隔离影响**：依赖门控走跨用例的「结论记录表」（`passed/failed/skipped`），与变量上下文是否共享无关；
- **共享有代价**：`--cases-shared` 下用例顺序敏感（用例按 `test_data` 文件路径排序执行），并行（xdist）时共享 dict 会互相踩踏，护栏积木会拦下含跨用例依赖的用例；
- 若拔出 `case_extras` 积木，`prepare_variables` 原样返回 session 实例，等价于「共享」模式。

---

## 8. 用例批量生成工具（casegen）

接口很多时，可用抓包 HAR 一键生成「结构层基线用例」（独立命令行工具，不属于 pytest 积木）：

```powershell
# 1) F12 抓包导出 HAR → 接口清单
python -m plugins.casegen.har2list --har xxx.har

# 2) 人工审阅 plugins/casegen/case_list_from_har.txt（删掉危险写接口）

# 3) 清单 → 基线用例 YAML + JSON Schema（默认写进 test_data/ 与 test_data/schemas/）
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt --dry-run  # 先预览
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt            # 再落盘
```

历史生成产物在 `plugins/casegen/generated/`（默认**不参与执行**，需要时把其中的 yaml 拷到 `test_data/` 对应目录即可）。

---

## 9. 现在能直接做接口测试吗？

**框架本身：可以，已经就绪。** 自检三步：

```powershell
python -m pytest testcases/test_core_unit.py testcases/test_http_heal_unit.py   # 78 条框架单测应全绿（不联网）
python -m pytest --collect-only                # 应收集到 79 项（78 单测 + 1 条接口用例）
python -m pytest -m smoke                      # 真正发请求
```

真正跑通接口用例还需要两个外部条件：

1. **网络可达被测服务**：当前 `config/dev.yaml` 指向 `http://10.148.242.205/dev-api`（内网地址），需在对应内网/VPN 环境运行；
2. **`.env` 凭据有效**：登录接口要能换到 Token。

当前活跃接口用例只有 1 条手写冒烟用例（[test_data/asset/asset_summary.yaml](test_data/asset/asset_summary.yaml)，组织列表接口，作为断言模板）。新增被测接口时按第 3 节登记 + 写 YAML 即可。

### 常见问题

| 现象 | 排查 |
|---|---|
| 启动即 `ImportError: No module named 'plugins'`（裸 `pytest` 命令） | 改用 `python -m pytest`；或先执行 `$env:PYTHONPATH="."` 再用 `pytest`。裸 `pytest` 不会把项目根目录加入 `sys.path`，导致积木装载器与清单中的 `plugins.*` 模块无法被导入 |
| `Connection refused / timed out` | 没连内网/VPN，或 `config/<env>.yaml` 的 base_url 不对 |
| 全部用例 401/403 | `.env` 里的登录凭据失效；看日志里登录接口的返回 |
| 收集期就报错 | YAML 写错：看报错指明的字段（api 未登记 / 断言为空 / marker 未注册等） |
| 启动报 `积木 xxx 加载失败` | 该积木代码或依赖有问题；临时绕过：注释 `plugins.yaml` 对应行，或 `APITEST_PLUGINS_OFF=短名` |
| 中文用例名显示成 `\uXXXX` | terminal_cn 积木被拔出，或终端不是 UTF-8 |
| 不想生成报告 | 不装报告依赖即可；也可注释 `plugins.yaml` 中 reporting 行 |
| `schema` 断言失败：Schema 文件不存在 | `test_data/schemas/` 下缺少对应文件，或 `schema_file` 拼写错误 |

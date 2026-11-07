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
├── test_data/             # ★ 业务用例 YAML（递归扫描，当前 15 条：1 手写 + 14 自动生成）
│   └── schemas/           #   schema 断言用的 JSON Schema 文件
├── testcases/
│   └── test_regression.py #   业务执行入口（唯一被默认收集的测试文件）
├── selftest/              # 框架自检（不联网；默认 pytest 不收集，需显式指定目录）
│   ├── test_core_unit.py  #   核心单测（71 条）
│   ├── test_http_heal_unit.py # HTTP 自愈积木单测（7 条）
│   └── test_assets.py     #   用例资产/配置/积木清单自检（7 条）
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
| [core/case_loader.py](core/case_loader.py) | 用例资产加载与校验（扫描 YAML、id 唯一、api 已登记），业务入口与离线自检共用 |
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
3. `test_regression.py` 调用 `core.case_loader.load_cases()` 递归扫描 `test_data/**/*.yaml` → `TestCase.from_dict` 校验 → 用例 id 唯一性 → `api` 是否已登记；
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

装完依赖后建议先跑一次框架自检，确认核心、资产、积木清单都没问题，再去连环境（详见第 9 节）：

```powershell
python -m pytest selftest
```

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

跑之前按「离线自检 → 收集校验 → 联网执行」三步走，每步失败的原因互不重叠：

```powershell
python -m pytest selftest             # 1) 离线自检：框架代码 + 用例资产 + 积木清单（不联网）
python -m pytest --collect-only       # 2) 收集校验：不登录、不发请求，只确认用例能被收集
python -m pytest                      # 3) 真正执行 test_data 下的业务用例（需网络 + 凭据）
python -m pytest -m smoke             #    或只跑冒烟
```

用例写错（api 没登记、断言为空、marker 拼错等）在第 2 步就会报错，不用等跑起来。`python -m pytest` 默认**只收集 `testcases/` 下的业务用例**，框架自测已拆到 `selftest/`，不会混进业务结果。

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
# ---- 业务用例（默认，需网络 + 凭据）----
python -m pytest                                    # 全量执行（按 plugins.yaml 清单加载积木）
python -m pytest -m smoke                           # 只跑冒烟
python -m pytest -m "p0 and smoke"                  # 标记组合
python -m pytest -m auto                            # 只跑 casegen 自动生成的用例
python -m pytest -k "查询"                           # 按用例名过滤
python -m pytest --env=staging                      # 切环境（默认 dev）
python -m pytest --collect-only                     # 只校验+收集，不发请求
python -m pytest -v                                 # 详细输出

# ---- 框架自检（离线，不联网、不登录）----
python -m pytest selftest                           # 全部自检 85 条（71 核心 + 7 自愈 + 7 资产）
python -m pytest selftest/test_assets.py            # 只查用例资产/配置/积木清单
python -m pytest selftest testcases                 # 自检 + 业务全量

# 纯核心模式（临时禁用全部积木，验证核心独立可运行；自愈积木自己的测试不在其列）
$env:APITEST_PLUGINS_OFF="*"; python -m pytest selftest/test_core_unit.py selftest/test_assets.py -q
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
5. **单测**：放 `selftest/test_<name>_unit.py`，文件头加 `pytest.importorskip("plugins.<name>.plugin")`，积木被拔出时测试自动跳过。框架/积木自测一律放 `selftest/`，不要放进 `testcases/`（那里默认只放业务入口）。

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

`plugins/casegen/` 是一个**独立命令行工具**（不是 pytest 积木，不影响框架运行），解决「接口太多、逐个手写 YAML 太慢」的问题。核心思路是**契约快照**：真实调用一次接口，把响应结构固化成断言，而不是猜字段。

流程分两段，中间必须人工审阅：

```
浏览器抓包 → xxx.har
   │  har2list.py：过滤静态资源/第三方域名/登录接口，按 method+path 去重
   ▼
接口清单 case_list_from_har.txt（每行一个接口）
   │  ★ 人工审阅：删掉导出/上传/危险写接口，把易变 ID 换成 ${VAR}
   ▼
gen_cases.py：真实调用接口 → 生成结构层断言 + JSON Schema
   ▼
test_data/<模块>/<模块>_gen.yaml + test_data/schemas/gen_*.json
   │  ★ 人工收尾：补 markers、放开值层断言、补 TODO 骨架
   ▼
python -m pytest -m auto
```

> HAR 文件含登录 Cookie / Authorization / 业务数据，已被 `.gitignore` 的 `*.har` 排除；不要提交、用完即删。

### 8.1 第一步：从被测环境抓 HAR

前提：浏览器能正常访问被测环境，并且**已经登录**（登录接口由框架自己调，抓包时排除）。

1. 打开被测系统页面，按 `F12` → **Network** 面板；
2. 勾选 **Preserve log**（保留跨页面请求）、建议勾选 **Disable cache**；
3. 过滤器选 **Fetch/XHR**（只看接口，静态资源会被 har2list 再过滤一遍）；
4. 把要生成用例的模块**逐个点一遍**（列表、详情、看板、字典下拉等），确保接口都发出来；
5. 在请求列表空白处右键 → **Save all as HAR with content**（一定要带 `with content`，否则没有响应体，无法生成断言）；
6. 保存为 `xxx.har`。

放在哪里都行，命令里的相对路径以**项目根目录**为基准。推荐约定：

```
plugins/casegen/dev_20261106.har
```

（`zichan.har` 就是历史遗留的示例文件。）

注意：`har2list` 只会保留 **URL 以 `config/<env>.yaml` 的 `base_url` 开头**的请求。所以 HAR 必须抓自同一环境；如果抓的是另一个域名，用 `--base-url` 显式指定，否则结果会是「保留 0 条」。

### 8.2 第二步：HAR → 接口清单

```powershell
# 默认：读 config/dev.yaml 的 base_url，输出 plugins/casegen/case_list_from_har.txt
python -m plugins.casegen.har2list --har plugins/casegen/dev_20261106.har

# 指定环境（base_url 取自 config/staging.yaml）
python -m plugins.casegen.har2list --har plugins/casegen/staging.har --env staging

# 直接指定 base_url（HAR 域名与配置不一致时用）
python -m plugins.casegen.har2list --har plugins/casegen/xxx.har --base-url http://10.148.242.205/dev-api

# 自定义清单输出路径
python -m plugins.casegen.har2list --har plugins/casegen/xxx.har --out plugins/casegen/my_list.txt
```

参数一览：

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--har` | 是 | — | HAR 文件路径 |
| `--env` | 否 | `dev` | 从 `config/<env>.yaml` 读 `base_url` |
| `--base-url` | 否 | — | 直接指定，优先级高于 `--env` |
| `--out` | 否 | `plugins/casegen/case_list_from_har.txt` | 清单输出路径 |

转换规则（脚本内固定行为，不需要配置）：

- 过滤非 `base_url` 前缀的请求、静态资源（`.js/.css/图片/字体/.html`）、`/auth/login`；
- 同 `method + path` 去重，保留**参数信息量最大**的一条（有 body 优先，query 参数更多优先）；
- 模块名 = path 第一段；接口名 = path 末段转 `snake_case`，重名时自动带前一段前缀（版本段 `v2` 自动跳过）；
- GET 的 query 串、POST/PUT 的 JSON / 表单 body 自动写成 `payload`；
- multipart 文件上传无法离线还原 body，输出为 `# TODO: ...` 注释行。

命令结束会打印统计，例如 `总请求 60 条 | 过滤后候选 60 条 | 同 method+path 合并 3 条 | 清单接口 57 个 | 静态资源过滤 25 条 | 登录接口排除 2 条`。注意「候选条数」是过滤后的请求数，「清单接口数」才是去重后真正写入文件的行数（同一个接口被页面重复调用时会合并成 1 条）。**清单接口 0 个**通常是 base_url 不匹配。

### 8.3 第三步：人工审阅清单（必做）

打开 `plugins/casegen/case_list_from_har.txt`：输出已按模块分组（`# ========== 模块 ==========`），条目之间留空行、列对齐，便于逐条删除或修改；`#` 开头为注释，空行与行首空格都不影响解析。每行一个接口，两种格式：

```text
# 格式1：接口已在 api/endpoints.yaml 登记（推荐，日常最常用）
system.org_list
asset.instock_list | pageNum=1&pageSize=10

# 格式2：新接口，脚本会自动登记进 api/endpoints.yaml
asset.secret_create | POST | /asset/secret/create | {"name": "测试资产"}

# 文件上传等无法还原 body 的，以注释输出；人工补 payload 后改成正常清单行
# TODO: asset.upload | POST | /asset/upload   ← multipart 文件上传接口...
```

审阅时至少做这几件事：

1. **删掉不该自动跑的接口**：导出、下载、批量删除、审批、发布等写接口；`DELETE` 即使留下也只会生成骨架，不会真实调用；
2. **检查 payload**：HAR 里的 `deptId=100`、`groupId=9c18...` 这类 ID 是当时的数据，建议改成 `${DEPT_ID}` / `${GROUP_ID}`（在 `config/<env>.yaml` 的 `case_vars` 里定义）；
3. **确认模块名/接口名**：`模块.接口名` 是唯一键，后续用例的 `api` 字段就引用它；命名太随意会影响可读性。

格式约束：

- 格式1 的 key 必须已在 `api/endpoints.yaml` 登记，否则命令直接报错并列出缺失项；
- 格式2 会先追加登记到 `api/endpoints.yaml`（自动备份 `.yaml.bak`），再生成用例；若 key 已登记，**以注册中心里的 method/path 为准**（清单里的写法不一致只会打 WARNING）；
- `--dry-run` 下不登记、不写任何文件，只打印将生成的用例。

### 8.4 第四步：清单 → 基线用例 + JSON Schema

```powershell
# 先预览：真实调用接口，但一张文件都不写（endpoints.yaml / 用例 / schema 均不落盘）
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt --dry-run

# 确认无误后落盘
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt

# 指定环境 / 输出目录
python -m plugins.casegen.gen_cases --list ... --env staging
python -m plugins.casegen.gen_cases --list ... --out test_data --schemas-dir test_data/schemas

# 重新生成：默认跳过已存在的 api；--force 整文件覆盖（覆盖前自动备份）
python -m plugins.casegen.gen_cases --list ... --force

# 更严格的结构契约（additionalProperties=false）
python -m plugins.casegen.gen_cases --list ... --strict-schema
```

**运行前提**：需要 `.env` 里有有效凭据，且网络能连到 `base_url`——脚本会先 `AuthManager.login()`，然后带着 Token 逐个真实调用接口。

参数一览：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--list` | 必填 | 接口清单 txt |
| `--env` | `dev` | 使用 `config/<env>.yaml` 的 base_url / 凭据 / timeout |
| `--out` | `test_data` | 用例 YAML 输出根目录 |
| `--schemas-dir` | `test_data/schemas` | JSON Schema 输出目录 |
| `--dry-run` | 关 | 只调用+预览，不写任何文件 |
| `--force` | 关 | 整文件覆盖 `<模块>_gen.yaml`（先备份到 `output/gen_cases_backups/`） |
| `--strict-schema` | 关 | schema 的 `additionalProperties=false` |
| `--default-markers` | `auto` | 自动用例默认标记，逗号分隔；空字符串则不打 |

调用安全策略（分级，防止污染环境）：

| 情况 | 行为 |
|---|---|
| `GET` | 真实调用（只读，安全） |
| `POST` / `PUT` 且给了 payload | 真实调用 |
| `POST` / `PUT` 没给 payload | **不调用**，生成骨架用例 + `TODO` 注释 |
| `DELETE` | **一律不调用**，只生成骨架用例 |
| 响应异常（HTTP ≠ 200 / 非 JSON / `code` ≠ 200） | 不固化错误结构，降级为骨架 + `TODO` |

生成产物：

```
test_data/<module>/<module>_gen.yaml     # 用例（每条一个顶层 - api_name 记录）
test_data/schemas/gen_<key>.json         # 响应结构 Schema（含 _meta：生成时间/来源 key）
```

> `gen_*.json` 给用例里的 `{type: schema, schema_file: gen_xxx.json}` 断言用。**Schema 断言固定从 `test_data/schemas/` 读取**，所以 `--schemas-dir` 一般保持默认；改到别处会导致 schema 断言报「文件不存在」。
>
> 生成文件写进 `test_data/` 后会被 `test_regression.py` 自动扫描，**无需改 Python 代码**。

重复生成的保护：

- 默认按 `api` 去重——已存在的接口跳过，只追加新接口，人工改过的断言不会被覆盖；
- `--force` 会整文件重写，覆盖前先把旧文件备份到 `<APITEST_OUTPUT_DIR>/gen_cases_backups/<module>_gen.<时间戳>.yaml`（默认 `output/gen_cases_backups/`）；
- 想只重生成某一个接口：从 `_gen.yaml` 里删掉那条记录，再跑一次（不加 `--force`）。

### 8.5 第五步：生成后必须人工收尾

生成结果只是「结构基线」，直接全量跑通常需要下面几处调整：

1. **markers**：默认 `auto`。核心链路建议追加 `smoke, p0`，普通接口 `p1`；之后可用 `python -m pytest -m auto` 只跑生成用例，`-m "not auto"` 只跑手写用例：
   ```yaml
   - api_name: "system.org_list（自动生成基线 2026-11-06）"
     api: system.org_list
     markers: [auto, smoke, p0]
   ```
2. **api_name**：它同时是 pytest 用例 id 和 `depends_on` 的引用键，**必须全局唯一**；可改名，但改名后要同步检查依赖它的用例。
3. **payload**：把抓包时的会话数据（部门 ID、分组 ID、时间戳、分页游标）换成 `${DEPT_ID}` 这类变量，避免换个环境就 404 / 空数据；变量在 `config/<env>.yaml` 的 `case_vars` 定义。
4. **TODO 骨架**（文件里的 `# TODO:` 行）：
   - 缺 payload 的写接口：补上 payload 后重跑生成，或手工补断言；
   - `DELETE` / 上传 / 导出类：建议手工写 `cleanup` 收尾（见第 4 节），或干脆不纳入自动回归；
   - 非 200 / 非 JSON 的：先排查参数（多半是缺 header / 缺 payload），不要直接保留骨架里的失败断言。
5. **值层断言**：生成时默认只生效结构层。`# ---- 值层断言（数据敏感，人工确认后放开） ----` 下面的 `list_len`、null 字段值等是**注释**，确认业务语义稳定后再放开；`msg: 操作成功`、`code: 200` 这类已经生效。
6. **Schema 严格度**：生成的 schema 把「本次观察到的所有字段」都列为 `required`。若接口字段经常增减，手工删掉非必填项；需要更严格契约时用 `--strict-schema` 重新生成。
7. **跑一遍收集校验**：
   ```powershell
   python -m pytest --collect-only     # 校验 api 已登记、用例 id 不重复、断言非空
   python -m pytest -m auto            # 真正执行生成用例
   ```

### 8.6 产物目录说明

| 路径 | 是否参与 pytest | 说明 |
|---|---|---|
| `test_data/<module>/<module>_gen.yaml` | **是** | gen_cases 的正式产物，递归扫描后自动成为用例 |
| `test_data/schemas/gen_*.json` | 间接（被 schema 断言引用） | 生成的结构契约 |
| `plugins/casegen/case_list*.txt` | 否 | 接口清单（`case_list.txt` 是本地草稿，`case_list.example.txt` 是格式示例） |
| `plugins/casegen/generated/` | 否 | 历史业务项目产出的样例；要复用就拷到 `test_data/` 对应模块目录 |
| `output/gen_cases_backups/` | 否 | `--force` 覆盖前的自动备份 |

### 8.7 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `HAR 中没有请求记录` | 导出的是页面/站点 HAR 或空 HAR；确认是 Network 面板 → *Save all as HAR with content* |
| 转换后「保留 0 条」 | HAR 域名与 `config/<env>.yaml` 的 `base_url` 不一致；用 `--base-url` 指定 |
| 生成时登录失败 / 超时 | `.env` 凭据无效或连不上被测环境（与跑 pytest 的环境要求相同） |
| 生成了骨架 + TODO | 正常：DELETE、缺 payload 的写接口、异常响应都不会固化断言，按 TODO 补 |
| 报「接口未在 api/endpoints.yaml 登记」 | 清单用了格式1 但接口没登记：改成格式2（自动登记）或先手工登记 |
| `--force` 后人工断言不见了 | 从 `output/gen_cases_backups/` 找时间戳备份恢复；下次优先用「删单条再重跑」 |
| `schema` 断言报文件不存在 | `gen_*.json` 没生成，或 `--schemas-dir` 改到了 `test_data/schemas` 之外 |
| 收集期报用例 id 重复 | 两个 YAML 的 `api_name` 相同（含 `python -m plugins.casegen.gen_cases` 多次生成的不同模块同名用例）；改成唯一名 |

---

## 9. 测前自检：框架能不能用

框架自检与业务用例已经**物理隔离**：`selftest/` 放框架自测，`testcases/` 只放业务入口。`pytest.ini` 的 `testpaths = testcases`，所以 `python -m pytest` 默认只跑业务，不会混入框架自测。

### 9.1 三层校验总览

按「从便宜到贵、从本地到环境」的顺序执行，每层失败的含义互不重叠：

| 层级 | 命令 | 联网 | 检查内容 | 失败含义 |
|---|---|---|---|---|
| L1 离线自检 | `python -m pytest selftest` | ❌ | 框架代码逻辑、用例资产结构、环境配置、积木清单 | 框架或资产有问题，**与网络无关** |
| L2 收集校验 | `python -m pytest --collect-only` | ❌ | 业务用例能否被收集（api 登记、id 唯一、断言强度、跨用例引用） | 用例 YAML 写错 |
| L3 联网预检 | `python -m pytest -m smoke` | ✅ | 登录换 Token、请求真实接口、断言通过 | 环境/凭据/业务有问题 |

**不要跳过 L1 直接跑 L3**：否则一个框架 bug 会表现成“接口全挂”，排查方向直接跑偏。

### 9.2 L1：离线自检（85 条）

```powershell
python -m pytest selftest
```

| 文件 | 条数 | 覆盖 |
|---|---|---|
| [selftest/test_core_unit.py](selftest/test_core_unit.py) | 71 | 断言引擎、路径取值、变量替换、配置插值、注册中心、客户端脱敏/鉴权日志、用例模型校验 |
| [selftest/test_http_heal_unit.py](selftest/test_http_heal_unit.py) | 7 | 重试退避、401 自动重登、鉴权失败开关（积木模块被删除时整文件自动 skip） |
| [selftest/test_assets.py](selftest/test_assets.py) | 7 | 注册中心可加载、3 个环境配置必填项、全部用例可加载、schema 文件存在、积木清单模块存在 |

`test_assets.py` 与业务入口共用 [core/case_loader.py](core/case_loader.py) 的 `load_cases()`，所以**自检通过的资产，就是真正执行时加载的资产**，不会出现“自检过了但收集失败”。

L1 不覆盖：网络可达性、`.env` 凭据有效性、Token 能否换到——这些必须联网，放到 L3。

只想检查资产（最常见）：

```powershell
python -m pytest selftest/test_assets.py
```

验证“禁用全部积木后核心仍可独立运行”（项目硬约束；临时禁用不等于删除积木，自愈测试文件仍可导入，所以这里不带上它）：

```powershell
$env:APITEST_PLUGINS_OFF="*"; python -m pytest selftest/test_core_unit.py selftest/test_assets.py -q
Remove-Item Env:APITEST_PLUGINS_OFF
```

### 9.3 L2：收集校验

```powershell
python -m pytest --collect-only
```

不登录、不发请求，只做收集期校验。这一步会检查：

- `api` 是否已在 `api/endpoints.yaml` 登记；
- `api_name`（pytest 用例 id）是否全局唯一；
- `assert` 是否非空、是否至少一条强断言（只有 `status_code` 会被拒绝）；
- `markers` 是否在 `pytest.ini` 注册；
- `depends_on` / `extract` / `cleanup` 的跨用例引用是否成立。

正常输出形如：

```
testcases/test_regression.py: 15
```

数字应等于 `test_data/**/*.yaml` 里的用例条数。对不上就是有 YAML 没被扫到（比如写成 `.yml`）或有空文件。

### 9.4 L3：联网预检

前置条件：

1. 能访问 `config/<env>.yaml` 的 `base_url`（`dev` 是内网地址，需 VPN）；
2. `.env` 里 `CLIENT_ID` / `ENCRYPT_KEY` / `LOGIN_PAYLOAD` 有效。

```powershell
python -m pytest -m smoke          # 推荐：最小代价验证「登录 + 真实请求 + 断言」全链路
python -m pytest -k "orgList"      # 或只跑单条
```

L3 通过 = 登录成功 + 接口可达 + 断言通过 + 报告落盘，此时再跑全量才不会把环境问题当成业务回归。

### 9.5 自检失败定位

| 层级 | 典型现象 | 含义 | 处理 |
|---|---|---|---|
| L1 | `test_core_unit.py` 红 | 框架代码被改坏 | 看失败断言对应模块（断言/取值/替换/模型） |
| L1 | `接口 'xxx' 未登记` | 用例引用了不存在的接口 | 补 `api/endpoints.yaml` 或改 `api` 字段 |
| L1 | `用例 id 重复` | 两个 YAML 的 `api_name` 相同 | 改成唯一名（`depends_on` 依赖它） |
| L1 | `缺少 base_url / timeout` | 环境配置不完整 | 补 `config/<env>.yaml` |
| L1 | `积木在清单中启用但找不到模块文件` | 清单写了但文件被删/改名 | 改 `plugins.yaml` 或补文件 |
| L2 | 数字小于 YAML 条数 | 文件扩展名不是 `.yaml` / 顶层不是列表 | 修正文件 |
| L2 | 收集期直接 ERROR | 用例 YAML 结构错误 | 按报错字段修 |
| L3 | `Connection refused / timed out` | 网络不通或 base_url 错 | 查 VPN / `--env` |
| L3 | 全部 401/403 | 凭据失效 | 更新 `.env`，看日志中登录响应 |
| L3 | 仅个别用例断言失败 | 业务变更或断言过期 | 进入第 10.4 节归因 |

---

## 10. 从开跑到结果审查：完整流程

```
① 环境准备 ──► ② 登记接口 ──► ③ 写用例 ──► ④ 三层自检 ──► ⑤ 执行
                                                          │
⑧ 归档与迭代 ◄── ⑦ 人工审查 ◄── ⑥ 结果落地 ◄──────────────┘
```

### 10.1 各阶段要做什么

| 阶段 | 动作 | 产出/检查点 |
|---|---|---|
| ① 环境准备 | 装依赖、配 `.env`、连 VPN | `pip install -r requirements.txt`；`copy .env.example .env` |
| ② 登记接口 | 在 [api/endpoints.yaml](api/endpoints.yaml) 写 `模块.接口 → method/path` | 路径只维护一份，用例只引用逻辑名 |
| ③ 写用例 | 手写 YAML（第 3、4 节），或 casegen 生成（第 8 节） | `test_data/<模块>/*.yaml` |
| ④ 测前自检 | L1 → L2 → L3（第 9 节） | 85 条自检绿、收集数正确、smoke 通过 |
| ⑤ 执行 | 按范围选择命令（10.2） | 控制台进度 + 报告 |
| ⑥ 结果落地 | 打开 HTML / Allure / 日志 | 10.3 的产物清单 |
| ⑦ 人工审查 | 按 10.5 清单逐项确认 | 通过 / 需修复 / 需调整预期 |
| ⑧ 归档与迭代 | 保留报告、清理 HAR、更新用例 | 下一轮回归基线 |

### 10.2 执行阶段：按范围选命令

```powershell
python -m pytest                        # 全量业务回归
python -m pytest -m smoke               # 冒烟（提测/上线前）
python -m pytest -m "p0 and smoke"      # 指定优先级
python -m pytest -m auto                # 只跑 casegen 生成用例
python -m pytest -m "not auto"          # 只跑手写用例
python -m pytest testcases -k "orgList" # 单接口调试
python -m pytest --env=staging          # 切环境
```

需要跨用例传值时加 `--cases-shared`（见第 7 节）；装了 `pytest-xdist` 时可 `-n auto`，但含 `depends_on`/`extract` 的用例会被护栏积木拦下。

### 10.3 结果在哪看

| 产物 | 位置 | 用途 |
|---|---|---|
| HTML 报告 | `output/reports/report.html` | 逐条结果、失败信息、**断言结果明细**（每条断言的通过/失败） |
| Allure 原始数据 | `output/reports/allure-results/` | 需要趋势/附件时用 allure 命令生成网页 |
| 运行日志 | `output/logs/run.log` | 请求/响应（已脱敏）、重试、登录、堆栈 |
| 控制台 | 终端实时输出 | 进度、首次失败摘要、live log |

排查顺序建议：**先看 HTML 的失败用例名 → 再看该用例的“断言结果明细” → 最后看 `run.log` 的请求/响应**。不要一上来翻日志全文。

### 10.4 失败归因：四类信号

| 类型 | 典型信号 | 判断依据 | 处理 |
|---|---|---|---|
| **环境层** | 所有用例同时失败；`ConnectionError`/`Timeout`/`401` | `pytest selftest` 全绿，但 L3 全挂 | 检查 VPN、`base_url`、`.env`、服务是否启动 |
| **框架层** | 大量不相关用例同时失败；堆栈落在 `core/`；自检也红 | `pytest selftest` 有失败 | 先修框架，再重跑业务 |
| **资产层** | 收集期 ERROR；`Schema 文件不存在`；断言数异常少 | `--collect-only` 就报错 | 修 YAML / 补 schema / 补登记 |
| **业务层** | 仅个别用例失败；断言明细指出具体字段/值 | 环境、框架、资产都正常 | 判断是接口变更还是断言过期 |

关键原则：**先用 `pytest selftest` 排除框架层**，避免把框架 bug 当成业务回归去改断言。

### 10.5 人工审查清单（跑完必看）

`passed` 数字不能直接当结论，至少确认以下 8 项：

1. **用例总数**是否等于预期（YAML 条数 / 与上次对比有无意外增减）；
2. **失败是否集中**在同一接口或模块（集中 = 大概率环境或该模块变更；分散 = 框架或数据问题）；
3. **断言明细是否真的校验了业务**：只有 `status_code=200` + `code=200` 的用例属于弱校验，容易假通过；
4. **自动生成用例的 TODO / 值层断言**是否已处理（`# TODO:` 说明当时调用异常；值层断言默认是注释）；
5. **跳过（skipped）的用例**是否合理：`depends_on` 前置失败会级联 skip，不要当成通过；
6. **响应时间**是否异常（`response_time` 断言或报告耗时），突然变慢可能是环境问题；
7. **日志脱敏**是否生效：`run.log` 中不应出现完整 Token / 密码；
8. **与上一轮结果对比**：新增失败优先看，长期失败要建缺陷或修正预期。

审查结论只有三种：**通过**（可归档）、**需修复**（代码/环境/资产）、**需调整预期**（业务确实变更，更新断言并在提交说明里写清原因）。

### 10.6 归档与迭代

- `output/` 已 gitignore，报告和日志不会误提交；
- HAR 文件含 Cookie / 业务数据（`*.har` 已忽略），生成完即删；
- casegen 的 `--force` 覆盖产物在 `output/gen_cases_backups/`，需要回滚时从那里取；
- 每次升级依赖或改 `core/` 后，先跑 `python -m pytest selftest` 再跑业务；
- 用例数量变化后，重跑 `--collect-only` 核对数量，避免“文件没被扫到”的静默漏测。

### 常见问题

| 现象 | 排查 |
|---|---|
| 启动即 `ImportError: No module named 'plugins'`（裸 `pytest` 命令） | 改用 `python -m pytest`；或先执行 `$env:PYTHONPATH="."` 再用 `pytest`。裸 `pytest` 不会把项目根目录加入 `sys.path`，导致积木装载器与清单中的 `plugins.*` 模块无法被导入 |
| `python -m pytest` 只跑了十几条，自检没跑 | 正常：自测在 `selftest/`，默认不收集；需要时显式 `python -m pytest selftest` |
| `selftest` 里 `test_http_heal_unit.py` 全部 skipped | 只有当积木**模块文件被删除/移走**时才会跳过；用 `APITEST_PLUGINS_OFF` 临时禁用不会跳过（模块仍可导入，测试照常运行） |
| `Connection refused / timed out` | 没连内网/VPN，或 `config/<env>.yaml` 的 base_url 不对 |
| 全部用例 401/403 | `.env` 里的登录凭据失效；看日志里登录接口的返回 |
| 收集期就报错 | YAML 写错：看报错指明的字段（api 未登记 / 断言为空 / marker 未注册等） |
| 启动报 `积木 xxx 加载失败` | 该积木代码或依赖有问题；临时绕过：注释 `plugins.yaml` 对应行，或 `APITEST_PLUGINS_OFF=短名` |
| 中文用例名显示成 `\uXXXX` | terminal_cn 积木被拔出，或终端不是 UTF-8 |
| 不想生成报告 | 不装报告依赖即可；也可注释 `plugins.yaml` 中 reporting 行 |
| `schema` 断言失败：Schema 文件不存在 | `test_data/schemas/` 下缺少对应文件，或 `schema_file` 拼写错误（先跑 `pytest selftest/test_assets.py` 可在离线阶段发现） |

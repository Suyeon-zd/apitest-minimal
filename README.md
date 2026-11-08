# 接口测试框架使用手册

数据驱动（YAML 写用例）+ 接口注册中心（method/path 统一登记）+ 积木化（非核心能力按需插拔）的 pytest 接口测试框架。

**写一条接口测试 = 登记接口 → 写一个 YAML → 跑 pytest**，不需要写 Python 代码。

## 目录

1. 快速开始（5 分钟跑通）
2. 写用例
3. 运行与结果审查
4. 多环境与变量
5. 批量生成用例（casegen）
6. 积木机制
7. 参考手册（目录结构 / 架构 / 模块 / 命令 / FAQ）

---

## 1. 快速开始（5 分钟跑通）

### 1.1 先认识 4 个位置

| 位置 | 作用 | 你什么时候会改它 |
|---|---|---|
| [api/endpoints.yaml](api/endpoints.yaml) | 接口注册中心：`模块.接口` → `method/path` | 新增被测接口时 |
| `test_data/` | 用例 YAML（递归扫描，一个列表项 = 一条用例） | 写/改用例时 |
| `config/<env>.yaml` | 环境配置：base_url、timeout、`case_vars` | 切环境、加变量时 |
| `selftest/` | 框架自检（不联网，默认不参与业务收集） | 改框架或排查问题时 |

### 1.2 安装

要求 Python 3.10+，在项目根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -r requirements-reporting.txt   # 可选：需要 HTML/Allure 报告时再装

copy .env.example .env
# 编辑 .env，填入 CLIENT_ID / ENCRYPT_KEY / LOGIN_PAYLOAD
```

`.gitignore` 已忽略 `.env`，密钥不会被提交。

### 1.3 跑通三步

```powershell
python -m pytest selftest          # 1) 离线自检：框架 + 用例资产 + 积木清单，不联网
python -m pytest --collect-only    # 2) 收集校验：不登录、不发请求，只确认用例能被收集
python -m pytest -m smoke          # 3) 联网执行：登录 + 真实请求 + 断言
```

三层的分工：第 1 层失败 = 框架或资产有问题；第 2 层失败 = YAML 写错；第 3 层失败 = 环境/凭据/业务有问题。详细说明见第 3 节。

> **必须用 `python -m pytest`，不要用裸 `pytest`。** 裸命令不会把项目根目录加入 `sys.path`，积木装载器和 `plugins.*` 模块会导入失败。

### 1.4 最小示例

`system.org_list` 已登记，直接写一份 YAML 就能跑：

```yaml
# test_data/demo/first_case.yaml
- api_name: "组织列表-我的第一条用例"
  api: system.org_list
  payload: parentId=0
  markers: [smoke]
  assert:
    - {type: status_code, expected: 200}
    - {type: json_path, path: code, expected: 200}
    - {type: field_type, path: data, expected: list}
```

```powershell
python -m pytest --collect-only    # 数量 +1
python -m pytest -k "第一条用例"
```

跑通后，按第 2 节写自己的用例。

---

## 2. 写用例

### 2.1 三个概念

| 概念 | 写在哪 | 说明 |
|---|---|---|
| 接口 | `api/endpoints.yaml` | method/path 的唯一事实来源，后端改路径只改这里 |
| 用例 | `test_data/**/*.yaml` | 一个顶层列表项 = 一条用例，`api` 引用注册中心的 key |
| 断言 | 用例的 `assert` 列表 | 至少一条强断言（只有 `status_code` 会被收集期拒绝） |

### 2.2 第一步：登记接口

```yaml
system:                     # 业务模块
  get_info:                 # 接口名 → 引用 key 为 system.get_info
    method: GET
    path: /system/user/getInfo
    # headers:              # 可选：接口专属请求头（与全局 Authorization 合并）
    #   X-Tenant-Id: ${TENANT_ID}
    # body_type: json       # 可选：json（默认）或 data（原始文本/表单）
```

`path` 支持运行时变量，例如 `/asset/${asset_id}`，变量来自 `config/<env>.yaml` 的 `case_vars` 或前置用例 `extract`。

### 2.3 第二步：写用例 YAML

```yaml
- api_name: "查询用户信息-冒烟"        # 用例名，同时是 pytest id，必须全局唯一
  api: system.get_info                # 引用注册中心的 key
  markers: [smoke, p0]                # 可选：分级标记
  payload: pageNum=1&pageSize=10      # 可选：GET=query string，其他=JSON body
  assert:                             # 必填：至少一条强断言
    - {type: status_code, expected: 200}
    - {type: json_path, path: code, expected: 200}
    - {type: exists, path: data.user, expected: true}
```

POST 默认发 JSON：

```yaml
- api_name: "新增资产"
  api: asset.create
  payload:
    name: "测试设备"
    type: "server"
  assert:
    - {type: json_path, path: code, expected: 200}
```

需要发原始文本/表单时，用例级 `body_type: data` 覆盖注册中心配置：

```yaml
- api_name: "提交加密报文"
  api: system.submit
  body_type: data
  payload: "encrypted-string-do-not-parse"
  assert:
    - {type: json_path, path: code, expected: 200}
```

### 2.4 用例字段参考

| 字段 | 必填 | 说明 |
|---|---|---|
| `api_name` | 是 | 用例名 / pytest id，全局唯一；`depends_on` 也用它引用 |
| `api` | 是 | 注册中心 key，如 `system.get_info` |
| `assert` | 是 | 断言列表，至少一条强断言 |
| `payload` | 否 | GET 为 query string；非 GET 为 JSON body（字符串 `"a=1&b=2"` 会自动转 dict） |
| `body_type` | 否 | `json`（默认）/ `data`（原始文本或表单），覆盖注册中心 |
| `headers` | 否 | 用例级额外请求头，与接口专属头合并 |
| `markers` | 否 | `smoke` / `regression` / `p0` / `p1` / `auto` / `skip` |
| `timeout` | 否 | 秒数或 `[连接超时, 读取超时]`，覆盖环境配置 |
| `extract` | 否 | 【跨用例积木】响应字段存为变量：`token: data.accessToken`，后续 `${token}` 引用 |
| `depends_on` | 否 | 【跨用例积木】前置用例名列表，前置失败则跳过 |
| `cleanup` | 否 | 【跨用例积木】结束后调用的清理接口，失败只告警 |

> `extract` 默认只在当前用例内生效；要跨用例传递，执行时加 `--cases-shared`（见 4.3）。

### 2.5 10 种断言类型

| type | 作用 | 典型写法 |
|---|---|---|
| `status_code` | HTTP 状态码（弱断言，不能单独使用） | `{type: status_code, expected: 200}` |
| `json_path` | JSON 路径值比对（点号/方括号/数字索引） | `{type: json_path, path: data.0.id, expected: 100}` |
| `exists` | 字段是否存在 | `{type: exists, path: data.user, expected: true}` |
| `field_type` | 字段类型：`int/str/bool/list/dict/float/null` | `{type: field_type, path: data.id, expected: int}` |
| `list_len` | 数组长度（支持 `gte/lte/eq` 等 operator） | `{type: list_len, path: data, operator: gte, expected: 1}` |
| `contains` | 字符串包含 | `{type: contains, path: msg, expected: 成功}` |
| `header` | 响应头 | `{type: header, field: Content-Type, operator: contains, expected: json}` |
| `each` | 数组每一项的某字段都等于期望值 | `{type: each, path: data, field: type, expected: org}` |
| `schema` | JSON Schema 结构校验（文件放 `test_data/schemas/`） | `{type: schema, schema_file: user.json}` |
| `response_time` | 响应耗时 SLA（秒） | `{type: response_time, operator: lte, expected: 2}` |

完整范例见 [asset_summary.yaml](test_data/asset/asset_summary.yaml)，每条断言一行示例，可当模板复制。

> **结构校验优先用 `schema`。** schema 文件里已有 `required`（字段存在）和 `properties.type`（字段类型），
> 不要再逐字段写 `exists` / `field_type` —— 同一件事做几十遍，只会让用例变长、变难读，接口改字段时还要同步改几十行。

**断言分层（一条用例 4~8 条是健康区间）**

| 层次 | 目的 | 推荐断言 | 条数 |
|---|---|---|---|
| 契约层 | 字段是否齐全、类型是否正确、嵌套结构 | `schema` | 1 |
| 业务层 | 接口是否成功 | `status_code` + `json_path code` | 2 |
| 语义层 | 数据是否符合业务含义 | `list_len` / `each` / `contains` | 1~3 |
| 值层 | 关键值是否正确 | `json_path`（只用于稳定且有业务含义的值） | 0~2 |

三个必须知道的细节：

- `exists` + `field_type` 只在两种情况单独写：**null 值字段**（schema 对 null 不约束类型）、**需要独立定位的关键字段**；
- `each` 遇到空列表会直接通过，所以非空列表必须配一条 `list_len ... gte 1` 兜底；
- `json_path` 期望 `null` 是**恒真断言**（字段被删除时取值同样是 `None`），要表达"字段存在且为 null"，用 `exists` + `field_type: null` 成对写。

### 2.6 写完后自查 6 项

1. 文件扩展名是 `.yaml`（`.yml` 不会被扫描），顶层是列表；
2. `api_name` 全局唯一；
3. `api` 已在 `api/endpoints.yaml` 登记；
4. `assert` 至少一条强断言（不是只有 `status_code`）；结构校验用一条 `schema`，不要逐字段展开 `exists` / `field_type`；
5. `markers` 只用 `pytest.ini` 注册过的值；
6. 跑一次 `python -m pytest --collect-only`，数量符合预期、没有 ERROR。

---

## 3. 运行与结果审查

### 3.1 执行前：三层自检

| 层级 | 命令 | 联网 | 检查内容 | 失败含义 |
|---|---|---|---|---|
| L1 离线自检 | `python -m pytest selftest` | 否 | 框架代码、用例资产、环境配置、积木清单 | 框架或资产有问题 |
| L2 收集校验 | `python -m pytest --collect-only` | 否 | api 登记、id 唯一、断言强度、跨用例引用 | 用例 YAML 写错 |
| L3 联网预检 | `python -m pytest -m smoke` | 是 | 登录换 Token、真实请求、断言 | 环境/凭据/业务有问题 |

不要跳过 L1 直接跑 L3，否则框架 bug 会表现成“接口全挂”，排查方向会跑偏。

**L1 明细（当前 104 条）**

| 文件 | 条数 | 覆盖 |
|---|---|---|
| [test_core_unit.py](selftest/test_core_unit.py) | 71 | 断言引擎、路径取值、变量替换、配置插值、注册中心、脱敏日志、用例模型 |
| [test_http_heal_unit.py](selftest/test_http_heal_unit.py) | 7 | 重试退避、401 重登、鉴权失败开关 |
| [test_casegen_unit.py](selftest/test_casegen_unit.py) | 19 | 类型推断、schema 断言生成、环境变量替换、只读保护、骨架 skip、JSON 离线转 schema |
| [test_assets.py](selftest/test_assets.py) | 7 | 注册中心、3 个环境配置、全部用例可加载、schema 文件、积木清单 |

`test_assets.py` 与业务入口共用 [case_loader.py](core/case_loader.py) 的 `load_cases()`，所以“自检通过的资产”就是“真正执行时加载的资产”。

**L2 正常输出**

```text
testcases/test_regression.py: 15
```

数字应等于 `test_data/**/*.yaml` 里的用例条数。对不上就是有文件没被扫到（比如 `.yml`）或文件为空。

**L3 前置条件**

1. 能访问 `config/<env>.yaml` 的 `base_url`（`dev` 是内网地址，需要 VPN）；
2. `.env` 里的登录凭据有效。

### 3.2 执行：按范围选命令

```powershell
python -m pytest                        # 全量业务回归
python -m pytest -m smoke               # 冒烟
python -m pytest -m "p0 and smoke"      # 标记组合
python -m pytest -m auto                # 只跑 casegen 生成的用例
python -m pytest -m "not auto"          # 只跑手写用例
python -m pytest testcases -k "orgList" # 单接口调试
python -m pytest --env=staging          # 切环境
```

跨用例传值时加 `--cases-shared`；装了 `pytest-xdist` 可加 `-n auto`，但含 `depends_on`/`extract` 的用例会被护栏积木拦下。

完整命令（含自检、纯核心模式）见 7.5。

### 3.3 结果产物

| 产物 | 位置 | 用途 |
|---|---|---|
| HTML 报告 | `output/reports/report.html` | 逐条结果、失败信息、断言明细 |
| Allure 原始数据 | `output/reports/allure-results/` | 需要趋势/附件时用 allure 命令生成网页 |
| 运行日志 | `output/logs/run.log` | 请求/响应（已脱敏）、重试、登录、堆栈 |
| 控制台 | 终端实时输出 | 进度、失败摘要、live log |

排查顺序：**HTML 的失败用例名 → 该用例的断言明细 → `run.log` 的请求/响应**。不要一上来翻日志全文。

### 3.4 失败归因：四类信号

| 类型 | 典型信号 | 判断依据 | 处理 |
|---|---|---|---|
| 环境层 | 所有用例同时失败；`ConnectionError`/`Timeout`/`401` | L1 全绿，L3 全挂 | 查 VPN、`base_url`、`.env`、服务是否启动 |
| 框架层 | 大量不相关用例同时失败；堆栈在 `core/`；L1 也红 | `pytest selftest` 有失败 | 先修框架，再重跑业务 |
| 资产层 | 收集期 ERROR；`Schema 文件不存在`；断言数异常少 | `--collect-only` 就报错 | 修 YAML / 补 schema / 补登记 |
| 业务层 | 仅个别用例失败；断言明细指出具体字段/值 | 环境、框架、资产都正常 | 判断是接口变更还是断言过期 |

> **ERROR 不等于业务失败。** 登录失败时，每条业务用例都会显示 `ERROR`（session fixture 阶段异常）。例如 14 条用例全部 `ERROR … ReadTimeout`，含义是“1 次登录失败导致 14 条未执行”，不是 14 个业务缺陷。

### 3.5 人工审查清单

`passed` 数字不能直接当结论，至少确认以下 8 项：

1. **用例总数**是否等于预期（与上次相比有无意外增减）；
2. **失败是否集中**在同一接口/模块（集中 = 环境或该模块变更；分散 = 框架或数据问题）；
3. **断言是否真的校验业务**：只有 `status_code` + `code` 的用例属于弱校验，容易假通过；
4. **自动生成用例的 TODO** 是否已处理（`# TODO:` 表示当时调用异常，用例已自动标 `skip`）；
5. **skipped 是否合理**：`depends_on` 前置失败会级联 skip，不要当成通过；
6. **响应时间**是否异常（`response_time` 断言或报告耗时）；
7. **日志脱敏**是否生效（`run.log` 不应出现完整 Token / 密码）；
8. **与上一轮结果对比**：新增失败优先看。

审查结论只有三种：**通过**（归档）、**需修复**（代码/环境/资产）、**需调整预期**（业务确实变更，更新断言并说明原因）。

> 生成用例里的骨架条目会自动带 `skip` 标记。看到 skipped 不要当成通过——它表示“还没有可执行断言”，补完 payload/断言后要删除 `skip`。schema 断言默认启用，详见 5.9。

### 3.6 归档与迭代

- `output/` 已 gitignore，报告和日志不会误提交；
- HAR 含 Cookie / 业务数据（`*.har` 已忽略），生成完即删；
- `--force` 覆盖的产物在 `output/gen_cases_backups/`，需要回滚时从那里取；
- 升级依赖或改 `core/` 后，先跑 `python -m pytest selftest`，再跑业务；
- 用例数量变化后重跑 `--collect-only` 核对，避免“文件没被扫到”的静默漏测。

---

## 4. 多环境与变量

### 4.1 环境切换

| 文件 | 用途 |
|---|---|
| `config/dev.yaml` | 开发环境（默认） |
| `config/staging.yaml` | 预发环境 |
| `config/prod.yaml` | 生产环境 |
| `.env` | 凭据与密钥，`${VAR}` 从系统环境变量注入 |

```powershell
python -m pytest --env=staging
```

### 4.2 变量从哪来

优先级从低到高：

1. `config/<env>.yaml` 的 `case_vars`（全局默认值）；
2. 前置用例 `extract` 提取的值（同名覆盖）；
3. 用例 YAML 中 `${VAR}` 的运行时引用。

```yaml
# config/dev.yaml
case_vars:
  DEPT_ID: "100"
```

```yaml
# 用例中引用
payload: deptId=${DEPT_ID}&pageNum=1
```

### 4.3 隔离还是共享

| 模式 | 触发方式 | 行为 | 适用场景 |
|---|---|---|---|
| 默认：隔离 | 直接运行 | 每条用例一份 `case_vars` 深拷贝，互不污染 | 绝大多数场景 |
| 共享 | 加 `--cases-shared` | 所有用例共用一份变量 dict，`extract` 跨用例可见 | 用例 A 提取 → 用例 B 引用 |

要点：

- `extract` 默认不跨用例生效；检测到 `extract` 却没开 `--cases-shared` 时，收集期会打 WARNING；
- `depends_on` 不受隔离影响，依赖门控走独立的结论记录表；
- 共享模式下用例顺序敏感（按 `test_data` 文件路径排序），并行时会互相踩踏；
- 拔出 `case_extras` 积木后等价于“共享”模式。

---

## 5. 批量生成用例（casegen）

### 5.1 什么时候用

`plugins/casegen/` 是**独立命令行工具**（不是 pytest 积木），用于接口多、逐个手写太慢的场景。核心思路是**契约快照**：真实调用一次接口，把响应结构固化成断言。

适合：只读查询类接口（列表、详情、字典、看板）。
不适合直接自动化：导出、上传、批量删除、审批、发布等写接口（需要人工判断副作用）。
**无论是否自动生成，中间都必须人工审阅。**

### 5.2 端到端流程

```text
浏览器抓包 → xxx.har
    │  har2list.py：过滤静态资源/第三方域名/登录接口，按 method+path 去重
    ▼
接口清单 case_list_from_har.txt
    │  ★ 人工审阅：删危险接口、把易变 ID 换成 ${VAR}
    ▼
gen_cases.py：真实调用 → 一条 schema（字段存在 + 类型）+ 业务语义断言
    ▼
test_data/<模块>/<模块>_gen.yaml + test_data/schemas/gen_*.json
    │  ★ 人工收尾：markers、TODO 骨架、schema 裁剪
    ▼
python -m pytest -m auto
```

> HAR 含登录 Cookie / Authorization / 业务数据，`*.har` 已被 gitignore；不要提交，用完即删。

### 5.3 第一步：抓 HAR

前提：浏览器能访问被测环境且**已登录**。

1. `F12` → **Network**；
2. 勾选 **Preserve log**，建议勾选 **Disable cache**；
3. 过滤器选 **Fetch/XHR**；
4. 把目标模块逐页点一遍，让接口都发出来；
5. 请求列表空白处右键 → **Save all as HAR with content**（必须带 `with content`，否则没有响应体）；
6. 保存为 `plugins/casegen/dev_20261106.har`（位置任意，命令按项目根目录找）。

> `har2list` 只保留 URL 以 `config/<env>.yaml` 的 `base_url` 开头的请求。HAR 域名不一致时用 `--base-url`，否则结果是“保留 0 条”。

### 5.4 第二步：HAR → 接口清单

```powershell
# 默认读 config/dev.yaml 的 base_url
python -m plugins.casegen.har2list --har plugins/casegen/dev_20261106.har

# 指定环境 / 直接指定 base_url / 自定义输出
python -m plugins.casegen.har2list --har plugins/casegen/staging.har --env staging
python -m plugins.casegen.har2list --har plugins/casegen/xxx.har --base-url http://10.148.242.205/dev-api
python -m plugins.casegen.har2list --har plugins/casegen/xxx.har --out plugins/casegen/my_list.txt
```

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--har` | 是 | — | HAR 文件路径 |
| `--env` | 否 | `dev` | 从 `config/<env>.yaml` 读 `base_url` |
| `--base-url` | 否 | — | 直接指定，优先级高于 `--env` |
| `--out` | 否 | `plugins/casegen/case_list_from_har.txt` | 清单输出路径 |

固定转换规则：

- 过滤非 `base_url` 前缀、静态资源、`/auth/login`；
- 同 `method + path` 去重，保留参数信息量最大的一条；
- 模块名 = path 第一段；接口名 = 末段转 `snake_case`，重名时带前一段（`v2` 等版本段跳过）；
- GET query、POST/PUT 的 JSON/表单 body 写成 `payload`；
- multipart 上传无法还原 body，只输出 `# TODO` 注释。

统计示例：`总请求 15 条 | 过滤后候选 15 条 | 同 method+path 合并 1 条 | 清单接口 14 个`。候选数是请求数，接口数是去重后写入文件的行数。

### 5.5 第三步：人工审阅清单（必做）

清单已按模块分组，条目间留空行，`#` 注释、空行、行首空格都不影响解析。两种格式：

```text
# 格式1：已在 api/endpoints.yaml 登记（推荐）
system.org_list
asset.instock_list | pageNum=1&pageSize=10

# 格式2：新接口，脚本会自动登记进 endpoints.yaml
asset.secret_create | POST | /asset/secret/create | {"name": "测试资产"}
```

审阅至少做三件事：

1. **删掉不该自动跑的接口**：导出、下载、批量删除、审批、发布等；`DELETE` 在生成阶段不会真实调用，但生成的骨架用例在执行阶段会真的发出 DELETE 请求，建议直接从清单删掉；
2. **替换易变数据**：`deptId=100`、`groupId=9c18...` 改成 `${DEPT_ID}` / `${GROUP_ID}`，变量在 `config/<env>.yaml` 的 `case_vars` 定义；
3. **确认命名**：`模块.接口名` 是唯一键，后续 `api` 字段引用它。

格式约束：

- 格式1 的 key 必须已登记，否则命令直接报错；
- 格式2 会先追加登记（备份 `.yaml.bak`）；若已登记，以注册中心为准，清单写法不一致只打 WARNING；
- `--dry-run` 下不登记、不写文件。

### 5.6 第四步：清单 → 基线用例 + Schema

```powershell
# 预览：真实调用接口，但不写任何文件
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt --dry-run

# 正式生成
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt

# 常用变体
python -m plugins.casegen.gen_cases --list ... --env staging
python -m plugins.casegen.gen_cases --list ... --force          # 整文件覆盖（先备份）
python -m plugins.casegen.gen_cases --list ... --strict-schema  # additionalProperties=false
```

运行前提：`.env` 凭据有效，网络能连到 `base_url`（脚本会先登录，再逐个真实调用）。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--list` | 见下 | 接口清单 txt（在线模式；与 `--from-json` 二选一） |
| `--from-json` | — | 离线模式：读响应 JSON 文件生成 schema，不登录、不请求接口 |
| `--key` | — | 配合 `--from-json`：`模块.接口名`，决定 schema 文件名 |
| `--env` | `dev` | 使用 `config/<env>.yaml` 的 base_url / 凭据 / timeout |
| `--out` | `test_data` | 用例输出根目录 |
| `--schemas-dir` | `test_data/schemas` | Schema 输出目录 |
| `--dry-run` | 关 | 只调用（只读模式）+ 预览，不写文件 |
| `--allow-write` | 关 | 允许真实调用 POST/PUT/PATCH；默认只生成骨架 |
| `--force` | 关 | 整文件覆盖，覆盖前备份到 `output/gen_cases_backups/` |
| `--strict-schema` | 关 | schema 的 `additionalProperties=false` |
| `--default-markers` | `auto` | 自动用例默认标记，逗号分隔 |

调用安全策略：

| 情况 | 行为 |
|---|---|
| `GET` | 真实调用（只读，安全） |
| 非 GET | 默认不调用（只读模式），生成骨架 + `TODO`，并自动打 `skip` 标记 |
| `--allow-write` 后的 POST/PUT/PATCH 且有 payload | 真实调用（会写数据） |
| POST/PUT/PATCH 无 payload | 不调用，生成骨架 + `skip` |
| `DELETE` | 永不调用，只生成骨架 + `skip` |
| 响应异常（HTTP ≠ 200 / 非 JSON / code ≠ 200） | 降级为骨架 + `skip` |

产物：

```text
test_data/<module>/<module>_gen.yaml      # 用例
test_data/schemas/gen_<key>.json          # 响应结构 Schema（含 _meta）
```

> Schema 断言固定从 `test_data/schemas/` 读取，`--schemas-dir` 一般保持默认。
> 生成文件写入 `test_data/` 后会被自动扫描，不需要改 Python 代码。

重复生成：

- 默认按 `api` 去重，已存在的跳过，人工修改不会被覆盖；
- `--force` 整文件重写，旧文件备份到 `output/gen_cases_backups/`；
- 只重生成单个接口：从 `_gen.yaml` 删掉该条，再跑一次（不加 `--force`）。

**接口暂时调不通：只有响应 JSON 时（离线模式）**

开发给了新的响应样例、但环境里接口还没部署或调不通时，不必等接口可用：

```powershell
# 1) F12 → Response → Copy response，存为 resp.json（纯 JSON，不要带 DevTools 包装）
# 2) 生成 schema：不登录、不请求接口
python -m plugins.casegen.gen_cases --from-json resp.json --key asset.demo_detail
```

输出：

```text
已生成 Schema: test_data/schemas/gen_asset_demo_detail.json

将以下断言粘贴到对应用例：
  assert:
    - {expected: 200, type: status_code}
    - {expected: 200, path: code, type: json_path}
    - {expected: true, path: data.remark, type: exists}
    - {expected: 'null', path: data.remark, type: field_type}
    - {schema_file: gen_asset_demo_detail.json, type: schema}
```

- `--key` 决定文件名（`gen_<key>.json`），必须是 `模块.接口名`；
- 只写 schema 并打印断言块，不改 `endpoints.yaml`、不生成用例 YAML；
- 接口恢复后建议再用 `--list` 跑一次在线生成，确认实际结构与样例一致；
- 临时的 `resp.json` 用完及时删除（含业务数据，且 `*.json` 未被 gitignore 忽略）。

### 5.7 第五步：生成后收尾（必须做）

生成结果只是“结构基线”，直接全量跑通常要调整：

1. **markers**：默认 `[auto]`，核心链路补 `smoke, p0`，普通接口补 `p1`；骨架用例会自动带 `skip`，补完后删除；
2. **api_name**：它是 pytest id 和 `depends_on` 的引用键，改名要同步检查依赖；
3. **payload**：生成器会把与 `case_vars` 值一致的参数/路径段自动换成 `${VAR}`；剩余告警项手工处理；
4. **TODO 骨架**：缺 payload 的写接口补 payload 并删除 `skip`；DELETE/上传/导出建议人工写 `cleanup` 或不纳入自动回归；
5. **断言精简**：字段存在性/类型已由 schema 一条覆盖，不需要再补 `exists` / `field_type`；只为 null 字段、或需要独立失败定位的关键字段单独加；语义层建议补 `list_len` / `each`；
6. **Schema 严格度**：schema 断言默认启用，生成的 schema 把观察到的字段都列为 `required`，字段常增减时手工裁剪；
7. **校验 + 执行**：

```powershell
python -m pytest --collect-only
python -m pytest -m auto
```

### 5.8 产物目录

| 路径 | 是否参与 pytest | 说明 |
|---|---|---|
| `test_data/<module>/<module>_gen.yaml` | 是 | 正式产物，自动成为用例 |
| `test_data/schemas/gen_*.json` | 间接 | 被 schema 断言引用 |
| `plugins/casegen/case_list*.txt` | 否 | 接口清单（`case_list.example.txt` 是格式示例） |
| `plugins/casegen/generated/` | 否 | 历史样例，要复用就拷到 `test_data/` |
| `output/gen_cases_backups/` | 否 | `--force` 覆盖前的备份 |

### 5.9 已修复项与已知限制

本轮已修复（无需再手工处理）：

| 原问题 | 现在的行为 |
|---|---|
| 逐字段 `exists`/`field_type` 与 schema 重复 | 生成器只输出「一条 schema + 少量业务语义断言」；存量 16 条用例的断言总量从 422 条降到 134 条 |
| `data` 为 null 时类型断言失真 | `_type_name(None)` 返回 `"null"`；null 字段由 `exists` + `field_type: "null"` 成对断言（schema 不约束 null 类型） |
| Schema 断言被注释、文件成死产物 | 14 条 schema 断言已全部启用，生成器默认写入并启用 |
| 参数/路径写死 | 与 `case_vars` 值完全一致的参数/路径段自动替换成 `${VAR}`；剩余疑似 ID 打 WARNING |
| 写接口风险 | 默认只读：非 GET 不调用；`--allow-write` 才允许写；骨架用例自动带 `skip`，不会跑红 |

仍需人工确认的限制：

| 级别 | 问题 | 表现 | 处理 |
|---|---|---|---|
| 中 | HAR 请求头丢失 | 只保留 query/body，自定义头（租户、签名）不会带出 | 在 `endpoints.yaml` 的 `headers` 手工补 |
| 中 | 重复 query 参数只保留第一个 | `ids=1&ids=2` 变成 `ids=1` | 改成 YAML dict + 列表值：`payload: {ids: [1, 2]}` |
| 中 | 用例 id 含生成日期 | `--force` 跨天重生成后 pytest id 变化，影响历史趋势和 `depends_on` | 优先用默认“跳过已存在”；必要时手工固定 api_name |
| 中 | `msg` 文案断言默认生效 | 后端改文案即失败 | 不稳定时删除或改成 `contains` |
| 低 | 空列表导致 `list_len` 失败 | 生成时观察到非空列表才写 `list_len gte 1`，运行时若返回空数组会失败 | 数据可能为空时把 `expected` 调成 0 或删除该断言 |
| 低 | multipart 上传被静默丢弃 | 只留 `# TODO` 注释，解析时跳过 | 人工补 payload 或在清单中删掉 |
| 低 | dry-run 预览不含 schema 行 | 预览与真实产物略有差异 | 以正式生成结果为准 |
| 低 | `.yaml.bak` 只有一个槽位 | 连续生成会覆盖上一次备份 | 重要改动前手工复制 `endpoints.yaml` |

### 5.10 casegen 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `HAR 中没有请求记录` | 导出的是页面/站点 HAR 或空 HAR；确认用 *Save all as HAR with content* |
| 转换后“保留 0 条” | HAR 域名与 `base_url` 不一致；用 `--base-url` |
| 生成时登录失败 / 超时 | `.env` 凭据无效或网络不通（与跑 pytest 的要求相同） |
| 生成了骨架 + TODO | 正常：非 GET（默认只读）、缺 payload、异常响应都不固化断言；骨架自动带 `skip` |
| 生成结果里有 skipped | 骨架用例的预期状态；补完 payload/断言后删除 `markers` 里的 `skip` |
| 接口调不通，只有响应 JSON | 用离线模式：`python -m plugins.casegen.gen_cases --from-json resp.json --key 模块.接口名`（见 5.6） |
| 报“接口未在 endpoints.yaml 登记” | 清单用了格式1 但接口没登记；改格式2 或先登记 |
| `--force` 后人工断言不见了 | 从 `output/gen_cases_backups/` 恢复；下次优先“删单条再重跑” |
| schema 断言报文件不存在 | `gen_*.json` 没生成，或 `--schemas-dir` 改到了别处 |
| 收集期报用例 id 重复 | 两个 YAML 的 `api_name` 相同；改成唯一名 |

---

## 6. 积木机制

### 6.1 是什么

非核心能力全部在 `plugins/`，由 [plugins.yaml](plugins.yaml) 清单控制：**注释掉某行 = 拔出，取消注释 = 插入**，不用改代码。

当前积木：

| 积木 | 能力 |
|---|---|
| `plugins.reporting.plugin` | HTML + Allure 报告 |
| `plugins.case_extras.plugin` | `depends_on` / `extract` / `cleanup` / `--cases-shared` |
| `plugins.xdist_guard` | 并行护栏（装了 xdist 才生效） |
| `plugins.terminal_cn.plugin` | Windows UTF-8 + 中文用例名 |
| `plugins.log_setup.plugin` | 文件日志 `output/logs/run.log` |
| `plugins.http_heal.plugin` | 重试退避 / 401 自动重登 / `--fail-on-auth-expired` |

装载原理：[pytest.ini](pytest.ini) 只挂 `-p plugins.loader`，装载器读清单动态注册。

### 6.2 临时禁用

```powershell
$env:APITEST_PLUGINS_OFF="reporting"; python -m pytest   # 本次不加载报告积木
$env:APITEST_PLUGINS_OFF="*"; python -m pytest           # 纯核心模式
Remove-Item Env:APITEST_PLUGINS_OFF                       # 用完清掉
```

积木提供的可选开关：

| 开关 | 积木 | 作用 |
|---|---|---|
| `--cases-shared` | case_extras | 跨用例共享变量（`extract` 才能传给后面的用例） |
| `--fail-on-auth-expired` | http_heal | 重登后仍 401/403 时直接抛错 |

HTTP 自愈参数在环境配置里：

```yaml
retry: {times: 0, backoff: 0.5, retry_on: [timeout, connection]}
auth_relogin_401: true
auth_fail_on_expired: false
```

### 6.3 开发一个新积木

1. **需要被核心调用？** 需要 → 在 `core/` 建端口（接口 + 空实现 + `set_xxx`），照抄 [reporting.py](core/reporting.py)；只挂 pytest 钩子的跳过；
2. **实现**：`plugins/<name>/plugin.py`，模块末尾注册端口，或写 pytest hook；可选依赖用 `try/except ImportError` 降级；
3. **登记**：`plugins.yaml` 加一行；
4. **依赖**：写进 `requirements-<name>.txt`，不进核心 `requirements.txt`；
5. **单测**：放 `selftest/test_<name>_unit.py`，文件头 `pytest.importorskip(...)`。

红线：积木可以 import core；**core/ 与 conftest.py 永远不 import plugins**。

---

## 7. 参考手册

### 7.1 目录结构

```text
apitest-minimal/
├── core/                  # 核心骨架（不含可选能力）
│   ├── config_loader.py   #   配置加载：.env 注入 + ${VAR} 插值
│   ├── base_client.py     #   HTTP 裸请求：URL 拼接 / Token 注入 / 脱敏日志
│   ├── auth_manager.py    #   登录鉴权、Token 缓存与刷新
│   ├── assertions.py      #   10 种断言
│   ├── case_model.py      #   用例模型：YAML → TestCase
│   ├── case_loader.py     #   用例加载与收集期校验（业务入口/自检共用）
│   ├── constants.py       #   markers 白名单
│   ├── utils.py           #   ${var} 替换
│   ├── reporting.py       #   报告端口（空实现）
│   ├── case_extras.py     #   跨用例端口（空实现）
│   └── http_heal.py       #   HTTP 自愈端口（空实现）
├── api/
│   ├── endpoints.yaml     # ★ 接口注册中心
│   └── registry.py
├── config/                # dev.yaml / staging.yaml / prod.yaml
├── test_data/             # ★ 业务用例 YAML（当前 15 条）
│   └── schemas/           #   JSON Schema
├── testcases/
│   └── test_regression.py #   业务执行入口（唯一被默认收集）
├── selftest/              # 框架自检（默认不收集）
│   ├── test_core_unit.py
│   ├── test_http_heal_unit.py
│   └── test_assets.py
├── plugins/               # 积木层
│   ├── loader.py          #   装载器（pytest.ini 中唯一的 -p）
│   ├── reporting/ case_extras/ http_heal/ log_setup/ terminal_cn/
│   ├── xdist_guard.py
│   └── casegen/           #   命令行工具（非 pytest 积木）
├── conftest.py            # 全局 fixture
├── pytest.ini
├── plugins.yaml           # ★ 积木清单
├── requirements.txt
├── requirements-reporting.txt
└── .env.example
```

`output/` 默认存放报告与日志（已 gitignore），可用 `APITEST_OUTPUT_DIR` 改路径。

### 7.2 分层架构

```text
数据层   api/endpoints.yaml · test_data/**/*.yaml · config/*.yaml · test_data/schemas/*.json
  │
核心层   conftest.py · testcases/test_regression.py · core/*（永远存在，不含可选能力）
  │  通过端口调用
端口层   core/reporting.py · core/case_extras.py · core/http_heal.py（接口 + 空实现）
  │  积木加载即注册
积木层   plugins/ + plugins.yaml（注释即拔出）
```

依赖只从上层指向下层；`core/` 与 `conftest.py` 不 import `plugins`。

### 7.3 核心模块职责

| 文件 | 职责 |
|---|---|
| [conftest.py](conftest.py) | 注册 `--env`；提供 `config` / `auth` / `api_client` / `_session_variables` / `variables` fixture |
| [test_regression.py](testcases/test_regression.py) | 数据驱动入口：加载用例 → 参数化 → 构造请求 → 断言 |
| [config_loader.py](core/config_loader.py) | 读配置、注入 `.env`、`${VAR}` 插值 |
| [base_client.py](core/base_client.py) | URL 拼接、Token 注入、脱敏日志 |
| [auth_manager.py](core/auth_manager.py) | 登录、Token 缓存与刷新 |
| [assertions.py](core/assertions.py) | 10 种断言实现 |
| [case_model.py](core/case_model.py) | `TestCase` 模型与收集期校验 |
| [case_loader.py](core/case_loader.py) | 扫描 YAML、id 唯一、api 登记校验 |
| [utils.py](core/utils.py) | `${var}` 替换 |
| [registry.py](api/registry.py) | 加载 `endpoints.yaml` → `Endpoint(method/path/headers/body_type)` |

### 7.4 端口 ↔ 积木

| 端口（空实现） | 积木（真实实现） | 拔出后 |
|---|---|---|
| [reporting.py](core/reporting.py) | `plugins/reporting` | 不产报告，只打印 pytest 输出 |
| [case_extras.py](core/case_extras.py) | `plugins/case_extras` | `depends_on`/`extract`/`cleanup` 退化为空操作 |
| [http_heal.py](core/http_heal.py) | `plugins/http_heal` | 裸请求：不重试、401 不重登 |
| 无端口（pytest hook） | `log_setup` / `terminal_cn` / `xdist_guard` | 无文件日志 / 中文名不美化 / 并行不设护栏 |

### 7.5 命令速查

```powershell
# ---- 业务用例（需网络 + 凭据）----
python -m pytest                                    # 全量
python -m pytest -m smoke                           # 冒烟
python -m pytest -m "p0 and smoke"                  # 标记组合
python -m pytest -m auto                            # 自动生成用例
python -m pytest -m "not auto"                      # 手写用例
python -m pytest -k "关键字"                         # 按用例名过滤
python -m pytest --env=staging                      # 切环境
python -m pytest --collect-only                     # 只收集，不发请求
python -m pytest -v                                 # 详细输出

# ---- 框架自检（离线）----
python -m pytest selftest                           # 全部自检（当前 104 条）
python -m pytest selftest/test_assets.py            # 只查用例资产
python -m pytest selftest testcases                 # 自检 + 业务

# ---- 纯核心模式 ----
$env:APITEST_PLUGINS_OFF="*"; python -m pytest selftest/test_core_unit.py selftest/test_assets.py -q
Remove-Item Env:APITEST_PLUGINS_OFF

# ---- casegen ----
python -m plugins.casegen.har2list --har plugins/casegen/xxx.har
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt --dry-run
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt
python -m plugins.casegen.gen_cases --list plugins/casegen/case_list_from_har.txt --allow-write  # 允许写接口
python -m plugins.casegen.gen_cases --from-json resp.json --key asset.demo_detail  # 只有 JSON，离线生成 schema
```

### 7.6 常见问题

| 现象 | 排查 |
|---|---|
| `ImportError: No module named 'plugins'` | 用了裸 `pytest`；改用 `python -m pytest` |
| `python -m pytest` 只跑十几条 | 正常，自测在 `selftest/`，默认不收集 |
| `test_http_heal_unit.py` 全部 skipped | 积木模块文件被删除/移走；`APITEST_PLUGINS_OFF` 临时禁用不会跳过 |
| `Connection refused / timed out` | 没连 VPN，或 `base_url` 不对 |
| 全部用例 401/403 | `.env` 登录凭据失效；看日志里登录响应 |
| 收集期报错 | YAML 写错：api 未登记 / 断言为空 / marker 未注册 |
| 启动报“积木 xxx 加载失败” | 积木代码或依赖有问题；注释 `plugins.yaml` 对应行，或 `APITEST_PLUGINS_OFF=短名` |
| 中文用例名显示 `\uXXXX` | terminal_cn 积木被拔出，或终端不是 UTF-8 |
| 不想生成报告 | 不装报告依赖，或注释 `plugins.yaml` 的 reporting 行 |
| schema 断言报文件不存在 | `test_data/schemas/` 缺文件或 `schema_file` 拼错；先跑 `pytest selftest/test_assets.py` |

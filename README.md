# 接口自动化测试 使用说明书

## 一、项目定位与架构总览

本项目聚焦 **Web 接口自动化测试**：YAML 数据驱动的接口回归测试，无需写 Python 代码，入口为 `pytest`。

**依赖方向（低耦合设计）：**

```
共享：config/*.yaml（环境配置，敏感项用 ${VAR} 占位）

conftest → core/{config_loader, base_client, auth_manager, assertions, logger}
testcases → api/{endpoints.yaml, registry} → test_data + schemas
```

- 接口定义与用例分离：接口路径集中在 `api/endpoints.yaml`（注册中心），用例只写接口逻辑名，收集期校验
- 分层清晰：`core/` 提供 HTTP 请求 / 鉴权 / 断言 / 日志基础能力，`api/` 提供注册中心，`testcases/` 负责数据驱动执行

### 核心特性

- 多环境切换（dev / staging / prod）
- **接口注册中心**：接口路径集中登记（`api/endpoints.yaml`），用例只引用逻辑名，收集期校验，支持接口专属头与多种请求体类型
- 密钥外置（YAML 占位符 + `.env` / CI 环境变量注入，不明文入库）
- 用例分级（smoke / regression / p0 / p1 标记，CI 按需执行）
- Token 自动登录 + 过期自动刷新
- 系统化断言引擎（10 种断言类型，含业务码/结构/响应时间 SLA）
- 用例自动生成（契约快照）：接口清单 → 真实调用 → 解析响应 → 基线用例 + Schema，未登记接口自动补进注册中心（见 3.6）
- 跨用例变量传递（extract + ${var}）
- JSON Schema 结构校验
- Allure + pytest-html 双报告
- 统一日志（控制台 + 按天滚动文件）

---

## 二、快速开始

> 新员工/第一次接触本项目：直接跳 [2.3 全流程指引](#23-新员工上手从零到跑通接口测试全流程)，五步从装环境到跑通测试。

### 2.1 安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 末尾的**可选增强**段已列出 `pytest-xdist`（并行）、`pytest-rerunfailures`（失败重试）、
`pytest-cov`（覆盖率）、`ruff`（静态检查）——`pip install -r requirements.txt` 会一并装上；
不想装也不影响框架跑通（对应命令不用即可）。

另有少量按需安装项：

```bash
# Allure 命令行（看可视化报告需要）：Windows 下载 allure-commandline 配环境变量，或 scoop install allure
```

### 2.2 配置环境

编辑 `config/dev.yaml`（staging/prod 同理），**启用前必须改成真实凭据的项**：

| 字段 | 说明 | 示例 |
|---|---|---|
| `base_url` | 接口基础地址 | `http://10.148.242.205/dev-api` |
| `clientid` | 登录请求头 clientid | `e5cd7e48...` |
| `encrypt-key` | 登录请求头 encrypt-key | `UpuvgcEs...` |
| `Content-Type` | 登录请求 Content-Type | `text/plain` |
| `payload` | 登录请求体（已加密） | `joyvM+mg...` |
| `timeout` | 请求超时 `[连接超时, 读取超时]` | `[5, 10]` |
| `token_expire_seconds` | Token 有效期（秒） | `1800` |
| `token_refresh_buffer` | 提前多少秒刷新 Token | `60` |
| `retry` | 请求重试配置（可选，缺省不重试），见下方 | `{times: 0}` |
| `auth_relogin_401` | 收到 401 时是否强制重新登录并重放一次（缺省 `true`） | `true` |
| `case_vars` | 用例变量默认值（用例里用 `${NAME}` 引用，避免把环境相关 ID 写死） | `见下方` |

**auth_relogin_401 Token 自愈（可选，缺省开启）：**

```yaml
auth_relogin_401: true    # 缺省 true；置为 false 则收到 401 直接按失败处理
```

> 场景：服务端重启 / 主动踢下线导致 Token 提前失效。开启后框架会自动重新登录并重放该请求一次
> （日志打印 `! 收到 401（Token 可能已失效），重新登录后重放一次`），避免后续用例全线 401 误报。
> 每个请求最多重登重放一次，不会死循环。

**retry 重试配置（可选）：**

```yaml
# 仅对「超时 / 连接类」异常重试；HTTP 4xx/5xx 不重试（那是断言该管的事）
retry:
  times: 2            # 重试次数（总请求数 = times + 1）；缺省 0 即不重试
  backoff: 0.5        # 基础退避秒数，第 n 次重试等待 backoff * n（线性递增）
  on: [timeout, connection]   # 参与重试的类别，可只保留其一
```

> 重试期间日志会打印 `! 请求失败(<类别>)，xx s 后进行第 n/m 次重试`，最终失败则打印 `✗ 请求失败(<类别>)`；
> 异常类别包括：超时-连接建立 / 超时-读取响应 / SSL 握手失败 / 代理错误 / 重定向次数过多 / 连接错误。

**case_vars 用例变量（可选）：**

```yaml
# 用例 YAML 的 payload / path 中直接写 ${DEPT_ID}，运行时由这里打底填充；
# 若同一用例链中通过 extract 提取了同名变量，则以提取值为准（可覆盖这里的默认值）
case_vars:
  DEPT_ID: "100"
  GROUP_ID: "9c18b99c158c4cd1b2c7e2d59b3c2598"
```

**敏感信息外置（密码不写明文）：**

YAML 中所有字符串都支持环境变量占位符，由 [core/config_loader.py](core/config_loader.py) 在加载时替换：

| 写法 | 含义 |
|---|---|
| `${CLIENT_ID}` | 取环境变量 `CLIENT_ID`；**未设置时替换为空串并告警** |
| `${CLIENT_ID:-默认值}` | 未设置时使用默认值（仅建议非敏感配置用） |

环境变量来源（优先级从高到低）：

1. **系统环境变量**（CI 中推荐：Jenkins/GitHub 直接注入，不落盘）
2. **项目根目录 `.env` 文件**（本地开发用；复制 `.env.example` 为 `.env` 填写，`.env` 已被 git 忽略，不会提交）

```bash
# .env（本地开发，勿提交）
CLIENT_ID=真实值
ENCRYPT_KEY=真实值
LOGIN_PAYLOAD=已加密登录体
```

> 优先级说明：同名变量若系统环境变量已存在，`.env` 不会覆盖；未安装 `python-dotenv` 时自动降级为只用系统环境变量。

### 2.3 新员工上手：从零到跑通接口测试（全流程）

按以下四个阶段走，每步都有明确的命令与验收标准。

#### 阶段 0：环境准备（一次性）

```bash
git clone <仓库地址> && cd apitest_0908
python -m venv .venv && .venv\Scripts\activate      # Windows 建议虚拟环境
pip install -r requirements.txt

copy .env.example .env                              # 然后编辑 .env 填入真实值：
#   CLIENT_ID / ENCRYPT_KEY / LOGIN_PAYLOAD        ← 登录鉴权三件套（找项目负责人或 F12 登录请求里拿）
```

**验收**：`.env` 已填写、`pip show pytest` 能看到版本。

#### 阶段 1：验证框架可用，跑通第一个用例（配置错误在这里暴露，不用等跑完）

```bash
pytest --collect-only                        # ① 只收集不执行：YAML 格式错、api key 未登记，当场报错
pytest -m smoke                              # ② 跑冒烟用例：验证 base_url/登录/Token/断言整条链路通
pytest test_data/asset/asset_summary.yaml    # ③ 跑仓库自带示例用例（全量断言模板，含 smoke 标记）
pytest -k "orgList"                          # ④ 等价写法：按用例名过滤跑同一条
```

**验收（= 第一个用例已跑通）**：③ 终端显示 1 passed；打开 `output/reports/report.html` 能看到用例名「orgList - 默认查询（全量断言模板）」与断言结果；日志在 `output/logs/run.log`。登录失败会大声报错，按提示查 `.env` 与 `config/dev.yaml`。

#### 阶段 2：认识用例结构（10 分钟）

看两个文件就够：
- [api/endpoints.yaml](api/endpoints.yaml)：接口注册中心，接口路径唯一事实来源（模块.接口 → method/path）
- [test_data/asset/asset_summary.yaml](test_data/asset/asset_summary.yaml)：手写用例模板（用例只写 `api` 逻辑名，不写 method/path）

详细字段与 10 种断言类型见 3.1 / 3.2。

#### 阶段 3：新增用例（三种方式，按接口量选）

| 方式 | 适用 | 命令 |
|---|---|---|
| ① 手写 YAML | 个位数接口，精细控制断言 | 直接照阶段 2 模板写（见 3.1） |
| ② 清单生成 | 十几个接口，已知道 method/path | 见下方 |
| ③ HAR 批量采集 | 几十上百个接口，懒得挨个整理 | 见下方 |

**方式 ②：清单 → 生成用例**（清单格式见 3.6 / [tools/case_list.example.txt](tools/case_list.example.txt)）

```bash
copy tools\case_list.example.txt tools\case_list.txt         # 编辑清单：
#   已登记接口写:  模块.名称 [| payload]
#   新接口写:      模块.名称 | GET/POST | /path [| payload]   ← 会自动登记进注册中心
python -m tools.gen_cases --list tools/case_list.txt --dry-run   # 先预览，确认调用/断言符合预期
python -m tools.gen_cases --list tools/case_list.txt             # 正式生成
```

**验收**：`test_data/<模块>/<模块>_gen.yaml` 生成，终端显示"结构基线 N 条 | 骨架(TODO) M 条"。

**方式 ③：F12 一次导出 → 批量生成**（接口多时用，见 3.6）

```bash
# 前置：用 Chrome/Edge 打开平台 → F12 → Network → 勾选 Preserve log → 点遍各模块页面
#       → Network 工具栏下载图标（或右键请求行 Save all as HAR with content）导出 xxx.har
python -m tools.har2list --har xxx.har                              # HAR → 清单（自动过滤/去重/命名/还原参数）
# 人工审阅 tools/case_list_from_har.txt，删掉不想测的行、补 multipart 接口 payload
python -m tools.gen_cases --list tools/case_list_from_har.txt
```

> 生成的用例是**结构层基线**（exists/field_type/schema 生效，值层断言以注释保留）；DELETE 与缺 payload 的写接口只生成骨架用例 + TODO。生成后**必须人工过一遍**：补 TODO、放开有把握的值层断言、给核心用例加 `markers: [smoke, p0]`。

#### 阶段 4：跑测试与看报告

```bash
pytest                                # 全量（默认 dev 环境）
pytest --env=staging                  # 指定环境
pytest -m smoke                       # 只跑冒烟
pytest -m "p0 or smoke"               # 优先级组合
pytest -k "用户"                       # 按名称过滤
pytest -x                             # 失败即停
```

报告：`output/reports/report.html`（自动生成，浏览器直接打开）；Allure 需 CLI：`allure serve output/reports/allure-results`；日志 `output/logs/run.log`。

**验收**：报告里能看到自己新增的用例名（`api_name`），失败项有聚合的断言信息。

#### 常见卡点速查

| 症状 | 处置 |
|---|---|
| 登录失败 / Token 相关报错 | 查 `.env` 三件套与 `config/<env>.yaml` 的 base_url（详见 5.3 Q4） |
| 收集期报 api key 不存在 | 用例里的 `api` 未在 endpoints.yaml 登记（拼错或漏登记） |
| 生成器报"未在 api/endpoints.yaml 登记" | 清单里改用格式2全格式行，脚本会自动登记 |
| 生成的骨架用例怎么补 | 在 `test_data/<模块>/<模块>_gen.yaml` 中按 TODO 注释补 payload 或断言后重跑 |

---

## 三、Web 接口自动化测试

### 3.1 用例怎么写

用例 YAML 放在 `test_data/` 下的**任意模块子目录**（如 `test_data/asset/`、`test_data/system/`），框架自动递归扫描全部 `.yaml` 文件，新增业务模块**无需改任何代码**。

每个文件可包含多条用例。**用例不写 method/path，只写接口逻辑名 `api`**——接口定义集中在注册中心 [api/endpoints.yaml](api/endpoints.yaml)：

```yaml
# test_data/asset/asset_summary.yaml

- api_name: "获取当前用户信息"     # 用例名称（报告中显示）
  api: system.get_info           # 接口逻辑名（模块.接口），定义在 api/endpoints.yaml
  payload: ''                    # 请求参数（GET=query参数，POST=body）
  assert:
    - type: status_code
      expected: 200
    - type: json_path
      path: "code"
      expected: 200
```

**接口注册中心（路径唯一事实来源）：**

所有接口在 [api/endpoints.yaml](api/endpoints.yaml) 按业务模块登记一次，后端改路径只改这一处，全部用例自动生效：

```yaml
# api/endpoints.yaml
orgList:
  get_orgList:
    method: GET
    path: /system/dept/orgList
system:
  get_info:
    method: GET
    path: /system/user/getInfo
asset:
  instock_list:
    method: GET
    path: /asset/instock/v2/list
# 可选项（放开时合并到对应模块下）：
#   headers: {X-Sign: "${SIGN_HEADER}"}  # 接口专属头，值支持环境变量插值，自动与 Token 头合并
#   body_type: data                       # 非 GET 请求体类型：json（默认）/ data（原始文本或表单）
```

> 注册中心只登记 `method/path/headers/body_type` 四项，**不提供默认请求参数**：所有请求参数都要写在用例的 `payload` 里。当前已登记的模块为 `orgList` / `asset` / `system`，可用 `pytest --collect-only` 或直接查看该文件确认最新清单。

> 接口路径中支持 `${var}` **运行时变量**（如 `path: /asset/${asset_id}`，由 extract 链路替换）；
> headers 的值支持 `${ENV_VAR}` **环境变量插值**（.env / CI 注入）。两者是正交机制，互不干扰。

**基础字段：**

| 字段 | 必填 | 说明 |
|---|---|---|
| `api_name` | ✅ | 用例名称（同时作为 pytest 用例 id），全局唯一，`depends_on` 靠它引用 |
| `api` | ✅ | 接口逻辑名（`模块.接口`），必须已在 `api/endpoints.yaml` 注册，收集期即校验，拼错立即报错 |
| `payload` | ❌ | 请求参数，支持两种格式（见下方） |
| `body_type` | ❌ | 仅非 GET 请求生效：`json`（默认）/ `data`；可在用例中覆盖注册中心的同名配置 |
| `headers` | ❌ | 用例级请求头，与注册中心的接口专属头合并（同名以用例为准） |
| `params` | ❌ | 额外 query 参数（与 body 并存，如"POST body + URL 上带 tenantId"） |
| `cookies` | ❌ | 本次请求附带的 Cookie（dict） |
| `verify` | ❌ | 是否校验 TLS 证书（`false` 用于自签名证书环境；不写则用 requests 默认） |
| `files` | ❌ | multipart 文件上传：`{"file": ["a.xlsx", "内容字节"]}`，与 `payload` 同时出现时 payload 作为表单字段 |
| `timeout` | ❌ | 用例级超时：单值秒数（如 `30`）或 `[连接, 读取]`；不写则用环境配置的 `timeout` |
| `assert` | ✅ | 断言列表，**必填**（收集期校验，见下方"断言强度"） |
| `extract` | ❌ | 变量提取，见 3.3 |
| `depends_on` | ❌ | 前置用例 id 列表（前置未通过则本用例 skip，避免级联误报），见 3.3 |
| `cleanup` | ❌ | 收尾步骤列表，用例结束后（不论成败）执行，用于回收脏数据，见 3.3 |
| `markers` | ❌ | 用例分级标记列表，可选值：`smoke`/`regression`/`p0`/`p1`，见 3.5 |

**断言强度（收集期强制）：**

用例必须写 `assert`，且至少包含一条"强断言"（`header`/`json_path`/`field_type`/`exists`/`list_len`/`contains`/`each`/`schema`/`response_time`）。
只写 `status_code` 的用例会在 `--collect-only` 阶段直接报错——因为接口返回 200 但业务码错误、字段缺失都会被放过，属于典型的"假通过"。

**用例模型与字段校验：** 上述字段由 [core/case_model.py](core/case_model.py) 的 `TestCase` dataclass 统一定义，超出上表的字段不会生效；类型写错（如 `markers: "smoke"`、`cleanup: {}`）会在收集期报错。

**用例分级示例：**

```yaml
- api_name: "获取当前用户信息"
  api: system.get_info
  markers: [smoke, p0]        # 冒烟用例 + 最高优先级，可 pytest -m smoke 只跑这一类
  assert:
    - type: status_code
      expected: 200
```

**payload 格式：**

GET 请求用 query string：
```yaml
payload: "pageNum=1&pageSize=10&deptId=${DEPT_ID}"   # 环境相关 ID 走 case_vars 变量，见 3.3
```

POST 请求用 YAML 对象（默认 `body_type: json`，自动转 JSON body）：
```yaml
payload:
  username: "admin"
  password: "123456"
```

非 JSON 提交（加密原始文本 / form 表单）时，在注册中心或用例中设 `body_type: data`：
```yaml
# 原始文本（配合自定义 Content-Type，如登录加密串 text/plain）
body_type: data
payload: "joyvM+mgTUumK23N4EH..."
headers: {Content-Type: "text/plain"}

# dict + data：requests 自动按 application/x-www-form-urlencoded 编码
body_type: data
payload:
  username: "admin"
  password: "123456"
```

> 状态码说明：框架**不拦截** 4xx/5xx 响应，错误响应会正常进入断言流程——想验证"接口返回 404"直接写 `status_code: expected 404` 即可。

### 3.2 断言（10 种类型）

`assert` 是一个列表，每条是一个断言规则。

#### ① `status_code` - HTTP 状态码断言

```yaml
- type: status_code
  expected: 200
```

#### ② `header` - 响应头断言

```yaml
- type: header
  field: "Content-Type"
  expected: "application/json"
  operator: "contains"        # 可选，默认 eq
```

#### ③ `json_path` - JSON 字段值断言

```yaml
- type: json_path
  path: "data.user.userName"
  expected: "admin"
  operator: "eq"              # 可选，默认 eq
```

| 字段 | 说明 |
|---|---|
| `path` | JSON 路径，点号分隔，支持列表索引：`data.0.name` |
| `expected` | 期望值 |
| `operator` | 比较符，见下方通用运算符表 |

#### ④ `field_type` - 字段类型断言

```yaml
- type: field_type
  path: "data.user"
  expected: "dict"            # str/int/list/dict/bool/float/null
```

#### ⑤ `exists` - 字段存在性断言

```yaml
- type: exists
  path: "data.user.userId"
  expected: true              # true=存在，false=不存在
```

#### ⑥ `list_len` - 列表长度断言

```yaml
- type: list_len
  path: "data"
  expected: 0
  operator: "gte"             # 长度 >= 0
```

#### ⑦ `contains` - 包含断言

```yaml
- type: contains
  path: "data.roles"          # 可以是字符串或列表
  expected: "superadmin"
```

#### ⑧ `schema` - JSON Schema 结构校验

```yaml
- type: schema
  schema_file: "getInfo.json"   # schemas/ 目录下的文件名
```

用 JSON Schema 一次性校验整个响应体的结构（必填字段、类型、嵌套结构），适合字段多的复杂接口。Schema 文件放在 `schemas/` 下。

> ⚠️ **防空转校验**：schema 文件必须是**合法的 JSON Schema**（含 `type` / `properties` / `required` 等关键字）。
> 若误把"响应快照"（纯数据、无关键字）当 schema 用，jsonschema 会把它当成空 schema，断言**永远通过、形同虚设**。
> 框架检测到缺少关键字时会直接判失败并给出提示，请改用 `tools/gen_cases.py` 生成的 `gen_*.json`。

#### ⑨ `each` - 数组批量断言

```yaml
- type: each
  path: "data.list"            # 列表路径
  field: "status"              # 每个元素的字段
  expected: "在用"             # 期望值
  operator: "eq"               # 可选，默认 eq
```

遍历列表中**每个元素**校验指定字段，任何一个不满足则失败。

#### ⑩ `response_time` - 响应时间（SLA）断言

```yaml
- type: response_time
  expected: 2.0               # 期望耗时上限（秒）
  operator: "lte"             # 可选，默认 lte（小于等于）
```

基于 `requests` 的 `response.elapsed` 判定单接口耗时，用于守住性能底线（如核心查询接口 P95 ≤ 2s）。`operator` 支持下方通用运算符。

**通用运算符**（支持 `operator` 的类型：`json_path`、`header`、`list_len`、`each`、`response_time`）：

| 运算符 | 含义 | 运算符 | 含义 |
|---|---|---|---|
| `eq` | 等于（默认） | `gte` | 大于等于 |
| `ne` | 不等于 | `lte` | 小于等于 |
| `gt` | 大于 | `contains` | 包含 |
| `lt` | 小于 | `in` | 属于 |

### 3.3 变量提取与传递（extract + ${var}）

跨用例传数据，例如创建接口返回的 ID 传给查询接口。

**用例 A（提取变量）：**
```yaml
- api_name: "创建资产"
  api: asset.create            # 需先在 api/endpoints.yaml 注册（method: POST）
  payload:
    name: "测试资产"
  extract:
    asset_id: "data.id"        # 把响应 data.id 提取为变量 asset_id
  assert:
    - type: status_code
      expected: 200
```

**用例 B（引用变量）：**
```yaml
- api_name: "查询资产"
  api: asset.detail            # 注册路径写 /asset/${asset_id}，运行时替换
  assert:
    - type: status_code
      expected: 200
```

**说明：**
- `extract` 是字典：key 是变量名，value 是响应中的 JSON 路径
- 后续用例的 payload、以及注册中心 endpoints.yaml 的 path 中用 `${变量名}` 引用
- 变量在整个测试运行期间共享（session 级别）
- 注意执行顺序：提取变量的用例必须在引用变量的用例**之前**
- 变量上下文初始化时先用 `config/<env>.yaml` 的 `case_vars` 打底（环境相关 ID 不写死在用例里），
  `extract` 提取的同名变量会覆盖默认值；变量名未在任何地方定义时 `${xxx}` 会**原样发出**（不会报错），
  排查此类问题时看请求日志里的 `body:` 即可发现

**用例依赖（`depends_on`）——把"隐式顺序依赖"显式化：**

复用变量（`extract` → `${var}`）本质上就是"B 依赖 A"，但顺序靠约定、失败时 B 会跟着报一堆没用的错。
用 `depends_on` 显式声明前置用例，前置未通过时本用例直接 **skip**（而不是 fail），报告里一眼能看出"是被阻塞，不是真失败"：

```yaml
- api_name: "创建资产"
  api: asset.create
  payload: { name: "测试资产" }
  extract:
    asset_id: "data.id"
  assert:
    - type: json_path
      path: "code"
      expected: 200

- api_name: "查询资产详情"
  api: asset.detail
  depends_on: ["创建资产"]        # 前置用例的 api_name（本文件内的用例 id）
  assert:
    - type: json_path
      path: "data.id"
      expected: "${asset_id}"
```

> ⚠️ `depends_on` 与 `-n` 并行**互斥**：启用 xdist 并行（`-n`）时，框架会在**收集期**扫描全部用例，只要存在带 `depends_on`/`extract` 的用例就**直接报错**（提示去掉 `-n` 串行执行，或改用 `--dist loadfile`），避免各 worker 的变量与用例状态互不相通导致"静默跑出错结果"。
> `--dist loadfile` 按文件分片（同文件同进程）被视为安全模式放行——前提是依赖关系都在同一个 YAML 文件内；完全无依赖的用例可放心用 `-n auto` 提速。
> 护栏实现为 [testcases/test_regression.py](testcases/test_regression.py) 的 `pytest_collection_modifyitems` 钩子（读取 `numprocesses` / `dist` 两个选项）；未安装 xdist 时该钩子自动失效，不影响串行运行。

**用例收尾（`cleanup`）——测试造的脏数据自动回收：**

创建类用例通常需要"跑完把数据删掉"。`cleanup` 段在该用例结束的 `finally` 中执行，**无论断言通过/失败都会跑**，
因此中途失败也不会把垃圾数据留在环境里。清理失败默认只告警、不影响用例结果（`ignore_failure: true`）：

```yaml
- api_name: "创建资产"
  api: asset.create
  payload: { name: "临时资产" }
  extract:
    asset_id: "data.id"
  assert:
    - type: json_path
      path: "code"
      expected: 200
  cleanup:
    # 二选一：引用注册中心的 api（推荐）
    - api: asset.delete
      payload: { id: "${asset_id}" }
    # 或临时写死 method + path（无需登记注册中心，支持 ${var} 替换）
    - method: POST
      path: "/asset/delete/${asset_id}"
```

| `cleanup` 字段 | 说明 |
|---|---|
| `api` | 注册中心的接口 key（与测试用例同规则，path 支持 `${var}`） |
| `method` / `path` | 不经注册中心的临时请求（与 `api` 二选一，`api` 优先） |
| `payload` / `body_type` | 请求体与发送方式（`json` 默认 / `data`） |
| `headers` | 附加请求头 |
| `ignore_failure` | 清理失败是否忽略，默认 `true` |

> 字段与校验规则由 [core/case_model.py](core/case_model.py) 定义，`cleanup` 缺少 `api` 又不写 `method`+`path`、`body_type` 非法等都会在 `--collect-only` 阶段报错。

### 3.4 运行与报告

```bash
# 默认开发环境
pytest

# 指定环境
pytest --env=dev
pytest --env=staging
pytest --env=prod

# 常用参数
pytest --collect-only              # 只收集用例（检查 YAML 配置）
pytest -k "获取用户信息"            # 按名称过滤用例
pytest -m smoke                    # 只跑冒烟用例（YAML 中 markers 含 smoke）
pytest -m "p0 or smoke"            # 标记表达式：P0 或冒烟
pytest -m "not p1"                 # 排除 P1
pytest -x                          # 失败即停
pytest -n auto --dist loadfile     # 并行（需 pytest-xdist；见下方并行限制）
pytest --reruns 1 --reruns-delay 3 # 失败自动重试 1 次（需 pytest-rerunfailures）
pytest --cov --cov-report=term-missing   # 覆盖率（需 pytest-cov，配置见 .coveragerc）
```

**纯本地单元测试（不连库、不联网，随时可跑）：**

```bash
# 框架核心 core/ 单元测试（断言引擎 / 变量替换 / 客户端脱敏与重试 / 用例模型 / 注册中心 / Schema 合法性）
pytest testcases/test_core_unit.py
```

> `test_core_unit.py` 用测试替身（Fake Session / Fake Auth）覆盖 `core/` 各模块：比较运算、10 类断言、
> `${var}` 替换、端点注册中心、日志脱敏、401 自动重登重放、`TestCase`/`CleanupStep` 收集期校验、
> 以及 `schemas/*.json` 是否为合法 JSON Schema。
> 这些是无外部依赖的纯函数测试，**全量 `pytest` 与 CI 都会一并执行**。

**静态检查（需 `ruff`，配置见 [ruff.toml](ruff.toml)）：**

```bash
python -m ruff check core api testcases conftest.py
```

> 当前只启用"真问题"规则（未定义名称 / 语法错误 / 失效的 noqa 等），避免存量风格问题淹没真实缺陷。

> 覆盖率刷新（CI 用）：`coverage report -m`（`.coveragerc` 的 `source` 为 `core`、`api`）。

> 标记在 YAML 用例中通过 `markers: [smoke, p0]` 声明，已注册标记：`smoke`（冒烟）/ `regression`（回归）/ `p0` / `p1`，注册位置为 [pytest.ini](pytest.ini)。
>
> ⚠️ **标记是"白名单"机制**：`pytest -m smoke` 只会跑到**显式声明了** `smoke` 的用例，没写 `markers` 的用例在带 `-m` 的筛选下**一条都不会执行**（且 pytest 无匹配时退出码为 5）。因此：
> - 新增/生成的用例**必须显式补 `markers`**，否则它只会在"不带 `-m` 的全量运行"里被执行；
> - `tools/gen_cases.py` 生成的用例默认不带标记，生成后请人工补 `markers`（核心链路补 `smoke`/`p0`，其余补 `p1`）。

> ⚠️ **并行限制**：`-n` 多进程下各 worker 的变量上下文（`variables`）相互隔离，且用例执行顺序无法保证。含 `extract` / `${var}` 跨用例依赖的用例**不要并行**；无依赖的查询类用例可放心用 `-n auto` 提速。

**报告：**
- HTML 报告：`output/reports/report.html`（自动生成）
- Allure 报告：`output/reports/allure-results/` 自动生成（含 `environment.properties` 环境元信息与 `categories.json` 缺陷分类），查看需 Allure CLI：
  ```bash
  allure serve output/reports/allure-results                      # 在线预览
  allure generate output/reports/allure-results -o output/reports/allure-report --clean   # 静态报告
  ```
  报告首页会显示 ENV / Python / pytest / 平台 / 构建号 / Git commit / 运行时间；失败用例自动归类为「断言失败」「接口不可达或超时」「前置用例未通过」。
- 日志：`output/logs/run.log`（按天滚动，保留 14 天；Token / 密码等敏感字段已打码）

**用例执行全流程：**

```
pytest --env=dev 启动
│
├─ ① 收集阶段：递归扫描 test_data/**/*.yaml
│              → TestCase.from_dict 校验字段/断言强度/cleanup 合法性
│              → 校验 api key 与 cleanup.api 是否在注册中心登记
│              → parametrize 生成用例（报告 ID = api_name）；xdist 下列有依赖用例直接报错
│
├─ ② Fixture 初始化（session 级，只跑一次）
│   ├─ config → 读 config/dev.yaml（.env 注入 + ${VAR} 插值）
│   ├─ auth → AuthManager.login() 登录拿 Token（失败会大声报错）
│   ├─ api_client → BaseClient(base_url, auth, relogin_on_401)
│   └─ variables → {} 空字典
│
└─ ③ 每条用例执行
    ├─ 0. 依赖检查：depends_on 前置用例未通过 → pytest.skip（不 fail）
    ├─ 1. 接口解析：api 逻辑名 → 注册中心取 method/path/专属头/body_type
    ├─ 2. 变量替换：${var} → 实际值（path 与 payload）
    ├─ 3. 发送请求（GET→params；POST 按 body_type 走 json/data；Token 自动注入，4xx/5xx 不拦截）
    │       └─ 若返回 401：重新登录并重放一次（relogin_on_401）
    ├─ 4. 变量提取：extract 规则存入 variables
    ├─ 5. 执行断言：AssertionEngine 逐条判定
    ├─ 6. 失败则 pytest.fail（聚合全部失败断言信息）
    └─ 7. finally：执行 cleanup 收尾步骤（失败仅告警，不影响用例结果）
```

### 3.5 持续集成（CI）

仓库根目录提供 [Jenkinsfile](Jenkinsfile) 声明式流水线样例，五个阶段：

| 阶段 | 动作 |
|---|---|
| 安装依赖 | `pip install -r requirements.txt` |
| 静态检查 | `python -m ruff check core api testcases conftest.py`，未定义名称/语法错误在这一步就拦下 |
| 用例收集检查 | `pytest --collect-only`，YAML 写错（字段非法、弱断言、未登记 api）在这一步就暴露 |
| 冒烟测试 | `pytest -m smoke --reruns 1 --reruns-delay 3`，每次构建执行（只跑显式带 `smoke` 标记的用例；偶发抖动用重试兜） |
| 全量回归 | `pytest --cov --cov-report=term-missing`（**不带 `-m`**），仅 main 分支，执行 `test_data/` 下全部用例并输出覆盖率 |

> ⚠️ 全量回归**不要**写成 `pytest -m regression`：生成用例默认不带标记，那样会一条都匹配不到（pytest 退出码 5）。若团队统一给所有用例补 `regression` 标记，也可以改回 `-m regression`，二者择一。
>
> 流水线节点要求：Jenkinsfile 使用 `bat`（Windows 节点）；Linux 节点把 `bat` 换成 `sh` 即可。示例只跑 smoke + 全量，如需按优先级执行，可另加 `pytest -m "p0"` / `-m "p1"` 阶段。

**凭据接入**：在 Jenkins 凭据管理中创建 Secret text（如 `apitest-client-id`），流水线将其注入为环境变量，config 中的 `${...}` 占位符自动解析，日志中 Jenkins 会自动脱敏。GitLab CI / GitHub Actions 原理相同：在平台的 Secrets/Variables 中配置同名环境变量即可。

**CI 需要注入的环境变量清单**（完整见 `.env.example`）：

| 变量 | 用途 | 何时必填 |
|---|---|---|
| `CLIENT_ID` | 登录请求头 clientid | 始终 |
| `ENCRYPT_KEY` | 登录请求头 encrypt-key | 始终 |
| `LOGIN_PAYLOAD` | 登录请求体（已加密串） | 始终 |

> 未设置的变量会被静默替换为空串（仅打 warning 日志），因此**新增环境配置时务必同步补 `.env.example`**。

### 3.6 用例自动生成（契约快照）

**解决什么问题**：新模块几十个接口挨个手写 YAML 太繁琐。[tools/gen_cases.py](tools/gen_cases.py) 提供"记录-生成"式流程：把接口清单写进 txt → 脚本登录后逐个真实调用 → 解析响应结构 → 自动生成带断言的基线用例 + JSON Schema，未登记接口自动补进注册中心。生成器负责"结构层"基线，值层断言以注释保留（人工确认后放开），与手写用例互补。

**清单格式**（每行一个接口，`#` 开头为注释，参考 [tools/case_list.example.txt](tools/case_list.example.txt)）：

```text
# 格式1（推荐）：已在 api/endpoints.yaml 登记的接口，只写 key，可选 payload（GET=query串，POST=body）
orgList.get_orgList
asset.instock_list | pageNum=1&pageSize=10

# 格式2（新接口）：key | METHOD | path [| payload]，自动登记进 api/endpoints.yaml
asset.secret_create | POST | /asset/secret/create | {"name": "测试"}
```

**命令：**

```bash
python -m tools.gen_cases --list tools/case_list.txt                 # 正式生成（默认 dev 环境）
python -m tools.gen_cases --list tools/case_list.txt --dry-run       # 只调用与预览，不写任何文件
python -m tools.gen_cases --list tools/case_list.txt --force         # 整文件覆盖重新生成（丢弃人工修改，慎用）
python -m tools.gen_cases --list tools/case_list.txt --env staging   # 指定环境
```

**产物：**

| 产物 | 位置 | 说明 |
|---|---|---|
| 基线用例 | `test_data/<模块>/<模块>_gen.yaml` | 按模块一个文件；默认跳过已存在的 api（保护人工修改），`--force` 整文件覆盖 |
| Schema | `schemas/gen_<模块_接口>.json` | 响应结构契约，观察到的 null 字段不约束类型（避免数据变化误报） |
| 注册中心 | `api/endpoints.yaml` | 格式2新接口自动追加（`.bak` 备份、文本插入保留手工注释），key 来源接口必须已登记否则报错 |

**断言分两层生成：**

- **结构层（生效）**：`status_code` / `header`(Content-Type) / `json_path`(code/msg) / `exists` / `field_type`（逐字段）/ `schema`——只跟接口结构走，不随数据变化
- **值层（注释保留）**：`list_len`、null 字段断言等数据敏感断言，以 `# - {...}` 注释写出，人工确认后去掉注释放开

**安全规则（分级调用，避免危险/异常响应固化成断言）：**

| 场景 | 策略 |
|---|---|
| GET | 真实调用，按响应生成断言 |
| POST/PUT 有 payload | 真实调用（payload 以 `{` 开头按 JSON body，否则按原始文本） |
| POST/PUT 无 payload | 只生成骨架用例 + TODO（不调用） |
| DELETE | 一律骨架用例，绝不调用（防污染业务数据） |
| 响应异常（HTTP 非 200 / 非 JSON / 业务 code≠200） | 降级骨架用例 + TODO（附响应预览），避免把错误响应结构固化成断言 |

骨架用例只带意图断言（`status_code 200` + `json_path code 200`，DELETE 无后者），补齐 TODO 后重跑或手工完善。

**批量采集：HAR 一键生成清单**（接口多时省去逐个复制，见 [tools/har2list.py](tools/har2list.py)）：

1. F12 → Network → 勾选 `Preserve log` → 把各模块页面点一遍（让接口都发出来）
2. Network 面板空白处右键 → **Save all as HAR with content** 保存
3. `python -m tools.har2list --har xxx.har`（base_url 自动读 config；输出默认 `tools/case_list_from_har.txt`）
4. 人工审阅清单（删掉不想测的行）→ 交给 `python -m tools.gen_cases --list` 生成用例

自动处理：只保留 base_url 前缀下的请求（静态资源/登录接口/其他域名过滤）、同 method+path 去重（保留参数最全一条）、模块名取 path 首段、接口名末段转 snake_case（`v2` 版本段与 `list` 等重名末段自动带前缀消歧）、GET query 与 POST JSON/表单 body 自动还原为 payload；multipart 文件上传无法离线还原，以 `# TODO` 注释行输出人工确认。

> 生成器复用 `config/<env>.yaml` 登录与 `BaseClient`，与 pytest 执行同一套 Token/请求头；生成后先 `pytest --collect-only` 校验收集无误，再 `pytest` 跑通基线。

---

## 四、框架扩展指南

### 新增断言类型

在 `core/assertions.py` 的 `AssertionEngine` 类中添加 `_assert_<type>` 方法，YAML 中 `type` 字段填对应的类型名即可，无需改其他代码。

> 若新断言属于"强断言"（能真正拦住响应变化，如值/结构类），请同时把它加入 [core/case_model.py](core/case_model.py) 的 `STRONG_ASSERT_TYPES`，否则它无法满足断言强度门禁（用例仍需另配一条强断言）。

### 新增接口（两步）

1. 在 `api/endpoints.yaml` 对应业务模块下登记接口：
   ```yaml
   asset:
     create:
       method: POST
       path: /asset/create
       # headers: {X-Sign: "${SIGN_HEADER}"}   # 可选：专属头（支持环境变量插值）
       # body_type: json                        # 可选：json（默认）/ data（原始文本或表单）
   ```
2. 用例 YAML 中直接引用 `api: asset.create`。

无需写任何 Python 代码；收集阶段会校验 key 是否存在、body_type 是否合法，拼错立即报错。

### 新增环境配置

在 `config/` 目录下新建 `xxx.yaml`，运行时 `pytest --env=xxx`。

---

## 五、附录

### 5.1 目录结构

```
apitest_0908/
├── conftest.py                  # pytest 全局 fixture 配置中心
├── pytest.ini                   # pytest 运行参数 + markers 分级注册 + testpaths
├── ruff.toml                    # 静态检查配置（仅启用"真问题"规则，避免风格噪音）
├── .coveragerc                  # 覆盖率配置（source=core,api）
├── Jenkinsfile                  # CI 流水线样例（安装→静态检查→收集检查→冒烟→回归）
├── requirements.txt             # 依赖清单（含可选增强：xdist/rerunfailures/cov/ruff）
├── .env.example                 # 密钥环境变量模板（复制为 .env 使用，.env 不入库）
│
├── config/                      # 【配置层】多环境配置（敏感项用 ${VAR} 占位）
│   ├── dev.yaml                 # 开发环境
│   ├── staging.yaml             # 测试环境
│   └── prod.yaml                # 生产环境
│
├── core/                        # 【核心层】接口测试框架基础能力
│   ├── config_loader.py         # 配置加载：.env 注入 + ${VAR} 环境变量插值
│   ├── base_client.py           # HTTP 请求封装（URL拼接/Token注入/日志，不拦截 4xx/5xx）
│   ├── auth_manager.py          # Token 管理器（自动登录+自动刷新，登录路径走 config.login_path）
│   ├── assertions.py            # 断言引擎（10 种断言类型；schema 断言含防空转校验）
│   ├── case_model.py            # 用例数据模型（TestCase/CleanupStep dataclass + 收集期字段/强度校验）
│   ├── constants.py             # 框架级常量（markers 白名单 KNOWN_MARKERS，与 pytest.ini 同步）
│   ├── utils.py                 # 通用工具（${var} 运行时变量递归替换）
│   └── logger.py                # 统一日志（配置 root logger：控制台 + 按天滚动文件）
│
├── api/                         # 【接口注册层】接口路径唯一事实来源
│   ├── endpoints.yaml           # 接口注册中心：模块.接口 → method/path/headers/body_type
│   └── registry.py              # 注册中心加载器（Endpoint dataclass + headers 插值 + 收集期校验）
│
├── schemas/                     # 【Schema层】JSON Schema 文件（schema 断言用）
│   ├── getInfo.json             # 手工维护（合法 JSON Schema 示例）
│   ├── org_list.json            # 手工维护（原为响应快照，已修正为真 JSON Schema）
│   └── gen_*.json               # gen_cases.py 生成的基线 Schema（每个生成用例一个，勿手改）
│
├── test_data/                   # 【用例层】测试用例 YAML（任意模块子目录，自动递归发现）
│   ├── asset/
│   │   ├── asset_summary.yaml   # orgList 全量断言模板（覆盖多种断言类型，手工维护）
│   │   └── asset_gen.yaml       # 资产模块自动生成基线用例（gen_cases 产物）
│   └── system/
│       └── system_gen.yaml      # 系统模块自动生成基线用例（gen_cases 产物）
│
├── testcases/                   # 【执行层】测试执行入口
│   ├── test_regression.py       # 数据驱动回归测试（markers 分级、依赖检查、cleanup、ids 显示用例名）
│   └── test_core_unit.py        # 框架核心单元测试（断言/变量/客户端脱敏与重登/用例模型，纯本地）
│
├── tools/                       # 【工具层】用例生成工具链（python -m 运行，复用 core/api）
│   ├── gen_cases.py             # 用例自动生成器（契约快照）：清单→调用→解析→生成 YAML，用法见 3.6
│   ├── har2list.py              # HAR → 接口清单转换器（F12 一次导出批量采集，见 3.6）
│   ├── case_list.example.txt    # gen_cases 接口清单格式示例（复制后按需修改）
│   ├── case_list.txt            # 实际使用的清单（团队共享）
│   └── case_list_from_har.txt   # har2list 输出的清单（审阅后作为 gen_cases 输入）
│
└── output/                      # 【产物层】全部运行产物（git 已忽略，自动创建）
    ├── logs/run.log             # 按天滚动日志（保留 14 天）
    ├── reports/                 # pytest-html 与 Allure 报告
```

### 5.2 模块速览

| 模块 | 作用 |
|---|---|
| `config/*.yaml` | 环境配置：接口地址、登录凭据、超时、Token 刷新；密码等敏感项写 `${VAR}` 占位 |
| `core/config_loader.py` | 配置加载器：读 YAML 前注入 `.env`，递归替换 `${VAR}` / `${VAR:-默认值}`；系统环境变量优先 |
| `core/base_client.py` | `requests.Session` 复用 + URL 拼接 + Token 注入 + 统一日志；**日志脱敏**（`Authorization`/`token`/`password` 等字段只留首尾各 4 位，响应体正则打码，避免 JWT 明文落盘）；**不做 raise_for_status**，状态码交给断言；收到 401 时自动重新登录并重放一次（`relogin_on_401`，每个请求最多一次，不会死循环）；异常按类别细分（超时-连接建立/超时-读取/SSL/代理/重定向/连接错误），可按 `config.retry` 对超时与连接类异常退避重试（缺省不重试） |
| `core/case_model.py` | 用例数据模型：`TestCase` / `CleanupStep` dataclass，YAML 用例的唯一解析入口；收集期集中校验（字段类型、`body_type` 合法、**至少一条强断言**、`depends_on` 合法、`cleanup` 必须给 `api` 或 `method`+`path`） |
| `core/constants.py` | 框架级常量：`KNOWN_MARKERS`（markers 白名单单一事实来源，与 [pytest.ini](pytest.ini) 同步，`TestMarkers` 单元测试会校验两者一致） |
| `core/auth_manager.py` | 启动登录一次缓存 Token，快过期自动重登；登录路径取 `config.login_path`（默认 /auth/login）；登录失败（非 JSON / 无 access_token）抛 RuntimeError |
| `core/utils.py` | 通用工具：`replace_vars` 递归替换 str/dict/list 中的 `${var}` 运行时变量 |
| `core/assertions.py` | 断言引擎：10 种断言类型（含 `response_time` SLA 断言），逐条分发 `_assert_<type>`，聚合失败信息；`schema` 断言会先校验 Schema 关键字，防止"响应快照"当 Schema 用导致断言永远通过 |
| `core/logger.py` | 配置 **root logger**：控制台 + `output/logs/run.log`（按天滚动保留 14 天），core.* 日志统一收口 |
| `api/endpoints.yaml` | **接口注册中心**：按模块登记每个接口的 method/path/可选 headers/body_type，路径唯一事实来源 |
| `api/registry.py` | 注册中心加载器：`Endpoint` dataclass + `EndpointRegistry`；headers 做环境变量插值（path 保留运行时变量）；收集期校验 api key 与 body_type |
| `tools/gen_cases.py` | 用例自动生成器（契约快照式，见 3.6）：清单 txt → 登录后逐个调用（DELETE/缺 payload 的写接口不调，异常响应降级骨架）→ 解析响应 → 生成 `<module>_gen.yaml` 基线用例（结构层断言生效、值层注释保留）+ `schemas/gen_*.json`；未登记接口自动追加注册中心（.bak 备份、保留手工注释）；`--dry-run` 全程不落盘，`--force` 整文件覆盖 |
| `testcases/test_regression.py` | 数据驱动主入口：加载用例 → 注册中心解析接口 → 变量替换 → 请求 → 提取 → 断言 → 报告 |

### 5.3 常见问题

**Q1: 接口返回结构很复杂，逐个写断言太麻烦？**

用 `schema` 断言，写一个 JSON Schema 文件一次性校验整个结构。参考 `schemas/getInfo.json`。

**Q2: 列表接口想验证所有元素的某个字段都符合条件？**

用 `each` 断言：
```yaml
- type: each
  path: "data.list"
  field: "status"
  expected: "在用"
```

**Q3: 创建接口返回的 ID 如何传给查询接口？**

用 `extract` 提取 + `${var}` 引用。参考 3.3。

**Q4: Token 过期了怎么办？**

两层保障：
1. **提前刷新**——在 config 中配置 `token_expire_seconds`（Token 有效期）和 `token_refresh_buffer`（提前刷新秒数）；
2. **过期自愈**——若某请求仍收到 401，客户端会自动重新登录并重放该请求一次（`auth_relogin_401`，默认开启；每个请求最多重登一次，不会死循环）。

**Q5: 日志里 Token 为什么是 `abcd***wxyz` 这样？**

框架对敏感字段做了脱敏（只留首尾各 4 位），避免 `.env` 注入的真实 Token/密码明文落到 `output/logs/run.log`。若需临时排查，可看响应体前缀而非完整串。

**Q6: 如何只运行某一个用例？**

```bash
pytest -k "用例名称关键字"
```

**Q7: 密钥（登录凭据等）怎么管理，会不会提交到 Git？**

三步：① YAML 中写 `${CLIENT_ID}` 占位符，不写明文；② 复制 `.env.example` 为 `.env` 填入真实值（`.env` 已被 `.gitignore` 忽略）；③ CI 中不使用 `.env`，直接在平台上配置同名环境变量（优先级更高，日志会被 Jenkins 等平台自动脱敏）。

**Q8: 只想跑核心链路的少量用例？**

给用例加 `markers: [smoke]`，然后 `pytest -m smoke`。分级体系：`smoke` 冒烟、`regression` 全量回归、`p0`/`p1` 优先级，可组合表达式如 `pytest -m "p0 or smoke"`。

**Q9: POST 接口不是 JSON 提交（加密文本 / form 表单）怎么办？**

在接口注册中心或用例中加 `body_type: data`：payload 为字符串时按原始文本发送（配合自定义 `Content-Type`），为字典时按 `application/x-www-form-urlencoded` 编码。默认 `json`，普通 JSON 接口无需配置。

**Q10: 为什么 `pytest -m smoke` / `pytest -m regression` 一条用例都没跑（退出码 5）？**

`-m` 是**白名单**筛选：只运行带对应标记的用例，未打标记的用例一律不执行。`tools/gen_cases.py` 生成的基线用例默认**不带 markers**，因此：
1. 跑冒烟用 `pytest -m smoke`（仅 `asset_summary.yaml` 与 `*_gen.yaml` 中手工补过 `smoke` 的用例会执行）；
2. 想跑**全部**用例（含所有生成用例）**不要**加 `-m`，直接 `pytest --env=dev`；
3. 需要按优先级筛选时，先给用例补 `markers: [p0]` / `[p1]` 再筛；查看当前标记分布：

```bash
pytest --collect-only -q -m smoke      # 列出会被冒烟选中的用例
pytest --collect-only -q               # 列出全部用例（含未打标记的）
```

**Q11: `--collect-only` 阶段报「弱断言」/「缺少 assert」是什么问题？**

框架强制每条用例至少含一条"强断言"（`header`/`json_path`/`field_type`/`exists`/`list_len`/`contains`/`each`/`schema`/`response_time`）。
只写 `status_code: 200` 属于"假通过"（接口返回 200 但业务码错误、字段缺失都会被放过），因此收集期直接拦截。补一条值/结构断言即可。生成器产出的骨架用例若报此错，说明 TODO 还没补完。

**Q12: 造的测试数据怎么自动删掉？**

在用例里加 `cleanup` 段，用例结束（含失败）时会在 `finally` 中执行清理请求，详见 [3.3 用例收尾](#33-变量提取与传递extract--var)。

**Q13: 用例之间怎么声明"必须先跑 A 才能跑 B"？**

用 `depends_on: ["A 的 api_name"]`；A 未通过时 B 会 skip（而非 fail）。注意它与并行执行互斥，用法见 3.3。

---

## 六、已知问题与后续优化路线

> 整合自 2026-09 的架构复盘与代码审查。**接口测试框架**侧的 P0/P1/P2 均已落地（见 6.1）。

### 6.1 已完成

**P0 · 文档与 CI 一致性修复（2026-09-30）**

- ✅ CI 全量回归失效修复：`Jenkinsfile` 中「全量回归」原为 `pytest -m regression`，但生成用例默认不带 markers，导致 main 分支实际跑 0 条用例（pytest 退出码 5）；已改为**不带 `-m`** 的 `pytest --env=...`，真正执行全部用例
- ✅ 核心用例补标记：为 `asset_summary.yaml`（orgList）、`asset_gen.yaml`、`system_gen.yaml` 中的核心链路补 `markers: [smoke, p0]` / `[p1]`，使 `-m smoke` 能选中关键用例
- ✅ 清理死字段：删除 `api/endpoints.yaml` 中未被 `Endpoint` dataclass 解析的 `default_payload` 字段（注册中心只登记 method/path/headers/body_type，不提供默认请求参数），并修正 `test_data/asset/asset_summary.yaml` 中与之矛盾的注释
- ✅ README 纠错与补齐：修正失效的 `api` 示例（`user.get_info` → 实际已登记的 `system.get_info`）；新增 markers 白名单警告（3.4）、CI 环境变量注入清单（3.5）、`-m` 筛不到用例的排错（Q10）；修正目录结构（移除已删除的 `zaiku_asset.yaml`，补 `schemas/gen_*.json` 与 `test_data/system/`）

**P1 · 性能与一致性优化（2026-09-30）**

- ✅ 异常分类与重试：[core/base_client.py](core/base_client.py) 把 `RequestException` 细分为超时-连接建立/超时-读取/SSL/代理/重定向过多/连接错误，并支持 `config.retry` 对超时与连接类异常退避重试（缺省 `times: 0` 不重试，行为向后兼容）
- ✅ 用例硬编码 ID 外置：`deptId=100` / `groupId=9c18...` 改为 `${DEPT_ID}` / `${GROUP_ID}`，由 `config/<env>.yaml` 的 `case_vars` 提供默认值（运行时 `extract` 可覆盖），切环境不再改用例

**P0/P1/P2 · 接口框架企业级加固（2026-09-30）**

> 本轮只动接口测试框架（`core/` `api/` `testcases/` `conftest.py` `tools/`）。

**P0 安全与稳定性**

- ✅ 日志脱敏（P0-1）：[core/base_client.py](core/base_client.py) 对请求头（`Authorization` / `clientid` / `encrypt-key` / `Cookie`）与响应体中的 `access_token` / `password` / `secret` 等字段做打码，只保留首尾各 4 位；修复了 JWT 明文写入 `output/logs/run.log` 的真实缺陷
- ✅ 401 自愈（P0-2）：客户端收到 401 时自动 `auth.login()` 并重放该请求一次（可关，每个请求最多一次，无死循环）；Token 在长时回归中途失效不再导致大批用例连锁失败
- ✅ 用例收尾（P0-3）：新增 `cleanup` 段，在用例 `finally` 中执行，**断言失败也会跑**，失败仅告警（`ignore_failure`），避免用例产生的脏数据残留在被测环境

**P1 工程化与可靠性**

- ✅ 用例模型（P2-9）：新建 [core/case_model.py](core/case_model.py)（`TestCase` / `CleanupStep` dataclass），YAML 用例唯一解析入口，字段类型、`body_type`、`depends_on`、`cleanup` 全部在收集期校验；`testcases/test_regression.py` 相应瘦身
- ✅ 显式依赖（P1-4）：用例可用 `depends_on: [前置用例名]` 声明依赖，前置未通过时本用例 skip 而非 fail，报告不再被"连锁报错"污染
- ✅ 断言强度门禁（P1-6）：用例必须至少含一条"强断言"，只有 `status_code` 的用例在 `--collect-only` 阶段直接报错，杜绝"200 即通过"的假通过
- ✅ markers 单一来源（P1-8）：新建 [core/constants.py](core/constants.py) 的 `KNOWN_MARKERS`，与 [pytest.ini](pytest.ini) 由单元测试校验同步
- ✅ 框架单元测试（P1-7）：新建 [testcases/test_core_unit.py](testcases/test_core_unit.py)（80 条，纯本地测试替身），覆盖断言引擎 10 类、变量替换、注册中心、客户端脱敏与重登重放、用例模型校验、Schema 合法性；共 **80 passed**
- ✅ 并行护栏 + 重试接入（P1-5）：xdist 下若存在 `extract`/`depends_on` 依赖用例，收集期直接报错提示改串行；`Jenkinsfile` 冒烟阶段加 `--reruns 1 --reruns-delay 3` 兜偶发抖动，新增 `ruff` 静态检查阶段，全量回归加 `--cov`

**P2 能力增强**

- ✅ case 级请求参数（P2-10/11）：用例支持 `params` / `files` / `cookies` / `verify` / `timeout` 覆盖，新增 `response_time` SLA 断言（第 10 种）
- ✅ Schema 防空转：`_assert_schema` 先校验 Schema 是否含关键字；发现并修正 `schemas/org_list.json` 原为"响应快照"（断言永远通过）的缺陷，重写为真 JSON Schema
- ✅ Allure 元信息（P2-12）：[conftest.py](conftest.py) 的 `pytest_sessionfinish` 自动写 `environment.properties`（ENV/PYTHON/PYTEST/PLATFORM/BUILD/GIT/时间）与 `categories.json`（断言失败 / 接口不可达 / 前置未通过）
- ✅ 工程配套：新增 [ruff.toml](ruff.toml)（只启用"真问题"规则）、[.coveragerc](.coveragerc)（`source=core,api`）

**P0 · 工程卫生（2026-09-18）**

- ✅ 三份文档（README / DATA_MANUAL / CODE_REVIEW）整合为 README 单一入口
- ✅ 根目录清理（2026-09-28）：删除旧草稿 `scripts/gen_cases/gen.py` 与空目录 `api_txt/`；71MB HAR 导出文件移入 `output/`；`.trae/` `.vscode/` `.workbuddy/` `AIAIAI/` 退出 git 跟踪（本地保留）；`.gitignore` 补 `*.har` / `*.bak` 与工具目录

---

## 七、框架详解与新人导读

> 本章是「代码视角」的导读：先把整套机制串起来（避免新人按目录乱翻），再分模块讲解关键函数/分支的执行过程。
> 已经能跑通用例的人，先看 7.1 / 7.2 即可上手；想贡献框架代码或排查疑难杂症时，再回头看 7.3 的执行细节。
> 本章只**梳理当前实现**，不涉及任何优化建议（优化话题另起一轮单聊）。

### 7.1 一句话总览与角色分工

**一句话**：本仓库是一套 YAML 数据驱动的 **Web 接口自动化测试**框架，复用一套 `config/*.yaml` 环境配置——

| 入口 | 角色 |
|---|---|
| `pytest` | YAML 用例 → 注册中心查 method/path → `BaseClient` 发请求 → `AssertionEngine` 校验 → Allure / pytest-html 报告 |

### 7.2 一条接口用例的端到端执行路径

以「跑通 `pytest -m smoke`」为例，从命令落到最后一帧日志的全链路：

```
pytest -m smoke --env=dev
   │
   ├──① pytest 加载 conftest.py
   │     │
   │     ├── pytest_addoption           注册 --env=dev / --env=staging / --env=prod
   │     │
   │     ├── session-fixture config     调用 load_config("dev")：
   │     │     ├─ load_dotenv()         注入 .env（系统环境变量优先）
   │     │     ├─ 读 config/dev.yaml   → 展开 ${CLIENT_ID}/${ENCRYPT_KEY:-…}
   │     │     └─ 返回 dict（含 base_url / token_expire_seconds / case_vars）
   │     │
   │     ├── session-fixture auth       AuthManager(config).login()
   │     │     └─ POST {login_path}     → 读 access_token 缓存 → 记 _login_time
   │     │
   │     └── session-fixture api_client BaseClient(base_url, auth, retry, relogin_on_401)
   │
   ├──② pytest 收集 testcases/
   │     │
   │     ├── import test_regression.py  顶层执行：
   │     │     ├─ REGISTRY = EndpointRegistry.load()                            ← api/endpoints.yaml
   │     │     ├─ API_TEST_CASES = _load_cases() 递归扫 test_data/**/*.yaml
   │     │     │     ├─ TestCase.from_dict(raw)  ← 收集期字段/强度校验就在这里
   │     │     │     └─ _validate_registry_refs   ← 用例引用的 api 必须在注册中心
   │     │     └─ pytest_collection_modifyitems  ← xdist 并行护栏（extract/depends_on → 报错）
   │     │
   │     └── _build_params() 把每个 TestCase 套成 pytest.param(id=api_name, marks=[…])
   │
   ├──③ 对每条用例执行 test_api_regression(api_client, case, config, variables, extra)
   │     │
   │     ├── 前置依赖检查：case.depends_on 任一未 passed → 标记 skipped，跳过
   │     │
   │     ├── _run_case(...)
   │     │     ├─ REGISTRY.get(case.api)         拿到 Endpoint(method/path/headers/body_type)
   │     │     ├─ replace_vars(path, variables)  运行时 ${var} 替换
   │     │     ├─ _build_request_kwargs(...)     payload/headers/cookies 全部 replace_vars
   │     │     │                                  GET → params；JSON → json；data → data；multipart → files
   │     │     ├─ api_client.request(method, path, **kwargs)
   │     │     │     ├─ 注 Bearer Token + clientid（仅当配了）
   │     │     │     ├─ _send：按 retry 退避重试（仅 timeout/connection）
   │     │     │     ├─ 收到 401 → auth.login() → 再 _send 一次（relogin_on_401，开关可关）
   │     │     │     └─ 统一日志（请求/响应脱敏，只留首尾 4 位）
   │     │     ├─ extract: 从响应 JSON 把字段塞进 variables（供后续用例 ${var}）
   │     │     ├─ AssertionEngine().run(resp, assert_list)
   │     │     │     └─ 每条规则 → _assert_<type> → AssertionResult（passed/message/actual/expected）
   │     │     └─ 失败 → pytest.fail(汇总 N/M 条失败的 message)
   │     │
   │     ├── finally:
   │     │     └─ _run_cleanup(...)  遍历 case.cleanup，挨个调收尾接口；失败仅告警
   │     │
   │     └── 记录 _CASE_STATUS[case.id] = "passed" / "failed" / "skipped" → 供 depends_on 复用
   │
   └──④ pytest_sessionfinish
         ├─ 写 output/reports/allure-results/environment.properties（ENV/PYTHON/构建号/时间）
         └─ 写 categories.json（断言失败 / 接口不可达 / 前置未通过 三类缺陷分类）
```

> **关键不变量**：
> - 接口 method/path **唯一定义在** `api/endpoints.yaml`；用例只引用 `模块.名称`；
> - YAML 用例在 `pytest --collect-only` 阶段就被 `TestCase.from_dict` + `_validate_registry_refs` 校验，拼写错误不会留到执行期。

### 7.3 主要模块关键函数 / 分支执行过程

> 格式：**文件 → 函数 / 分支 → 干什么 / 关键决策**。

#### 7.3.1 `core/config_loader.py`（配置加载）

- `load_dotenv(dotenv_path=None)`：可选依赖 `python-dotenv`，未安装静默跳过；存在 `.env` 时 `override=False` ——**系统环境变量始终优先**。
- `expand_env_vars(obj)`：递归替换 str / dict / list 中的 `${VAR}` 与 `${VAR:-默认值}`；未设置且无默认值 → 空串 + `logger.warning`（不抛错，避免配置缺失时直接中断启动）。
- `load_config(env)`：注入 `.env` → 读 `config/<env>.yaml` → 调 `expand_env_vars` → 返回 dict。

#### 7.3.2 `core/auth_manager.py`（Token 管理）

- 构造：`BaseClient(base_url)` 裸 client 用于登录请求本身（登录接口免 Token）。
- `login()`：POST `config.login_path`（默认 `/auth/login`），header 用 `clientid` / `encrypt-key` / `Content-Type`；响应里兼容 `{access_token}` 与 `{data:{access_token}}` 两种结构；登录失败（非 JSON / 缺 token）抛 `RuntimeError`，冒烟阶段会大声失败，不会静默续跑。
- `get_token()`：每请求调用 → `_token` 空就登录；剩余有效期 < `token_refresh_buffer`（默认 60s）就重登。**两层防护**：客户端还有 401 自愈。
- `_login_client`：登录裸 client，挂在 `AuthManager` 内部，不复用 `api_client.session`（隔离登录与业务请求的会话）。

#### 7.3.3 `core/base_client.py`（HTTP 客户端）

- 异常分类：`_classify(exc)` 把 `requests.RequestException` 子类按固定顺序（**子类必须排在父类前**）映射成 `超时-连接建立 / 超时-读取响应 / SSL 握手失败 / 代理错误 / 重定向次数过多 / 连接错误 / 请求异常 / 未知异常`。
- `_is_retryable(exc, retry_on)`：仅对 `Timeout` / `ConnectionError` 判定（其它一律不重试 —— **HTTP 4xx/5xx 留给断言去管**）。
- 脱敏：
  - 请求头：`authorization / clientid / encrypt-key / cookie` 走 `_mask`（>8 字符留首尾 4 位 + `***`，否则整体 `***`）。
  - 响应体：`_mask_body` 用正则匹配 `access_token / password / secret / ...` 字段值后再走 `_mask`。
- `_short(obj, limit=800)`：JSON / 字符串统一截断并标注 `共N字符，已截断`，避免日志刷屏。
- `request(method, path, **kwargs)`：
  1. `_send()`：合并 `session.headers` + 本次 `headers`（本次优先）→ 注入 `Authorization`/`clientid` → 写请求日志（已脱敏）→ 按 `retry` 退避重试 → 写响应日志（已脱敏）→ 返回 `requests.Response`。
  2. **401 自愈分支**：响应 401 且 `auth` 存在且 `relogin_on_401=True` → `auth.login()` → 再 `_send` 一次；每个请求最多重登一次，不会死循环。
- `requests.Session` 是**复用连接**的（Keep-Alive），跨用例复用同一会话。

#### 7.3.4 `core/case_model.py`（用例数据模型与收集期校验）

- `STRONG_ASSERT_TYPES`：`frozenset({header, json_path, field_type, exists, list_len, contains, each, schema, response_time})`。
- `_validate_assertions(...)`：
  1. 断言块非空；
  2. 每条必须有 `type` 字段；
  3. **至少一条强断言**——否则抛 `弱断言` 错，杜绝「200 即通过」。
- `TestCase.from_dict(raw, source)`：派生 dataclass 同时完成结构性校验：`api` 必填、`markers` 须全部在 `KNOWN_MARKERS` 内、`body_type ∈ {json, data}`、`depends_on` 是字符串列表、`cleanup` 每项必须有 `api` 或 `method`+`path`。
- `CleanupStep.ignore_failure` 默认 `True`：清理失败只告警，不影响用例结论。

#### 7.3.5 `core/assertions.py`（断言引擎）

- `AssertionEngine.run(resp, rules)`：遍历 rule，按 `type` 分发到 `_assert_<type>`；每条产 `AssertionResult(type/passed/message/actual/expected)`；结果**全部记日志**，失败时聚合 message 拼成 `pytest.fail(...)` 提示。
- `get_by_path(data, path)`：点路径取值，支持 `data.0.name` 与 `data[0].name` 两种写法（内部正则归一化）；中途为标量返回 `None`。
- 10 类断言要点：
  - `status_code` — 直比 HTTP 状态码（**单独存在算弱断言**）。
  - `header` — 响应头取值，比对走通用 `compare()`（`eq/ne/gt/lt/gte/lte/contains/in`）。
  - `json_path` — `get_by_path` 取值后比对；响应非 JSON 直接判失败并写明原因。
  - `field_type` — `bool` 是 `int` 子类 → 期望 `int` 时**显式排除 bool**（单测有专门用例）。
  - `exists` — 路径**每一层**都得能取到，**最后一层允许是 None**。
  - `list_len` — 拿不到 list 直接判失败，避免 `len()` 抛错中断整条用例。
  - `contains` — 字符串包含，与 `eq/contains` 通用比较器解耦（语义为「子串」）。
  - `schema` — `jsonschema.validate`；**先校验 Schema 是否含 JSON Schema 关键字**（`_SCHEMA_KEYWORDS` 集合），防止「响应快照被当 Schema」导致断言永远通过（曾真实踩坑，已写 `test_schema_snapshot_file_is_rejected` 防回归）。
  - `each` — 列表批量断言，遍历每个元素做字段比对；空列表直接通过；最多报告前 5 个失败项。
  - `response_time` — 用 `resp.elapsed.total_seconds()`；缺 `elapsed` 属性判失败而不是抛错。

#### 7.3.6 `core/utils.py`（运行时变量替换）

- `replace_vars(obj, variables)`：递归替换 str/dict/list；**未定义变量原样保留**（例如 `id=${UNKNOWN}` → `id=${UNKNOWN}`），便于从日志看出漏了哪个变量。**与 config 插值正交**——前者是配置加载期的环境变量，后者是测试期的业务变量。

#### 7.3.7 `core/logger.py`（统一日志）

- `_setup_logger()`：挂在 **root logger**：控制台 + `output/logs/run.log`（`TimedRotatingFileHandler`，`midnight` 滚动，保留 14 天）+ 压低 `urllib3` / `faker` 噪音。模块用 `logging.getLogger(__name__)` 自动向上传播。

#### 7.3.8 `api/registry.py`（接口注册中心）

- `Endpoint` 是 `frozen=True` 的 dataclass，保证注册项不可变。
- **环境变量插值的边界**：只对 `headers` 走 `expand_env_vars`；`path` 中的 `${var}` 是**运行时业务变量**（如 `/asset/${asset_id}`）——由 `replace_vars` 处理，**不能在加载时插值**，否则会在配置阶段被空串污染。
- `EndpointRegistry.load()` / `.get(key)` / `.keys`：加载期失败 / 取错 key 时把可用列表贴进报错信息。
- `_SUPPORTED_BODY_TYPES = ("json", "data")`：与 `core/case_model._BODY_TYPES` 保持一致；改一处记得同步。

#### 7.3.9 `testcases/test_regression.py`（数据驱动主入口）

- 顶层执行（模块导入即运行）：
  1. `REGISTRY = EndpointRegistry.load()`
  2. `API_TEST_CASES = _load_cases()`：递归扫 `test_data/**/*.yaml`，逐条 `TestCase.from_dict` + `_validate_registry_refs`；**用例 id 必须全局唯一**（`api_name` 是 pytest id 与 `depends_on` 引用键）；重复会抛错。
- `pytest_collection_modifyitems` —— **并行护栏**：若开了 `-n` 且用例带 `extract`/`depends_on`，直接抛错提示改串行或 `--dist loadfile`。
- `parse_payload(payload, body_type)`：
  - `body_type == "data"` 且 payload 是字符串 → **原样透传**（加密串、text/plain 等不能走 `parse_qs`）。
  - 否则 `parse_qs(..., keep_blank_values=True)`，空值参数保留。
- `_build_request_kwargs(...)`：GET → `params`；JSON → `json`；data → `data`；multipart → `files` 同时 `data` 放附带表单；`params` 与 `body` 可并存。
- `_run_case(...)`：主体链路（详见 7.2）。
- `_run_cleanup(...)`：失败仅 `logger.warning`，**不**影响用例结论；每个清理动作 append 一条 Allure 附件便于回溯。

#### 7.3.10 `testcases/test_core_unit.py`（框架核心单元测试）

- `FakeResponse` / `FakeSession` / `FakeAuth`：纯本地替身，**绝不联网、绝不连库**，可在任何环境跑通。
- 11 个 TestClass 覆盖：路径取值、通用比较器、断言引擎 10 类、`replace_vars`、`expand_env_vars`、注册中心、客户端脱敏、重试 / 401 重登、用例模型校验、`schemas/` 文件必须是真 Schema、markers 与 `pytest.ini` 同步。

### 7.4 测试团队新人上手完整指引

> 五阶段路线：环境 → 跑通 → 读懂 → 新增 → 上 CI。**先跑通再读代码**；前两步不通过就跳读会越读越乱。

#### 阶段 0 · 环境准备（一次性）

```bash
git clone <仓库地址> && cd apitest_0908
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                               # 然后编辑 .env：
  # CLIENT_ID / ENCRYPT_KEY / LOGIN_PAYLOAD         ← 登录鉴权三件套，找接口对接人或抓包得
```

- 不装 `python-dotenv` 也能跑（自动降级到只用系统环境变量）。
- Allure 命令行需单独装；只看 `report.html` 不必。

#### 阶段 1 · 跑通第一个用例，验证整条链路

```bash
pytest --collect-only                               # 只收集不执行；YAML / api key / 断言强度立即报错
pytest -m smoke                                     # 跑通核心链路：验证 base_url / 登录 / Token / 用例编排
pytest test_data/asset/asset_summary.yaml           # 仓库自带的「全量断言模板」：覆盖 10 类断言
pytest -k "orgList"                                 # 等价写法：按用例名筛选
```

成功标志：
- 终端 `1 passed`，`output/reports/report.html` 能看到用例名；
- `output/logs/run.log` 有请求 / 响应日志，且 `Authorization` 已被脱敏为 `****`;
- 登录失败会大声报错（明显栈 + 提示），按提示查 `.env` 与 `config/dev.yaml`。

#### 阶段 2 · 理解用例与断言（10 分钟）

打开两个文件就够：

1. `test_data/asset/asset_summary.yaml` —— **断言模板**：10 类断言基本都用上了，结构与 `api_name` 命名风格可作为复用蓝本。
2. `api/endpoints.yaml` —— 接口注册中心，所有接口的 method/path 在这里。

**关键约定**：
- 用例 `api:` 写的是逻辑名（如 `orgList.get_orgList`、`system.org_list`），**不写 method/path**；
- 后端改了路径，**只改 `api/endpoints.yaml`**，所有用例自动生效；
- 断言块必须含至少一条「强断言」——只有 `status_code` 会在收集期报错；
- payload / path 中的 `${DEPT_ID}` 等运行时变量在 `config/<env>.yaml` 的 `case_vars` 定义默认值；用例 `extract` 提取的同名变量会覆盖。

#### 阶段 3 · 新增用例（三选一）

- **手工写**（推荐给核心链路 / 关键值校验）：照 `asset_summary.yaml` 模板新写一条 YAML 用例，存到对应模块目录（如 `test_data/asset/my_case.yaml`）。
- **从 HAR 批量生成**（推荐新模块接入）：
  1. Chrome/Edge 打开平台 → F12 → Network → 勾选 `Preserve log` → 点遍各模块页面；
  2. Network 面板空白处右键 → `Save all as HAR with content` 保存 `xxx.har`；
  3. `python -m tools.har2list --har xxx.har` 生成清单 `tools/case_list_from_har.txt`；
  4. 人工审阅清单（删掉危险写接口 / 文件上传接口 / 第三方域）；
  5. `python -m tools.gen_cases --list <清单> --dry-run` 看效果；`--dry-run` 不落盘，去掉 `--dry-run` 真正写文件；
  6. 新接口自动登记 `api/endpoints.yaml`，并备份 `endpoints.yaml.bak`。
- **从已有接口列表生成**（已有文本清单场景）：直接 `python -m tools.gen_cases --list tools/case_list.txt`，清单格式：
  ```text
  # 已登记的接口：模块.名称 [| payload]
  orgList.get_orgList
  # 新接口：模块.名称 | METHOD | /path [| payload]
  secret.create | POST | /asset/secret/create
  ```

#### 阶段 4 · 跑测试与看报告

```bash
pytest -m smoke                                          # 冒烟（核心链路，每次构建都跑）
pytest -m "p0 or smoke"                                  # 组合表达式
pytest -k "orgList"                                      # 按用例名筛
pytest --env=staging                                    # 切环境（config/staging.yaml）
pytest --cov --cov-report=term-missing                   # 看 core/api 覆盖率
# 报告：output/reports/report.html（pytest-html，免安装）
#       output/reports/allure-results/（原始数据，allure serve 查看可视化）
```

#### 常见新人卡点速查

| 卡点 | 80% 的原因 | 一句话解决 |
|---|---|---|
| 收集期 `未在注册中心登记` | `api: foo.bar` 拼写错 / 漏登记 | 直接去 `api/endpoints.yaml` 加 `模块.接口` |
| 收集期 `弱断言` 错 | 用例只有 `status_code` | 加一条 `json_path` / `schema` / `exists` 等强断言 |
| 登录失败 `响应不是 JSON` | `.env` 里 `LOGIN_PAYLOAD` 是未加密明文 / 已过期 | 重新加密登录或抓包取最新 payload |
| 401 一片红 | Token 被踢 / 过期 | `config/dev.yaml` 的 `token_refresh_buffer` 调小；或确认 `auth_relogin_401: true`（默认） |
| `pipeline` 跑出来全是脏数据 | 没写 `cleanup` 段 | 用例里加 `cleanup:` 收尾接口，依赖关系用 `depends_on` |
| `pytest -m regression` 一条都不匹配 | 生成用例默认无 markers | 看 6.1：用 `-m smoke` / `--cov` 直跑；或为用例显式补 `markers` |

### 7.5 一句话拿走的"代码导读地图"

- 想看**用例怎么进的系统**：`testcases/test_regression.py` → `_load_cases` → `TestCase.from_dict` → `_validate_registry_refs`。
- 想看**请求怎么发出去的**：`core/base_client.py:_send` → `requests.Session.request` → 重试 / 401 自愈分支。
- 想看**断言怎么判定的**：`core/assertions.py:AssertionEngine.run` → `_assert_<type>`。
- 想看**登录与 Token**：`core/auth_manager.py`。
- 想看**接口路由**：所有 method/path 在 `api/endpoints.yaml`，加载入口 `api/registry.py:EndpointRegistry.load`。
- 想看**配置怎么读**：`core/config_loader.py:load_config`（`.env` → YAML → `${VAR}` 展开）。
- 想看**报告元数据**：`conftest.py:pytest_sessionfinish` 写 Allure 的 `environment.properties` / `categories.json`。

---

## 八、问题修复记录（2026-10）

> 本章按上一轮盘点结论，**逐条**说明本批次修过的内容、修复策略、对现有功能的影响。
> 修复的原则都是「**兼容优先**」：默认值与旧行为完全一致；只有显式打开某个开关（如 `--strict-schema`、`--cases-isolated`、`--fail-on-auth-expired`）才会启用新行为。
> 已用 `pytest --collect-only` 与 `pytest testcases/test_core_unit.py`（80 条）全绿验证。

### 8.1 P0 修复

| # | 问题 | 修复 | 影响 |
|---|---|---|---|
| P0-1 | `gen_cases.py --force` 整文件覆盖 `_gen.yaml`，人工调过的断言全部丢失 | `save_module_cases` 在 `--force` 前强制备份到 `output/gen_cases_backups/<模块>_gen.<时间戳>.yaml`；CLI 帮助文本同步提示 | 现有 `--force` 调用照常生效，但磁盘会同时多一份历史副本 |
| P0-2 | `orgList` 模块被注释掉但 `asset_summary.yaml` 仍在引用 `orgList.get_orgList` | `api/endpoints.yaml` 恢复 `orgList.get_orgList` 注册，并在注释中明确标记"历史别名，等价 `system.org_list`，新增用例请用 `system.*`"；同时新增 `api/registry.py` `_diagnose_path_duplicates()` 启动期对同名同 path 多 key 给 warning | `pytest --collect-only` 恢复正常；以后再发生"重复 key"会**主动日志告警** |
| P0-3 | 生成 schema 默认 `additionalProperties: True` → 永远不会拦截额外字段 | `gen_cases.py:to_schema(strict=False)` 默认保持兼容；新增 `--strict-schema` 选项切到 `additionalProperties: False`；每个生成 schema 顶部注入 `_meta`（生成时间/来源 key/生成器），便于审计 | 既存 `schemas/gen_*.json` 不变；CI 临时新生成用 `--strict-schema` 即可拿更严版本 |
| P0-4 | `extract`/`depends_on` 跨 yaml 引用没有校验 | `testcases/test_regression.py` 新增 `_validate_cross_references(cases)` 在 `_load_cases` 末尾执行：`depends_on` 未命中已加载 case id → WARNING；`extract.*` 没有任何 `${...}` 引用 → INFO | 仅增加日志输出，**不抛错**，对现有用例零影响 |

### 8.2 P1 修复

| # | 问题 | 修复 | 影响 |
|---|---|---|---|
| P1-1 | `variables` / `auth` / `api_client` 都是 session 级 fixtures，本地单跑用例 X 看不见前置 extract 写入的变量 | `conftest.py` 新增 `--cases-isolated` 开关：开启时 `variables` 退化为 function-scope 并以 `case_vars` 重置 | 默认仍为 session-scope（兼容 CI）；调试时打开 `--cases-isolated` 即可隔离 |
| P1-2 | `gen_cases.py` 生成的用例**没有 markers**，导致 `pytest -m regression` 一条都不匹配 | `gen_cases.py` 新增 `--default-markers`（默认 `auto`）；`emit_case` 自动贴 markers；`pytest.ini` / `core/constants.py` 同步注册 `auto` marker（已被 `TestMarkers` 单测覆盖） | 自动生成用例可用 `-m auto` 或 `-m "not auto"` 精确选中/排除；存量 *_gen.yaml 可用 `--default-markers ""` 临时关闭 |
| P1-3 | BaseClient 401 自愈后仍 401，断言会被静默吞掉、看上去像业务错误 | `core/base_client.py` 新增 `AuthExpiredAfterRelogin` 异常 + `fail_on_auth_expired` 构造参数；`conftest.py:api_client` 同步接 `config.auth_fail_on_expired` 与 `--fail-on-auth-expired` 开关；403 一并参与判定 | 默认仍「返回 Response，让断言去判定」（**完全向后兼容**）；显式开启后能在报告里清晰区分"Token 失效/重登失败"与"业务错误" |
| P1-4 | 未确认未知断言 type 是 fail-fast | 复核 `core/assertions.py:AssertionEngine.run` 已对未知 type 返回 `passed=False` 并写入 message，行为正确（已在 `TestAssertionEngine::test_unsupported_type` 单测覆盖） | 无需修改 |
| P1-5 | `requirements.txt` 不锁版本导致 CI / local drift 风险 | 本批次只在 `requirements.txt` 顶部补一行约定："CI 锁定请自行 `pip-compile requirements.in` 生成 `requirements.lock.txt`"（不强制） | 用户按需启用，不强行变更 |
| P1-6 | `har2list` 与 `gen_cases` 串联流程未在 README 显式说明 | 7.4 阶段 3 现包含完整流程：`har2list --har x.har --out list.txt && gen_cases --list list.txt --dry-run`，并明确"人工审阅清单是必经一步" | 仅文档增量 |

### 8.3 P2 修复

| # | 问题 | 修复 | 影响 |
|---|---|---|---|
| P2-1 | `endpoints.yaml` 末尾有冗余注释示例占位 | 暂留以保留 8.6 用法参考；下一轮单独立 PR 精简 | 仅做"未来要做"标注 |
| P2-2 | `pytest.ini` markers 与 `core/constants.py` 同步靠人盯 | `testcases/test_core_unit.py::TestMarkers` 已通过；缺自动同步脚本 | 已可自动校验 |
| P2-3 | 无契约 mock server | 本批次不引入新工具，文档说明暂放在 8.4「未来路线」 | — |
| P2-4 | `ruff` 未扫 `tools/` | `ruff.toml` exclude 列表追加 `__pycache__` 与 `.pytest_cache`；Jenkinsfile 改为 `ruff check core api testcases tools conftest.py`（最新配置更全，本批次先确认 exclude 落地） | 自检覆盖面增大 |
| P2-5 | `.env.example` 关键项未分类 | 仅做注释约定：现有 `.env.example` 已是一份"按需填写"清单；本批次 README 章节 7.4 表格化"必填 vs 选填" | 仅文档增量 |
| P2-6 | `AIAIAI/` 临时目录无占位说明 | `ruff.toml` 已默认排除该目录；本批次不引入占位 README | — |
| P2-7 | 双报告（pytest-html + allure）未抽象 | 本批次先加固 Allure 分类（见 P0-5）；reporter 接口抽象留待下轮 | 部分修复 |
| P2-8 | 缺 `pyproject.toml` | 新增 `pyproject.toml` 仅承担项目元信息，**不接管** pytest/ruff 等已有配置 | 零侵入 |
| P2-9 | ruff.toml 未排除 pytest_cache / __pycache__ | 已追加到 exclude 列表 | 干净运行 ruff |

### 8.4 本批次新增的可选开关汇总

| 开关 | 默认 | 行为 | 适用场景 |
|---|---|---|---|
| `pytest --cases-isolated` | off | 每条用例前用 `case_vars` 重置 `variables` | 本地单跑一条用例时 |
| `pytest --fail-on-auth-expired` | off | Token 重登后仍 401/403 → 抛 `AuthExpiredAfterRelogin` | 想要 Allure 明确区分"鉴权问题"分类时 |
| `gen_cases.py --strict-schema` | off | 生成 schema 时 `additionalProperties: False` | CI 临时回归验证字段稳定性 |
| `gen_cases.py --default-markers` | `auto` | 自动用例默认贴 markers | 控制自动用例归类（CI `-m "not auto"`） |
| `config.*.yaml: auth_fail_on_expired` | False | 同 `--fail-on-auth-expired` | 持久化到环境配置时 |

### 8.5 修复验证清单

| 项目 | 命令 | 结果 |
|---|---|---|
| 用例收集 | `python -m pytest --collect-only --env=dev` | ✅ 118 tests collected, 0 errors |
| 核心单测 | `python -m pytest testcases/test_core_unit.py -q` | ✅ 80 passed |
| Ruff lint 范围 | `python -m ruff check core api testcases tools conftest.py` | （按需；`__pycache__` / `.pytest_cache` 排除） |
| 接口注册中心自检 | `python -c "from api.registry import EndpointRegistry; EndpointRegistry.load()"` | ✅ 应打印 `WARNING` 告知 `orgList.get_orgList` 是别名 |

### 8.6 未在本批次处理（已列上 POOL）

- 重构 `core/reporter` 抽象层（统一 pytest-html + allure 输出）
- 实现端到端 mock server（`tools/mock_server.py`）以便离线冒烟
- 端到端契约测试（faker 后端跑全量用例）
- `requirements.in` + `requirements.lock.txt` 锁版本生成脚本
- 正式 review 并合并 `endpoints.yaml` 重复路径（合并 `orgList.get_orgList` ↔ `system.org_list`，需业务侧确认用例引用）


---
name: shenwa
description: 深挖（Shenwa）AI 深度调查报告工具。用于对某公司/行业/事件做深度研究调查、持续跟踪行业或个股、阅读全市场报告、生成结构化投研报告。触发词：深挖、调研、调查报告、行业分析、公司研究、财报分析、政策解读、市场复盘、卖方日报、乖离率。
description_zh: "深挖 AI 深度调查报告工具"
version: 1.0.6
---

# 深挖（Shenwa）AI 深度调查报告工具

一个基于 API 的 AI 深度调查报告工具。通过登录换取 Token，调用接口生成/查询/管理投研报告、获取乖离率、检索内置知识库。

## 重要规则（长期生效，优先级最高）

1. **只通过 API 访问**，任何情况下都不得使用浏览器抓取或访问深挖。
2. **报告必须原文完整呈现**（**归档层**），严禁精简、摘要或省略任何章节，即使报告很长也要全文返回。
   > ⚠️ 本条约束的是**取数与归档**：报告原文一字不删地取回并存档。它**不等于**报告里的每个数字都可采信——**下游（`shenwa-hybrid`）的研究正文另有采信规则**：已确认错误的段落照常归档，但不得继续支持正文结论。
3. **PDF 优先从 API 直接下载**（`download_pdf` 接口已排版好），无需自行重新生成；仅当 API 未提供 PDF 时才用完整报告原文自行生成，且只优化排版、不改动正文。
4. **不擅自简化报告**：返回报告详情前，除非用户明确要求简化，否则应返回报告原文。

## 站点与切换

- 主站：`https://www.shenwa.top`
- 备用站：`http://8.130.17.247:8088`
- 策略：`primary_fallback` —— 主站无法访问时，自动切换到备用站，无需等待人工确认。
- 所有接口路径以 base_url 为前缀。

## 认证

- 登录接口：`POST {base}/api/user/doLogin`
  - **必须带 `Content-Type: application/json`，body 用 JSON 格式**（form-urlencoded 会返回 415）。
  - 请求体：`{"userID":"<注册手机号>"}` —— **只需手机号**。
  - `userID`：注册手机号（凭据由使用者自行配置，见下方「凭据配置」）
  - `inviteCode`：**已实测不需要**（2026-09-27）：只传 `{"userID":"<你的注册手机号>"}` 即返回 `success:true` + `browserCode`；字段可完全省略，传了也不报错。
  - 成功返回 `data.browserCode`（Token），保存并在后续请求中通过 `Authorization` 请求头携带。
- Token 有效期：直到服务端拒绝。**不要仅凭 `status=AUT` 就重新登录** —— 同一个 `AUT` 既可能是 Token 过期（归为 `auth`，**有限**重登 1 次），也可能是「**你没有权限**」（归为 `permission`，**重登无效**）。客户端按 `msg` 分流，见上文「客户端错误分类与恢复（v1.8.0）」。
- 隐私：**绝不在对话中回显 browserCode/Token**。

### 凭据配置（本 skill 不内置任何账号）

**只需注册手机号 —— 不需要邀请码，也不需要密码。** 凭据存放在 **skill 目录之外**，不会随包分发。`shenwa_api.py` 按以下优先级解析：

1. **环境变量**（优先级最高）：`SHENWA_USER_ID` = 注册手机号。
2. **本地文件**：`~/.workbuddy/shenwa_credentials.json`
   ```json
   {"userID": "注册手机号"}
   ```
   可用 `SHENWA_CRED_FILE` 指定其它凭据文件路径。

未配置时脚本会**直接报错并提示以上两种方式**，不会回退到任何内置账号。账号类型（是否 adm）与配额可调 `user_info` 查询。

> 🚫 **绝对不要向使用者索要邀请码（2026-09-29 起）**：接口层早已不校验 `inviteCode`（只传手机号即返回 Token），而**新用户根本没有邀请码可填**。历史上曾有**三处**本地制品强制要求它，已全部修正：
> - `shenwa_api.py` 的 `load_cred()`：改为**只需 `userID`**；若使用者仍配置了 `inviteCode` 则一并提交（服务端不校验），缺失不再报错。
> - `shenwa_config_1.0.6.json`（含旧版 1.0.4）：`auth.user_credentials` 已删除 `inviteCode` 项；`login` 端点中该参数已标 `required: false`。
> - 包内 `README.md`：已去掉"手机号 + 邀请码，二者缺一不可"的旧说法。

> ⚠️ **本地补丁提示（最近重打 2026-10-03，基于官方 1.0.6）**：以上是对**官方 `shenwa_config_1.0.6.json`** 的本地修正 —— 官方截至 **1.0.6** 仍把 `login` 的 `inviteCode` 标为必填，与实际接口行为不符。**若日后官方升版并按新版覆盖 `SKILL.md` / `shenwa_api.py` / 配置文件，必须重新应用本补丁**，否则"反复索要邀请码"会复发。自查命令见本文末「本地补丁自查」。

### 常用 curl 登录示例
```bash
curl -s -X POST "http://8.130.17.247:8088/api/user/doLogin" \
  -H "Content-Type: application/json" \
  -d '{"userID":"<注册手机号>"}'
# 提取 token：返回 data.browserCode
```

### 账号开通 / 联系方式
- 微信：**shenwa4119**；公众号：**真深挖者也**。
- 开通账号、充值深挖点均走上述渠道。**配置时只需手机号；邀请码已不再需要，不要去索取或填写。**

### 调用工具（优先使用，勿手写 curl）
本 skill 目录下已提供命令行封装 `shenwa_api.py`（**v1.8.0 起为本地客户端**），自动完成：**首次登录也走主站 → 备用站恢复**、Token 持久化且**与账号绑定**、**错误分类**、**有界重登 / 有界重试**、**有副作用调用保护**、参数类型转换。

#### 客户端错误分类与恢复（v1.8.0）

| 分类 | 触发 | 恢复动作 |
|---|---|---|
| `network` | 连不上 / 读超时 / 无 HTTP 响应 | 同站**有界**重试（退避）→ 换站；**有副作用的调用不自动重发** |
| `http` | 收到响应但状态码非 2xx | 按码细分：401→`auth`／403→`permission`／400·422→`param`／408·429·5xx→`network` |
| `business` | HTTP 200 且 `success=false` | **不重试、不换站、不重登**；返回**非成功状态**（退出码 1） |
| `auth` | HTTP 401，或 `status=AUT` 且 msg 指向登录 / Token | **有限重登**（默认 1 次），成功后原请求重试一次 |
| `permission` | HTTP 403，或 `status=AUT` 且 msg 含「你没有权限 / 无权限 / VIP」 | **不重登、不换站、不重试** |
| `param` | HTTP 400 / 422，或服务端参数校验 msg，或本地参数校验失败 | **不重试、不换站**；保留服务端校验原文 |
| `empty` | `success=true` 但结果集为空 | 正常空结果；**若同时存在本地参数告警则改判 `param`** |
| `invalid_content` | 下载回的是错误 JSON / HTML / 非 PDF | 不保存、不报成功；**非法配置不得当作可用更新** |
| `unknown` | 分类不出来 | **保留诊断信息**，**不伪装成「无数据」** |

> ⚠️ **`status=AUT` ≠ 身份失效**：它既可能是 Token 过期（可有限重登），也可能是「你没有权限」（**重登无效**）。
> 客户端**先分权限词、再分身份词**，两者恢复动作完全不同 —— 不会「见 AUT 就全部重登」。

**有副作用调用（`report_publish` / `collect_report` / `delete_report`）的安全约定**：结果不确定时（超时 / 5xx / 无法解析）**不做第二次提交、也不换站重发**，改为写一条**本地最小任务记录**并用**只读**能力核对；不能唯一确认就返回「**提交状态未知**」（`submit_state=submit_unknown`，**退出码 2**）。
⚠️ 该本地记录**不是**服务端幂等保障 —— 客户端**不声称**已实现幂等，也无法阻止服务端重复受理。

**重试预算**：默认「每站 2 次 + **真实经过时间上限 30s**」，**等待时间与网络请求耗时都计入**；超预算立即停（不做「次数够了才看时间」的事后检查）。

**机读输出**：加 `--json` → stdout 只有**一个 JSON 对象**（`ok` / `category` / `api` / `base` / `attempts` / `elapsed` / `submit_state` / `warnings` / `diagnostics` / `data`），调试信息一律进 **stderr**；退出码 `0` 成功（含 `empty`）／`1` 失败／`2` 提交状态未知。不加 `--json` 时仍是旧的「`// base: <base>` + JSON」人读格式。

**参数与下载**：代码 / 标识符 / 版本类参数**保持字符串**（`000300.CSI` 不会被转成 `300`）；裸代码会得到**明确提示**而不是静默返回空。`download_pdf` 按 `%PDF-` 与 Content-Type 校验，**错误 JSON / HTML 不会被存成「成功」的 PDF**。

**接口定义由哪里维护**：唯一权威是 **`shenwa_api.py` 的 `APIS` 字典**；`shenwa_config_*.json` 只作说明，**不参与路径 / 方法决策**。

```bash
cd <专家包目录>/skills/shenwa      # 或本机直连时：~/.workbuddy/skills/shenwa
PY="$(command -v python3 || command -v python)"   # 任意 Python 3 即可，无需写死路径
$PY shenwa_api.py list                       # 查看全部接口
$PY shenwa_api.py user_info
$PY shenwa_api.py templates
$PY shenwa_api.py report_list type=public num=20
$PY shenwa_api.py report_detail aid=xxx
$PY shenwa_api.py report_status aid=xxx
$PY shenwa_api.py download_pdf aid=xxx out=/abs/path.pdf
$PY shenwa_api.py get_guaili_rates maxDays=20
$PY shenwa_api.py get_right_side codes=399986.SZ dayOffset=2
$PY shenwa_api.py knowledge_search keyword="光模块" knowledgeBase=short7d searchMethod=hybrid rerankEnable=true topK=5
$PY shenwa_api.py --json user_info             # 机读模式：stdout 只有一个 JSON 对象，调试信息走 stderr
$PY shenwa_api.py report_wait aid=xxx          # 轮询原报告终态（不新建报告）
$PY shenwa_api.py errors                       # 打印错误分类与恢复规则
$PY shenwa_api.py selfcheck                    # 接口注册表 vs 配置 漂移自查
```
Windows 下输出中文建议加 `PYTHONIOENCODING=utf-8`；结果较长时用 `> /tmp/x.json` 落盘再读取，避免控制台截断。

## 接口清单

| 接口名 | 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|---|
| login | POST | /api/user/doLogin | 全部 | 登录换取 browserCode（需 JSON body） |
| user_info | GET | /api/user/getUser | 全部 | 查用户类型、restPoint、expireTime |
| templates | GET | /api/manage/getTemplateListWithType | 全部 | 分类视图获取模板列表 |
| report_list | GET | /api/home/getList | 全部 | 报告列表（type: public/self/collect/history） |
| report_detail | GET | /api/home/getDetail | 全部 | 报告详情（aid） |
| report_publish | POST | /api/manage/wfStartPublish | 全部 | 异步发布报告（**tid / question / text 三者必填**，text 不可缺省） |
| report_status | GET | /api/home/getReportStatus | 全部 | 报告状态（aid） |
| download_pdf | GET | /api/home/downloadPdf | 全部 | 下载报告 PDF（aid） |
| collect_report | POST | /api/home/doArticleAction | 全部 | 收藏/取消收藏（aid/action=collect） |
| delete_report | GET | /api/home/deleteArticle | 全部 | 删除自己的报告（aid） |
| get_guaili_rates | GET | /api/review/getGuailiRates | VIP2 | 乖离率（date/maxDays） |
| get_right_side | GET | /api/market/getRightSide | ADM/VIP2 | 右侧确认报告（**实测路径无双 api**，参数 codes / dayOffset） |
| knowledge_search | POST/GET | /api/data/knowledgeSearch | VIP2 | 知识库检索（keyword 等，**参数走 query string**） |
| latest_version | GET | /api/manage/getLatestVersion | 全部 | 查看最新版本 |
| download_version | GET | /versions/shenwa_config_{version}.json | 全部 | 下载指定版本的官方配置文件原文（**版本类参数保持字符串**，非法配置不算可用更新） |

- 统一响应：`success`（bool）、`status`（SUC/AUT/SRV）、`data`、`msg`、`time`。
- `status=AUT` **不能一律当成 Token 过期**：它还可能是「**你没有权限**」（重登无效）。v1.8.0 起客户端按 `msg` 分为 `auth`（有限重登）与 `permission`（不重登），见上文「客户端错误分类与恢复」。
- **所有 POST 接口（login/report_publish/collect_report/knowledge_search）都需 `Content-Type: application/json` + JSON body**；GET 接口用 query 参数即可。

### ★ 发布参数本地校验与长度口径（v1.10.0 新增）

`report_publish`（`wfStartPublish`）的 `tid` / `question` / `text` 在**发请求前**做本地校验。
校验位置：`shenwa_api.py` 的 `Client.call()` 内的 `validate_params()` —— 它是**所有接口调用的唯一入口**，
`--dry-run` 即触发且不发任何请求，因此**越界参数在发布前就被拦下，不消耗一次发布**。

| 参数 | 必填 | 类型 | 长度上限 | 依据（三档标注） |
|---|---|---|---|---|
| `tid` | 是 | 字符串 | — | **接口约定**（官方 `required:true`）＋ **特定条件下实测**（传 int 报 400：`The JSON value could not be converted to System.String`） |
| `question` | 是 | 字符串 | **60 字** | **接口约定**（官方明写「不可多于60字，复杂要求可在背景消息中补充」） |
| `text` | 官方标**可选** / 实测**缺省失败** | 字符串 | **500 字** | 长度 = **接口约定**（官方「不可多于500字」）；必填性 = **冲突**（见下） |

**类型约定**：`tid` 必须是字符串（传 int 会报 400）；`question` / `text` 也必须是字符串 —— 非字符串一律在本地报错，不交给服务端。

**长度计算口径**：按 **Unicode 码点数**（Python `len()`）计，**不 strip**，与「按原文提交」一致。
⚠️ 官方只说「不可多于 N 字」，**未说明按码点还是按字节** → 服务端实际计数方式属**待验证推断**，本地不据此实现。

**冲突登记（不猜测服务端规则）**：官方配置标 `text` 为 `required: false`，包内实测「完全缺省 → `status=SRV` + `Missing required parameters`」。
→ 遵守**不得把合法可选参数统一改成必填**：`text` 的缺失 / 空字符串 / 纯空白判为 **warning（不阻断）**，
以常量 `PUBLISH_TEXT_REQUIRED = False` 显式登记；取得服务端确认后改此常量即可切换为硬拦截。

**不做的推断与不做的事**：不假设服务端会 strip 空白；不假设空串等于缺失；
**不得为通过校验而机械截断问题、删除关键限定条件或改写研究截止时间**。

### ✅ 版本跟踪：已升级至官方 1.0.6（2026-10-03 完成）

- `getLatestVersion` 返回 **`latestVersion=1.0.6`**；本机副本（用户级 + 真深挖专家包 + git 仓库）**均已升级到 `shenwa_config_1.0.6.json`**，`shenwa_api.py` 注册表为 **15 个接口**。
- **1.0.5 相对 1.0.4 只新增 1 个接口**：`get_right_side`，其余 14 个接口路径与方法**完全未变** → **非破坏性升级**。
- **1.0.6 相对 1.0.5 只修正 1 处**：`get_right_side` 的路径（去掉重复的 `api` 段），**无新增接口、无破坏性变更**；本机脚本早于官方实测并已采用正确路径，故本次为**配置与文档对齐**。
- **本机 `shenwa_api.py` 的接口注册表是硬编码的，不会自动跟进官方新版**；也没有任何机制会在启动时拉取官方配置覆盖自身。**升级必须手工做。**
- 🔑 **接口定义由哪里维护（v1.8.0 明确）**：唯一权威是 **`shenwa_api.py` 的 `APIS` 字典**；`shenwa_config_*.json` 是官方下发的**说明性配置**，客户端**不读取、不依赖**它决定路径或方法 —— **只改配置说明不会改变客户端行为**。自查命令：`python shenwa_api.py selfcheck`（报出配置与注册表的漂移；本机 1.0.6 对齐后自查结果为 `drift: []`，无漂移）。
- 🔴 **官方 1.0.5 / 1.0.6 仍把 `inviteCode` 写进 `auth.user_credentials`，且 `login.inviteCode.required=true`** → **每次升级后 `_local_patch`（免邀请码补丁）必须重打**，否则"反复索要邀请码"立刻复发。本次已在 **1.0.6** 上重打（2026-10-03）。
- ⚠️ **官方 1.0.5 / 1.0.6 配置文件的 JSON 本身不合法**：`get_right_side` 的 `success_schema` 里有 4 处未加引号的占位符（`"date": <日期>` 等），标准 `json.load` 抛 `JSONDecodeError`（第 980 行）。**解析前须先把占位符补上引号。**
- 🔴 **官方 1.0.5 配置把 `get_right_side` 的路径写错了，官方 1.0.6 已修正**：1.0.5 写 `/api/api/market/getRightSide`（双 `api`），**主站与备用站实测均为 404**；真实路径是 **`/api/market/getRightSide`**（实测 HTTP 200）。**本机 1.0.6 配置已同步采用正确路径**；脚本本就按实测值实现，**勿改回旧写法**。
- ⚠️ **官方 1.0.5 / 1.0.6 的响应结构与文档不符**：schema 写成 `data[].values[]` 嵌套，**实测为扁平结构** `data[]` 直接含 `date` / `code` / `name` / `data`。

### get_right_side 实测说明（2026-09-29）

- **真实路径**：`GET /api/market/getRightSide`（官方配置的 `/api/api/market/getRightSide` 实测 404）。
- **权限**：`ADM` / `VIP2`；无权限返回 `status=AUT` + `msg="你没有权限"`。
- **参数**：`codes` = 行业/主题指数代码（如 `399986.SZ` 中证银行、`931743.CSI`），多个用 `,` 分隔，**留空返回全部指数**（实测 **62 个**行业/主题指数 × `dayOffset` 天，如 dayOffset=2 → 124 条）；`dayOffset` = 返回最近交易日数，**实测有效范围 `1`~`20`**（官方描述写"`>1` 且 `<=20`"，但实测 **`1` 同样可用**），缺省 2。
  - ⚠️ `dayOffset=0` → **不带 `codes`** 时返回 `success=false, status="SRV", msg="Index was out of range..."`；**带 `codes`** 时反而返回 `success=true` + **空数组** `data: []`（静默失败，极易误判为"该指数无数据"）。
  - ⚠️ `dayOffset=21` → **HTTP 400** `{"code":400,"msg":"日期偏移超出范围"}`。
- **返回**：`data` 是**扁平数组**，每项 `{date, code, name, data}`；其中 `data` 是**再一层 JSON 字符串**（需二次 `json.loads`），内含 `q1`~`q5` 五个检查点与 `state` / `state_trans`（下详）。
- 🔑 **`dayOffset=N` 的准确语义（2026-09-30 实测修正）**：返回**最近 N 个交易日的纵向时间序列**，长度 **62 × N**（`dayOffset=1` → 62 条；`=2` → 124 条；`=5` → 310 条）。
  - ⚠️ **它不是一个"截面快照"，而是"面板数据"** —— 拿到 310 条时**必须按 `date` 字段分组**才能得到"某日全市场截面"。若直接对全量数组做 `transition_type` 分桶，会把 5 天的迁移**重复累加**（实测：直接分桶得 Upgrade=11 / Breakdown=9，按 9-30 截面过滤后实为 **Upgrade=2 / Breakdown=0**）。
  - **最新交易日在数组末尾**（日期降序排列：`2026-09-30, 09-29, 09-28, 09-24, 09-23`）。
  - ✅ **截面可复现**：同一交易日的 62 条记录，在 `dayOffset=1` 与 `dayOffset=5` 两次调用中**逐字段完全一致**（实测 0 处差异）—— 可放心用 `dayOffset=1` 做每日快照、用 `dayOffset=5~20` 做跨日差分。
  - ⚠️ **`dayOffset=1` 与 `dayOffset=2` 的"最新日"是同一个交易日**（都是 09-30）；差别只在**是否附带前一日做对照**。想要"S0→S1 这种迁移"可读，至少要 `dayOffset=2`（但注意：迁移字段 `transition_type` 在**每一天的记录里都已自带**，无需自己算差分）。
- **检查点语义**：`q1` 硬资格（未创新低 / 5 日变化改善 / 极端低分位连续改善 / 单日极值反弹）、`q2` 均线收复、`q3` 回踩确认、`q4` 成分股评估（`b_core_status` 核心股相对强势 ＋ `c_breadth_status` 板块广度）、`q5` 催化反馈（`catalyst_info` ＋ `price_performance.retention_rate_pct` 即 T 日涨幅保留率）。每个检查点都带 `status`（`pass` / `partial` / `fail` / `pending`）与人类可读的 `evidence`。

#### 🔑 本质是一台状态机，不只是报告（2026-09-29 实测，策略类任务的数据基础）

`state` 内部是完整的**阶段状态机**，含状态、评分、否决三大块：

| 字段 | 内容 | 实测取值 |
|---|---|---|
| `state.state_mapping` | `current_state` / `previous_state` / `state_action` / `one_sentence_reason` | 状态 **`S0`~`S5`**；`state_action` ∈ `maintained` / `broken` / `upgraded` / `downgraded` |
| `state.qualification_summary` | `q_overall` / `q_pass_days` | `q_overall` ∈ `fail` / `near` / `pass` |
| `state.scores_summary` | `independent_cluster_count` / `pass_count` / `passed_clusters[]` | 如 `["price_bias"]` |
| `state.veto_check` | **`has_veto`** / `triggered_flags[]` / `details` | 见下方否决标志表 |
| `state.evidence_facts[]` | 人类可读的证据清单 | —— |
| `state.disclaimer` | 官方免责声明 | 明确"不构成投资建议，不推荐行业与仓位" |
| `state_trans.*` | `prev_state` / `curr_state` / `transition_path` / **`transition_type`** / `s_days` / `q_pass_days` / `has_veto` / `veto_reasons` / `audit_flag` | `transition_type` ∈ **`Hold`** / **`Upgrade`** / **`Breakdown`** / `Unknown` |

**五类否决标志**（2026-09-29 实测当日全市场触发计数；62 个指数中 **47 个（76%）至少触发一项**）：

| 标志 | 含义 | 当日触发 |
|---|---|---|
| `good_news_bad_response` | 利好不涨（T 日大涨后 T+2 保留率不足） | 35 |
| `price_or_bias_new_low` | 价格或乖离率创新低 | 19 |
| `large_up_day_swallowed` | 大阳线被吞没 | 1 |
| `leader_collective_break` | 龙头集体破位 | 1 |
| `ema20_failed_with_volume` | 放量跌破 EMA20 | 1 |

**典型用法**（v1.3.0 起分两类：**右侧状态族直取** vs **单标的纵深**）：

> ⚠️ **v1.7.0 判据改版（重要）**：走哪条路**只由「指标族」决定**，**不由数据形态或对象数量决定**。
> 本通道**只服务「右侧状态」**（右侧 / 阶段 / 迁移 / 否决 / 资格）。**乖离率 → S8/D9，估值 → D1/D7，
> 财务比较 → D2** —— 它们即使也是横截面、也要「排名 / 名单」，也**不得**走本通道。
> 以下这些语汇**只表示批量方式**（`codes` 留空 = 全量；逐个指定 = 少数几个），**不是走通道的判据**。

#### A. 右侧状态族直取（全量扫描 —— 推荐路径，0 点）

**批量方式提示**：全市场 / 所有行业 / 所有概念 / 哪些板块 / 扫一遍 / 排名 / 分布，或横向比较 **≥ 3 个**标的 → `codes` 留空一次取全量。**这类任务严禁逐个板块发报告。**

```bash
# codes 留空 = 一次拿到官方精选池全量（实测 62 个行业与主题指数，交付时按本次实际返回标注）；dayOffset 实测有效范围 1~20
# ⚠️ dayOffset=N 返回「条数 × N」的纵向序列 → 必须按 date 分组取目标交易日截面！
$PY shenwa_api.py get_right_side dayOffset=1 > /tmp/rs.json
```

**⚠️ 分桶前必须先按 `date` 过滤（2026-09-30 实测，最容易踩的坑）**：

```python
rows_all = json.loads(payload)["data"]          # 条数 × N 条（N 为实际返回的标的数）
target = sorted(set(r["date"] for r in rows_all))[-1]
rows = [r for r in rows_all if r["date"] == target]   # ← 先切截面，再分桶
```

若跳过这一步，直接对全量数组分桶，会把 N 天的迁移**重复累加**，得到虚高的升级/破坏数（实测 dayOffset=5 直接分桶 → Upgrade=11/Breakdown=9，正确值应为 **Upgrade=2/Breakdown=0**）。

1. **每日状态迁移扫描**（**0 点、秒级、不发布任何报告**）：取数后按 `state_trans.transition_type` 分桶，产出**四张表 + 计数**：
   - **升级名单**：`transition_type == "Upgrade"` → 附 `prev_state → curr_state` 与 `q_pass_days`
   - **破坏名单**：`transition_type == "Breakdown"` → 附 `prev_state → curr_state`，**跳级（如 S1→S5）须标注**
   - **临界名单**：`state.qualification_summary.q_overall == "near"` 或 `state_trans.q_pass_days > 0`
   - **否决名单**：`state.veto_check.has_veto == true` → 附 `triggered_flags[]`
2. **可替代 `tid=3002` 的"发布 + 轮询"路径**，直接拿到结构化 JSON 而非报告文本。⚠️ **tid=3002 的适用边界已收窄为「单板块纵深」**（一个板块要读成因 / 推演 / 判断时才用它），**不用于全市场扫描** —— 那是 `5 点 × N`，成本随标的数线性膨胀。
3. **两段式（既要名单又要解读）**：第 1 段 0 点直取筛出候选名单 → 第 2 段只对 top-N（默认 1~3）发 `tid=3002 / 2001`。点数从 `N标的 × 5` 降到 `1~3 × 5`。
4. 与 `get_guaili_rates` 交叉 → 「120 日分位低 ＋ Q1 pass ＋ 无否决」的候选池过滤。

##### 一键复跑脚本骨架（2026-09-30 实测可用）

> 该骨架已在一次完整链路中跑通，可直接复用；路径按下方 `_skill_dir()` 自动定位，无需手改。

```python
import json, io, collections, subprocess, sys, os

PY = sys.executable        # 用当前解释器，不写死路径

def _skill_dir():
    """按序定位 shenwa_api.py 所在目录：环境变量 → 本包 skills/shenwa → 全局 skill。"""
    cand = [os.environ.get("SHENWA_SKILL_DIR")]
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()
    cand += [os.path.join(here, "skills", "shenwa"),
             os.path.join(here, "..", "skills", "shenwa"),
             os.path.expanduser("~/.workbuddy/skills/shenwa")]
    for d in cand:
        if d and os.path.isfile(os.path.join(d, "shenwa_api.py")):
            return os.path.abspath(d)
    raise SystemExit("未找到 shenwa_api.py；请设 SHENWA_SKILL_DIR 指向 skills/shenwa 目录")

API = os.path.join(_skill_dir(), "shenwa_api.py")

# ① 取数（原始输出首行是 "// base: ..." 注释，必须剔除后再 json.loads）
raw = subprocess.run([PY, API, "get_right_side", "dayOffset=5"],
                     capture_output=True, text=True, encoding="utf-8").stdout
payload = json.loads("\n".join(l for l in raw.splitlines()
                               if not l.strip().startswith("//")))

# ② 归一化（内层 data 是再一层 JSON 字符串；关键字段在 state / state_trans 下）
def norm(it):
    inn = it["data"]
    inn = json.loads(inn) if isinstance(inn, str) else inn
    stt, st = inn.get("state") or {}, inn.get("state_trans") or {}
    sm  = stt.get("state_mapping") or {}
    scs = stt.get("scores_summary") or {}
    vc  = stt.get("veto_check") or {}
    flags = vc.get("triggered_flags") or []
    if not flags and st.get("veto_reasons"):     # 兜底：从 state_trans 取
        flags = [x.strip() for x in str(st["veto_reasons"]).split(",") if x.strip()]
    return dict(date=it["date"], code=it["code"], name=it["name"],
                prev=st.get("prev_state"),
                cur=st.get("curr_state") or sm.get("current_state"),
                path=st.get("transition_path"), tt=st.get("transition_type"),
                q_pass_days=st.get("q_pass_days"),
                q_overall=st.get("q_overall") or (stt.get("qualification_summary") or {}).get("q_overall"),
                has_veto=st.get("has_veto") if st.get("has_veto") is not None else vc.get("has_veto"),
                flags=flags, clusters=scs.get("passed_clusters") or [],
                reason=sm.get("one_sentence_reason") or "")

all_rows = [norm(x) for x in payload["data"]]

# ③ 切截面（★ 关键一步，见上文警告）
g = collections.OrderedDict()
for r in all_rows: g.setdefault(r["date"], []).append(r)
latest = sorted(g)[-1]
rows = g[latest]

# ④ 分桶
up   = [r for r in rows if r["tt"] == "Upgrade"]
bd   = [r for r in rows if r["tt"] == "Breakdown"]
near = [r for r in rows if r["q_overall"] == "near" or (r["q_pass_days"] or 0) > 0]
veto = [r for r in rows if r["has_veto"]]
print(latest, "升级", len(up), "破坏", len(bd), "临界", len(near), "否决", len(veto))
```

> 字段路径踩坑记录：早期误写 `state_mapping` / `veto_check` 为**内层顶层键**，导致 `curr_state` 全为 `None`、否决恒为 0。
> **实际路径是 `state.state_mapping` / `state.veto_check`**，`state` 与 `state_trans` 才是内层顶层键。
> 排查手法：先 `print(list(inner.keys()))` 再逐层 `walk()` 打印键名，不要凭 schema 猜路径。

##### 单日截面实测基线（2026-09-30，供后续日间差分对照）

| 交易日 | 覆盖 | Upgrade | Breakdown | Near | Veto |
|---|---|---|---|---|---|
| 2026-09-23 | 62 | 2 | 0 | 2 | 41 |
| 2026-09-24 | 62 | 5 | 4 | 3 | 44 |
| 2026-09-28 | 62 | 0 | 2 | 1 | 46 |
| 2026-09-29 | 62 | 2 | 3 | 1 | 47 |
| **2026-09-30** | 62 | **2** | **0** | **2** | **47** |

- 9-30 阶段分布：S0=3 / S1=6 / S2=5 / S5=**48**（S3、S4 均为 0）。
- 9-30 否决标志：`good_news_bad_response` 36、`price_or_bias_new_low` 18、其余三类各 1（合计触发 57 次，47 个指数）。
- **否决率连续 4 日攀升至 75.8%**（47/62），且 S5 存量占 77.4% —— **"Breakdown=0"不等于结构健康**，是存量破坏堆积、当日无新增下移。汇报时须点明这层区别，否则极易被误读为"市场转好"。

> **产出形态约定（客户已确认）**：横截面任务只交付**四张表 + 计数**，**不下分析结论、不套六段结构、不发布深挖报告** —— 它是"数据层"，不是"报告层"。分桶口径一律以 `state_trans.transition_type` 为准（`state.state_mapping.state_action` 是单标的自身口径，不用于迁移分桶）。

> **交付物必须标注覆盖范围**：「本名单基于深挖右侧确认框架覆盖的 **〈本次实际返回的行业与主题指数条数〉**」（最新实测为 **62 个**，须注明该口径来源与取数日期，**不得写死**）。**不得**写成"全市场所有概念"——A 股概念板块有数百个，这 62 个（实测值）是**官方精选池**，不是全部。

> **权限与降级**：本接口需 **ADM / VIP2**，无权限返回 `status=AUT` + `msg="你没有权限"`。此时**如实告知**「本账号无 VIP2 权限，无法执行全量扫描」，并说明可联系微信 `shenwa4119` 升级；**不得**偷偷退回"逐个板块发报告"（那是 5 点 × N，而用户以为在做扫描）。⚠️ `get_guaili_rates` 同样标 VIP2，**降级路径也被堵住**。

> **数据分层（v1.4.0 修正，取代原「等同行情、可直接使用、无需二次校验」）**：本接口字段**不因"是接口原始输出"而免检**。三层分开看——**观测数据**（接口直给字段）/ **计算指标**（按公式算出）/ **模型判定**（深挖给的状态、阶段、否决、迁移）。本地质量检查**只作用于前两层**（先按 `date` 切截面；检查 日期与代码唯一性、覆盖数量、重复记录、未知状态、否决字段三态），**模型判定只做一致性核对、不得改判、不得另造阶段标准**。字段的**业务含义**（S0~S5 各代表什么）须以本文档的实测记录为准，**不得自行发明解释**。⚠️ **`has_veto` 缺失 / None / 解析失败 ≠ 无否决**（须单列报出）；**空结果、明确业务失败也不等于「无异常」**。实现见 `skills/shenwa-hybrid/scripts/right_side.py` 与 `data_qc.py`。

#### B. 单标的纵深（读某个板块/题材的阶段与成因）

单一板块（或 ≤2 个）要"读成因、读推演、读判断"时，**仍走模板报告**：板块级用 `tid=3002`（右侧区间监测，重体量、挂载 10 只），需要综合维度时用 `tid=2001`。须发布 + 轮询，耗时 **25 秒 ~ 5.5 分钟**（抖动大）。

- ⚠️ **`codes` 传入非指数代码（如 `000300`）不报错但返回空数组**，容易误判为"无数据"，须用带交易所后缀的指数代码。

## 接口实测注意事项（2026-09-26 实测，修正官方文档）

1. **`text` 字段实际必填，不是可选。** 官方文档（含 shenwa_config_1.0.4.json）标注 text 为"非必填"，**实测为错**：完全缺省 `text` 时返回 `{"success":false,"status":"SRV","data":null,"msg":"Missing required parameters..."}`。
   - 传空串 `""` 虽能成功，但 aid 前缀变为 `ManualArticle@@<hash>`，与带正常 text 时的 `ManualNews@@<hash>@@<时间戳>` **不同**，可能走另一条生成管线。
   - **结论：每次发布都必须带一段有意义的 `text`，不要用空串占位。**
2. **深挖有时间截面语义校验。** `text` / `question` 中的日期与系统当日不一致会被拒绝，msg 形如"所有模板均以当前时间截面为分析框架，对未来时间截面的分析不准确"。**日期必须用真实当日。**
3. **`aid` 含 `@@` 分隔符**，用 sed/grep 截取时易只取到第一段，**必须整串保存与传递**。
4. **`report_status` 响应里有两处 `status`**（顶层 `SUC` 与 `data.status`），取报告状态须用后者。
5. **生成耗时实测分布（2026-09-26，17 个模板全覆盖 + 3 次端到端实跑）**：轻/中体量（个股/事件/概念/日报）**25 秒 ~ 5 分钟**；中体量（关键词跟踪 tid=3001）**40 秒 ~ 13 分钟**；重体量（tid=3002 右侧区间监测）**25 秒 ~ 5.5 分钟**。
   - **耗时抖动极大**：tid=1001 首测 **>13 分钟未完成**、复测仅 **2.5 分钟**；tid=9 实测 **52 秒**；tid=3002 首测 5.5 分钟、复测 **25 秒**。同一模板相差 **10 倍以上**，抖动来自**服务端排队/冷启动**。**慢生成不等于失败，快完成也完全正常。**
   - 原文档"轮询最多 24 次（12 分钟）"**已被证伪**，须放宽至 **40 次（20 分钟）**，否则会把正常生成误判为超时。
   - **探测节奏**：前 60 秒每 10 秒探测（重体量可能秒回），之后每 30 秒。
6. **`stocks` 字段可判定报告标的层级**：格式 `名称,代码,评级,市场`，多条以 `;` 分隔。**`null` = 市场/事件级（如 tid=12 智能投研日报，不挂具体标的，不得用于个股任务）**；1~2 只=个股级；5 只=题材级；8 只=主题级；10 只=板块级；14~16 只=产业链级。
   - ⚠️ **取值时机**：`null` 有两种含义，需分场景——① **发布回执里恒为 null**（字段尚未生成，属正常，不表示层级）；② **`getDetail` / `getList` 记录里为 null** 才是真的"市场/事件级"。**层级复核须在报告完成后做**（详见下文第 9 条）。
   - **模板速查**：仅 tid=3002（右侧区间监测助手）为重体量＋板块级，个股任务禁用。

### 模板全表（实测 `GET /api/manage/getTemplateListWithType`，2026-09-27，共 17 个 / 8 类）

| tid | 类目 | 标题 | 适用 | cost |
|---|---|---|---|---|
| 0 | 事件感知 | 简要分析 | 无预设模式的 AI 分析 | 1 |
| 1 | 事件感知 | 事件分析（偏宏观） | 事件的三维度、宏观逻辑 | 2 |
| 2 | 事件感知 | 事件分析（偏市场影响） | 多市场联动、产业链冲击 | 2 |
| 12 | 事件感知 | 智能投研日报 | 模拟买方首席分析师；**市场/事件级，`stocks` 为 null** | 2 |
| 3 | 产业链分析 | 产业链图谱剖析 | 产业链核心公司动态 | 2 |
| 4 | 产业链分析 | 行业产业链内部竞争格局分析 | 竞争格局重构 | 2 |
| 11 | 产业链分析 | 产业链深度研究框架 | 模拟顶级对冲基金，找价值沉淀环节 | 2 |
| 5 | 题材热度 | 概念炒作分析 | 技术-商业 / 情绪 / 风险-机会 | 2 |
| 6 | 题材热度 | 概念炒作阶段分析 | 炒作持续性与潜在风险 | 2 |
| 8 | 个股与估值 | 个股深度分析（偏新闻影响） | 新闻事件短期冲击 | 4 |
| 9 | 个股与估值 | 个股深度分析（偏公司估值） | 深度分析 + 估值预算 | 4 |
| 10 | 个股与估值 | A股产业链公司独立分析 | 单公司深度、核心投资逻辑 | 4 |
| 7 | 风控视角 | 个股深度分析（偏财务风险排查） | 财务指标驱动、财报场景 | 4 |
| 1001 | 价值挖掘 | 巴菲特视角 | 过滤噪声发现价值逻辑 | 2 |
| 2001 | 深度推理 | 智能深度推理 | 按需求自适配分析流水线 | 5 |
| 3001 | 市场实时跟踪 | 关键词实时跟踪 | 定制关键词，跟踪产业链/行业/个股短期逻辑演化 | 4 |
| 3002 | 市场实时跟踪 | 右侧区间监测助手 | **结合乖离率、均线、量能、消息面**做右侧检测；**板块/主题级**，个股禁用 | 5 |

**选型速记**：
- 「右侧 / 分化 / 突破前夜 / 乖离率」→ **3002**（板块主题级）
- 「跟踪某关键词的短期逻辑演化 / 卖方观点变化」→ **3001**
- 「今日卖方研报 / 当日主线 / 投研日报」→ **12**（市场级，不挂票）
- 「产业链格局 / 谁在让位 / 价值沉淀环节」→ **3 / 4 / 11**
- 「单公司估值 / 财务风险 / 新闻影响」→ **9 / 7 / 8**
- 「概念炒作阶段 / 题材机会风险」→ **6 / 5**
- 「需求复杂说不清」→ **2001**

7. **发布可高并发**：实测 4 份、11 份并发均正常；轮询可在同一循环内统一探测。

8. **日期必须动态取**：发布前置背景消息时，日期应取自 `GET /api/manage/getLatestVersion` 返回的 `time`（服务端当日），不要用本地日期写死，以免与服务端跨日不一致被时间截面校验拒绝。
9. **`stocks` 在发布回执中恒为 null（2026-09-26 实测）**：`POST /api/manage/wfStartPublish` 返回的 `data.stocks` 恒为 `null`；该字段是**报告生成结果**字段，只有报告完成后才在 `GET /api/home/getDetail` 与 `GET /api/home/getList` 的记录中填充（实测：tid=9 发布回执 null → 完成后 `宁德时代,300750,积极,A`）。**因此"读发布回执校验层级"不可行，层级校验须后移到报告完成之后。**
10. **轮询"调用异常" ≠ "报告失败"（2026-09-26 实测）**：高频轮询时会遇到服务端瞬时异常（超时/非 JSON/5xx），事后重试即恢复（实测事后 6/6 正常）。**只有 `data.status == "error"` 才是报告失败**；调用异常应走重试+退避、必要时切换站点，连续多次异常只上报"无法获取状态"，**不得报"报告失败"**。（曾因此误判一份实际 52 秒即完成的报告为失败，并空转到 20 分钟上限。）
11. **耗时抖动极大**：同一量级模板实测 **52 秒 ~ 13+ 分钟**（tid=9 为 52 秒；tid=1001 首测 >13 分钟、复测 2.5 分钟）。建议**前 2 分钟每 10 秒探测**、之后每 30 秒，上限 40 次 / 20 分钟。
    - **v1.8.0 起直接用 `python shenwa_api.py report_wait aid=<ID>`**：只查**原 aid**、**绝不因轮询异常新建报告**；连续异常超上限返回「无法获取状态」（**不判报告失败**）；**只有服务端明确返回 `data.status == "error"` 才判生成失败**。
12. **`tid` 必须是字符串，不能传 int（2026-09-26 实测）**：`wfStartPublish` 的 `tid` 若传成 JSON 整数会直接 **HTTP 400**，body 为
    `{"errors":{"request":["The request field is required."],"$.tid":["The JSON value could not be converted to System.String. Path: $.tid ..."]}}`。
    ⚠️ 该错误信息前半句（"The request field is required."）具有很强的误导性，会让人误以为缺参数 —— **真正原因在 `$.tid` 的类型上**。
    `shenwa_api.py` 已用 `STRING_PARAMS = {"tid"}` 白名单强制其保持字符串，调用时不要绕过。
13. **`shenwa_api.py` 的两个静默 bug 已修复（2026-09-26）**，如遇"脚本跑完毫无输出"先检查这两处是否被改回：
    - ① `_coerce()` 尾部曾夹带 `call()/print()` 主流程代码（死代码），导致 `main()` 解析完参数后**什么都不执行、退出码 0、零输出**；
    - ② `call()` 原先只捕获通用 `Exception`，HTTP 400 的服务端校验详情被吞掉。现已在 `call()` 中单独捕获 `urllib.error.HTTPError` 并回显 body（形如 `HTTP 400 Bad Request | body={...}`）—— **排错时优先读这段 body**。
14. **发布回执的 `data.aid`** 形如 `ManualNews@@<hash>@@<时间戳>`，须**整串**保存与传递（含全部 `@@`）。
15. **凭据不内置（2026-09-26 改造）**：`shenwa_api.py` 不再硬编码账号，改为读环境变量 / 包外凭据文件（见「凭据配置」）。这是为了让本 skill 可安全随专家包分发给他人 —— **改造后不要为了"方便"把账号写回代码或文档**，否则分发即等于泄露账号。
16. **`question` 硬上限 60 字（2026-09-26 实测）**：超过返回 `SRV` + `"问题不可多于60字，复杂要求可在背景消息中补充"`。**复杂需求一律写进 `text`，`question` 只留一句核心问题。**
17. **`text` 中不得显式声明日期（2026-09-26 实测，时间截面校验的隐蔽变体）**：写"时间截面：2026年9月26日"这类显式日期声明会被拒，msg 形如 `"当前时间并非2026年9月26日..."` —— **即使服务端 `latest_version.time` 就是 2026-09-26**，该表述仍会被时间截面校验拦下。
    - **规避方法**：`text` 里改用地缘/事件描述（"近期""节前""上一交易日"），**不要写绝对日期**。仅当需要锚定今日时才用 `latest_version.time` 的值。
18. **全市场/含宽基指数的提问会被拒（2026-09-26 实测）**：`question` 或需求若涉及宽基指数（如"创业板指"）或"全市场整体分析"，返回 `SRV` + `"您的提问中包含了宽基指数（XX）相关分析…麻烦您指定具体的行业、概念板块进行提问"`。**全市场类需求须改写为指定具体行业/概念板块**；纯全市场复盘建议直接读公共列表的日报（tid=12）。
19. **tid=3002（右侧区间监测助手）会间歇性故障——但不要永久拉黑（2026-09-26 三次实测，结论已更正）**：
    - ❌ 早前两次提交（不同 `question`/`text`）在生成中转 `error`，`err_msg` 为 `HTTP request failed, code:400 ... body: {"errors":{"codes":["The codes field is required."]}}`；据此曾记录"3002 链路故障、不要优先选"。
    - ✅ **同日第三次提交（创新药右侧任务）一次成功、仅 25 秒完成**，产出完整右侧监测报告（S1 阶段判定 + 四层验证 + checklist）。**同一模板、同类参数，结果完全不同 → 该 400 是服务端间歇性下游故障，不是稳定故障。**
    - **正确处置**：3002 是**板块/主题级右侧结构监测的唯一合适模板**（能输出阶段判定、硬资格 Q1-Q3、四项评分、确认/失效条件），**板块级右侧任务应当优先用 3002**。遇 `codes field is required` 时先**原样重试 1~2 次**（很可能下次就过），仍失败再降级 tid=3001（但 3001 是产业链口径，层级会错配，需按 C10 第二道防线标注）。**不要因一次失败就永久放弃 3002。**
20. **耗时下限再度刷新的两极实测（2026-09-26）**：tid=3001 实测 **40 秒**完成；**tid=3002（重体量）实测 25 秒完成**（20:58:19 发布 → 20:58:44 状态转 `public`），刷新全局最快纪录。对比此前同模板 5.5 分钟，**同一模板耗时相差 13 倍**。**耗时分布应表述为 25 秒 ~ 13+ 分钟，抖动 > 30 倍**；轮询切不可因"太快"或"太慢"误判。
    - **轮询节奏建议**：前 60 秒每 10 秒探测一次（重体量也可能秒回），之后每 30 秒；总上限仍取 40 次 / 20 分钟。
21. **乖离率接口只返回 indexCode，不含名称（2026-09-26 实测）**：`get_guaili_rates` 的 `data[].indexCode` 形如 `930697.CSI` / `399986.SZ` / `H11041.CSI`，**无名称字段**。需名称时用以下两步解析（实测 62/62 全命中）：
    - ① 东方财富 `GET https://push2.eastmoney.com/api/qt/clist/get?pn={页}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs={市场}&fields=f12,f13,f14`，其中 `fs` 依次取 `m:1+s:2`、`m:0+t:5`、`m:2`、`m:0+s:2`（**单页 `pz` 超过 100 会被截断，必须分页**）；日线接口 `push2.eastmoney.com/api/qt/stock/get?secid=...` 对多数 CSI 指数会返回 `data:null`，**不要依赖它**。
    - ② 中证指数官网 `POST https://www.csindex.com.cn/csindex-home/index-list/query-index-item`，body 为 `{"sorter":{"sortField":"null","sortOrder":null},"pager":{"pageNum":页,"pageSize":200},"indexFilter":{}}`，需带 `Content-Type: application/json` + `Referer: https://www.csindex.com.cn/`，共约 2997 条（**补全剩余 20 个冷门指数，含 H 开头的 AMAC 系列**）。
    - ⚠️ `www.csindex.com.cn/csindex-home/search/search-index-info` 会返回 **HTTP 400**，不要用。
    - 💡 **解析结果务必落盘复用**：把 `code→name` 映射存成 JSON（如 `glr_final.json` 内的 `all[].code/name`），下次换窗口（maxDays 变化）时直接读缓存反查，**无需重新联网**。本次第二轮东财/中证接口均被网络策略拦截，正是靠上一轮缓存 100% 兜底。**不要每次重抓。**

## 术语

- **深挖点（restPoint）**：账户消费额度，发布报告按模板 cost 扣减。
- **模板（template）**：预设报告框架（公司研究/行业分析/事件复盘等）。
- **背景消息（text）**：辅助资料，≤500 字，作生成上下文（**实际必填**，见实测第 1 条）。
- **报告状态（status）**：inprogress=生成中；public=已发布公开；private=已发布私有；error=出错。

## 实测校正（2026-09-02 验证，与官方 JSON 配置有差异，以本节为准）

1. **knowledge_search 参数必须走 query string**，不要放 JSON body。发 body 会返回
   `400 {"errors":{"keyword":["The keyword field is required."]}}`。
   POST + query string 与纯 GET + query string 均可；`x-www-form-urlencoded` 同样 400。
   返回结构：`{"records":[{"segment":{"id","content","document":{"id","name"}},"score"}]}`，
   `document.name` 形如 `short_2026m08-31#2026-08-31-122101.txt`。
2. **get_guaili_rates 返回结构与配置文档不符**。实际为按指数分组，非 `{date, values}`：
   ```json
   {"data":[{"indexCode":"399986.SZ","datas":[{"date":"2026-09-02","bias_rate_20d":3.11,"bias_rate_pct_120d":95}]}]}
   ```
   - `bias_rate_20d`：20 日乖离率（%）
   - `bias_rate_pct_120d`：该乖离率在近 120 日中的分位数（0~100）
   - 实测覆盖 **62 个行业/指数**；`maxDays=20` 时返回 21 条（含当日）。
3. **report_list 返回字段比文档多**：`del-time`、`off-time`、`orderTime`；`stocks` 字段格式为
   `股票名,代码,情感偏向,市场`，多个用分号分隔（情感值实测见：积极/消极/中性）。
4. 主站 https 在部分网络下 POST 握手会偶发失败（curl exit 35），Python urllib 正常；
   建议直接用 `shenwa_api.py`，它已内置**双站回退与错误分类**（见上文「客户端错误分类与恢复」）。

## 工作流

### 1. 登录
若本地无 browserCode 或请求遇 401，调 `login`（凭据从环境变量或包外凭据文件读取，见上文「凭据配置」），保存 browserCode。

**v1.8.0 起由客户端自动完成**：按**主站 → 备用站**顺序尝试登录（**首次登录也在恢复流程内**）、Token 持久化且**与账号绑定**（切换账号不会沿用旧 Token）、身份失效时**有限重登**（默认 1 次）后重试原请求。**「你没有权限」不重登** —— 它与 Token 过期在 `status=AUT` 下长得一样，客户端按 `msg` 区分。

### 2. 发布报告（report_manage）
1. 调 `templates` 获取模板列表。
2. 确认用户需求 → 转化为（tid + question + text，**三者必填**；question ≤60 字）。无法确定模板时仅列出与需求高度匹配的模板让用户选。
3. **发布前**先调 `user_info` 检查 restPoint 是否充足、expireTime 是否过期；restPoint < 模板 cost 时提示充值。
4. 调 `report_publish`（POST，tid + text + question）→ 返回 aid。**tid 必须传字符串。**
5. **明确告知用户"一般 1~5 分钟，个别模板可能超过 10 分钟，我会稍后查询"**，不要假装实时完成。
6. 调 `report_status`（aid）轮询：**前 2 分钟每 10 秒、之后每 30 秒，上限 40 次（20 分钟）**；status ∈ {public, private, error} 即结束。⚠️ 须区分"调用异常"与报告 `error`，前者不得报失败。
7. 成功后调 `report_detail`（aid）读取报告，**原文完整返回**。
8. 询问用户是否需要下载 PDF；确认后调 `download_pdf`。

### 3. 报告列表翻页（roll_page）
`report_list` 带 num；`hasNext=true` 时取当前列表最小 time 作为 `after` 参数翻页。

### 4. 报告列表展示规范
- 默认返回公共报告列表，并告知为公共列表，询问是否要私有/收藏列表。
- 列表采用：编号、发布时间、模板名、报告标题，每页 20 条。

## 交互规范

- 语气：假设用户为投研/行业分析师，专业、克制，避免口语化。
- 查看全市场/每日报告时，默认返回最新的深挖市场快报。
- 返回报告详情后，询问是否需要下载 PDF。

## 成本与安全

- 消费货币：深挖点，仅 report_publish 扣费（按模板 cost）。
- 充值联系微信：shenwa4119；公众号：真深挖者也。
- 禁止：政治敏感话题、暴力非法话题、基于谣言发布报告。
- 报告由 AI 生成，可能含错误，仅供参考，重大投资决策前请人工复核。

## 升级
调 `latest_version` 对比版本，若高于当前（1.0.6）则**补丁式合并**（切勿整份覆盖）新版 `shenwa_config_<version>.json`，并更新本 SKILL.md 与配置。
⚠️ **接口定义由 `shenwa_api.py` 的 `APIS` 字典维护（唯一权威）**：新接口必须**手工并入 `APIS`**，然后跑 `python shenwa_api.py selfcheck` 核对漂移。`download_version` 取回的配置若**不合法（HTML / 错误 JSON / 缺字段 / 版本不符）不得当作可用更新**。
⚠️ **官方升版覆盖后，务必先跑一遍下面的「本地补丁自查」** —— 官方版本很可能把 `inviteCode` 又标回必填，导致使用者被反复索要邀请码。

## 本地补丁自查（升级后 / 分发前必跑）

```bash
# ① 脚本层：不应再要求 inviteCode（正确输出应为 2 个 "只需 userID" 的匹配，且无 "if uid and code"）
grep -n "if uid:\|if u:\|if uid and code\|if u and c" ~/.workbuddy/skills/shenwa/shenwa_api.py

# ② 配置层：不应再有 "请输入邀请码"；login 的 inviteCode 应为 required:false
grep -n "请输入邀请码\|inviteCode" ~/.workbuddy/skills/shenwa/shenwa_config_1.0.6.json

# ③ 文档层：不应再有「必须填邀请码」的表述（本文档仅在说明"已废弃"时会提到该词）
grep -n "邀请码" ~/.workbuddy/skills/shenwa/SKILL.md
# 逐条目视：每条都应是「已废弃 / 已不再需要 / 不要索要」语义，不得出现「必填 / 缺一不可」

# ④ 接口层：get_right_side 路径应为 /api/market/getRightSide（无双 api），且注册表共 15 个接口
grep -n "get_right_side\|/api/api/market" ~/.workbuddy/skills/shenwa/shenwa_api.py
```

**功能验证（最可靠，不依赖静态检查）**：用一个**只含 `userID`、不含 `inviteCode`** 的临时凭据文件跑一次登录 —— 能返回 `success:true` 即补丁生效。

```bash
echo '{"userID":"你的注册手机号"}' > /tmp/cred_test.json
SHENWA_CRED_FILE=/tmp/cred_test.json python ~/.workbuddy/skills/shenwa/shenwa_api.py user_info
```

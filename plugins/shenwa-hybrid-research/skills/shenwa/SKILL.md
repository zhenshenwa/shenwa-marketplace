---
name: shenwa
description: 深挖（Shenwa）AI 深度调查报告工具。用于对某公司/行业/事件做深度研究调查、持续跟踪行业或个股、阅读全市场报告、生成结构化投研报告。触发词：深挖、调研、调查报告、行业分析、公司研究、财报分析、政策解读、市场复盘、卖方日报、乖离率。
description_zh: "深挖 AI 深度调查报告工具"
version: 1.0.4
---

# 深挖（Shenwa）AI 深度调查报告工具

一个基于 API 的 AI 深度调查报告工具。通过登录换取 Token，调用接口生成/查询/管理投研报告、获取乖离率、检索内置知识库。

## 重要规则（长期生效，优先级最高）

1. **只通过 API 访问**，任何情况下都不得使用浏览器抓取或访问深挖。
2. **报告必须原文完整呈现**，严禁精简、摘要或省略任何章节，即使报告很长也要全文返回。
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
- Token 有效期：直到返回 401；遇 401（`success=false, status=AUT`）须重新登录刷新 Token。
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
> - `shenwa_config_1.0.4.json`：`auth.user_credentials` 已删除 `inviteCode` 项；`login` 端点中该参数已标 `required: false`。
> - 包内 `README.md`：已去掉"手机号 + 邀请码，二者缺一不可"的旧说法。

> ⚠️ **本地补丁提示**：以上是对**官方 `shenwa_config_1.0.4.json`** 的本地修正 —— 官方截至 **2026-09-29** 仍把 `login` 的 `inviteCode` 标为必填（`GET /api/manage/getLatestVersion` 返回 `latestVersion=1.0.4`，与本地同版），与实际接口行为不符。**若日后官方升版并按新版覆盖 `SKILL.md` / `shenwa_api.py` / 配置文件，必须重新应用本补丁**，否则"反复索要邀请码"会复发。自查命令见本文末「本地补丁自查」。

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
本 skill 目录下已提供命令行封装 `shenwa_api.py`，自动完成：主站/备用站回退、Token 持久化（`.shenwa_token`）、401 自动重登、参数类型转换。

```bash
PY="C:/Users/songj/.workbuddy/binaries/python/versions/3.13.12/python.exe"
cd ~/.workbuddy/skills/shenwa
$PY shenwa_api.py list                       # 查看全部接口
$PY shenwa_api.py user_info
$PY shenwa_api.py templates
$PY shenwa_api.py report_list type=public num=20
$PY shenwa_api.py report_detail aid=xxx
$PY shenwa_api.py report_status aid=xxx
$PY shenwa_api.py download_pdf aid=xxx out=/abs/path.pdf
$PY shenwa_api.py get_guaili_rates maxDays=20
$PY shenwa_api.py knowledge_search keyword="光模块" knowledgeBase=short7d searchMethod=hybrid rerankEnable=true topK=5
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
| knowledge_search | POST/GET | /api/data/knowledgeSearch | VIP2 | 知识库检索（keyword 等，**参数走 query string**） |
| latest_version | GET | /api/manage/getLatestVersion | 全部 | 查看最新版本 |

- 统一响应：`success`（bool）、`status`（SUC/AUT/SRV）、`data`、`msg`、`time`。
- `status=AUT` 表示未登录或 Token 过期，须重新登录。
- **所有 POST 接口（login/report_publish/collect_report/knowledge_search）都需 `Content-Type: application/json` + JSON body**；GET 接口用 query 参数即可。

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
   建议直接用 `shenwa_api.py`，它已内置双站回退。

## 工作流

### 1. 登录
若本地无 browserCode 或请求遇 401，调 `login`（凭据从环境变量或包外凭据文件读取，见上文「凭据配置」），保存 browserCode。

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
调 `latest_version` 对比版本，若高于当前（1.0.4）则下载新 `shenwa_config_<version>.json` 覆盖更新本 SKILL.md 与配置。
⚠️ **官方升版覆盖后，务必先跑一遍下面的「本地补丁自查」** —— 官方版本很可能把 `inviteCode` 又标回必填，导致使用者被反复索要邀请码。

## 本地补丁自查（升级后 / 分发前必跑）

```bash
# ① 脚本层：不应再要求 inviteCode（正确输出应为 2 个 "只需 userID" 的匹配，且无 "if uid and code"）
grep -n "if uid:\|if u:\|if uid and code\|if u and c" ~/.workbuddy/skills/shenwa/shenwa_api.py

# ② 配置层：不应再有 "请输入邀请码"；login 的 inviteCode 应为 required:false
grep -n "请输入邀请码\|inviteCode" ~/.workbuddy/skills/shenwa/shenwa_config_1.0.4.json

# ③ 文档层：不应再有「必须填邀请码」的表述（本文档仅在说明"已废弃"时会提到该词）
grep -n "邀请码" ~/.workbuddy/skills/shenwa/SKILL.md
# 逐条目视：每条都应是「已废弃 / 已不再需要 / 不要索要」语义，不得出现「必填 / 缺一不可」
```

**功能验证（最可靠，不依赖静态检查）**：用一个**只含 `userID`、不含 `inviteCode`** 的临时凭据文件跑一次登录 —— 能返回 `success:true` 即补丁生效。

```bash
echo '{"userID":"你的注册手机号"}' > /tmp/cred_test.json
SHENWA_CRED_FILE=/tmp/cred_test.json python ~/.workbuddy/skills/shenwa/shenwa_api.py user_info
```

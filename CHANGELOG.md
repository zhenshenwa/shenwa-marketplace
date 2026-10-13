# CHANGELOG

## 2026-10-13 — 轮 17（待定版号）：任务身份与报告绑定 / 旧版本任务兼容 / 补充授权后恢复

> 用户口径「第 6 轮补丁（重新做一次）」：按文字描述实施三组修复。**版本号未升**（仍 `1.15.1 / 1.1.7`），
> 本轮为脚本层修复，**未做客户端实跑验证**。

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.15.1`（不变） | ① 业务身份与展示序号分离：`entry_id = <日期>-<business_fp>-<NN>`，`business_fp` 由对象/模板/问题内容/执行参数决定、**与清单顺序无关**；关键参数变更（`plan_hash` 变）**不继承旧结果**。② 旧版本任务兼容：旧记录回算 `business_fp_resolved`、`_match_old_entry` 4 级匹配、`plan_id` 未命中时**降级按业务身份匹配全部快照**、无法可靠匹配进「待核对」不自动重发；旧记录/旧 aid/旧占用一律保留。③ 补充授权后恢复：`pending_authorization` 按 `reserve_state` 分叉 —— `none`（从未预留=从未发送）**不占额度、授权后可执行**；其余 `mark_unknown` 保留占用。`selftest` 92→**118 项**。 |
| shenwa-agent | `1.1.7`（不变） | 同步 `shenwa_api.py` 兼容层：`_dedup_key` / `Client.call` / `_find_existing` 增 `business_fp` 通道；新增 `_find_existing_compat()` **保留复核方旧 4 参签名夹具**。`selftest` 保持 **56 项**。 |

**变更**：

- `skills/shenwa-hybrid/scripts/publish_plan.py`（`selftest` 92→**118 项**）——
  - 新增 `_entry_fingerprint()`：业务身份指纹（`object_id` / `object_level` / `template_tid` / `question_ids`(sorted) / `questions`(sorted) / `research_cutoff`）。
  - `make_entry_id(..., *, business_fp=None)`：编号改为三段 `日期-业务身份指纹-NN`；未传时保持旧两段格式仅供读旧记录。
  - 新增 `_legacy_entry_alias()`：兼容轮12 `<日期>-<NN>` 与轮15 `<日期>-<plan指纹>-<NN>`。
  - `plan_fingerprint()`：`question_ids` / `input_materials` 改 **sorted** 入指纹 → 仅调整顺序不再误判「关键参数已变」。
  - `build()`：条目新增 `business_fp` / `seq`；**条目顺序保持输入顺序**（不重排序 —— 防串位由身份绑定保证，不由排序保证）。
  - `_restore_into_plan()`：`plan_hash` 不等等于关键参数已变 → 状态归 `pending`、aid 清空、记 `params_changed`；`pending_authorization` 按 `reserve_state` 分叉。
  - `_match_old_entry()`：`business_fp` → `entry_id` → 旧别名 → 旧记录回算身份指纹，四级匹配。
  - `restore_pending()`：旧记录回算 `business_fp_resolved`；新增 `executable_after_auth` / `held_authorization` / `needs_review`。
  - `publish()`：`client.call(..., task_id=entry_id, business_fp=...)`，带 `except TypeError` 回退旧签名。
- `skills/shenwa/shenwa_api.py`（hybrid 与 agent 两链同步）——
  - `_dedup_key(task_id, fingerprint, business_fp=None)`：新增 `business_fp` 通道。
  - `Client.call(..., business_fp=None)`；`_find_existing` 候选集增 `business_fp` 命中。
  - 新增 `Client._find_existing_compat()`：先按新签名调用，捕获**参数不匹配型 `TypeError`** 后退回旧 4 参签名 —— **保留复核方夹具原件不改**。
- `skills/shenwa/SKILL.md`（hybrid 包）：`transition_type` 取值由**四种**更正为**七种**（`Hold` / `Upgrade` / `Breakdown` / `Downgrade` / `Recovery` / `Upgrade_Jump` / `Unknown`，2026-10-10 实测 434 行），并加「`Downgrade` / `Recovery` / `Upgrade_Jump` 暂不入表，**不得**折算进升级/破坏数」的边界注记。

**验收**（离线替身 + 合成材料，**不产生任何真实付费发布**）：`publish_plan.py selftest` **118**、`shenwa_api.py selftest` 83（hybrid）/ 56（agent）、`reuse.py` 27、`capability.py` 101、`router.py` 131、`monitor.py` 40、`test_round15.py` 21 项 / 13 场景、12 项边界反例 **12/12 PASS**、agent 5 项 **5/5 PASS**、`verify.py --backup` **全部通过（exit 0，清单 339 项缺失 0）**。**未删除 / 未跳过 / 未放宽任何断言。**

**未验证**：客户端实跑未做；复核方 6 项检查因附件从未送达**无法执行**（该验收通道缺失）。**未改**深挖 AI 服务端与接口路径。

**纠正记录**：`build()` 曾一度按身份重排序 `order`，破坏轮12 `C19`（逐项加总按位取 `items.0/1`）与轮13 `场景K`（`flow.0/1` 复用判定）等**依赖输入顺序**的既有断言共 7 项；已**恢复输入顺序**，防串位改由 `business_fp` 绑定承担，并改写 `T12b` 断言为「身份段稳定 + 展示序号随输入」。


## 2026-10-04 — v1.15.1 / v1.1.7（第 6 轮补丁：独立复核 12 项边界反例修复）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.15.0 → 1.15.1` | 独立复核 12 项边界反例全部修复：① dry-run 不再创建意图文件；② 1.14 旧格式 `submit_unknown` 按指纹兜底阻断；③ API 台账损坏即阻断；④ 恢复 `read_error` 即中止（ `run_plan_flow` / `run_reuse_flow` ）；⑤ `submitting` 纳入 `LOCKED_STATES` + `pending_authorization` 二次恢复保留占用；⑥ 发送前持久化 `submitting`+`reserved`；⑦ 结果落盘失败即停止后续；⑧ `_plan_fingerprint` 纳入问题内容与执行参数；⑨ 成功回填失败不释放锁 + `sent` 纳入阻断；⑩ 取得意图锁后复检消除 check-then-lock 竞态。`selftest` 无回归。 |
| shenwa-agent | `1.1.6 → 1.1.7` | 同步 `shenwa_api.py` 上述修复中 API 层可复用的部分（dry-run / 旧记录兜底 / 台账损坏阻断 / 回填失败不释放锁 / 取得锁后复检），保持纯深挖版 56 项自检。 |

## 2026-10-04 — v1.15.0（第 6 轮：发布防重、状态保存与恢复、任务编号唯一性）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.14.0 → 1.15.0` | **发布防重**：跨进程原子意图文件互斥，已受理 / 结果未知**阻断不重发**、已确认返回已有 aid；**任务身份与参数指纹分离**；**状态可靠保存与恢复**（写失败中止、损坏行显式报告、恢复回填预算占用、aid 只代表受理）；**任务编号唯一性**（plan_id / entry_id 含内容指纹，独立计划不冲突、兼容旧记录） |
| shenwa-agent | `1.1.5 → 1.1.6` | 同步 `shenwa_api.py` 防重修复（`_dedup_key` / 意图文件互斥 / `_find_existing` 严格匹配 / `append_task` 写失败显式报告 / `load_tasks` 损坏行显式报告 / `_release_guard` / `_submit_unknown` 保留意图文件），保持纯深挖版 56 项自检 |

**背景（要修的病根）**：① 提交超时后**重复调用仍会再次发布**——没有跨进程防重，「不确定就重试」会制造重复报告、重复扣点；② 两条任务第一条受理、第二条中断，恢复后**第一条又变回 pending**——状态没在提交前可靠落盘、也没按 plan_id 精确回填；③ 状态文件写失败**仍继续发布**——`save_state` 失败被静默吞掉；④ 同日两个独立计划 `plan_id` / `entry_id` **会撞号**——编号只由「日期 + 当天局部序号」构成，不区分计划内容。

**变更**：

- `skills/shenwa/shenwa_api.py`（**扩展**，`selftest` 79→**83 项**，新增 P23~P26）——
  - 跨进程防重：`Client.call()` 有副作用分支改为「**先查 `_find_existing`**（已受理 / 结果未知 / 已发送 → 阻断；已确认 / 已完成 → 返回已有 aid）→ **原子意图文件**（`os.open(O_CREAT|O_EXCL)`）→ `append_task` 失败中止发布」；两进程并发**至多 1 次发布**。
  - 任务身份 vs 参数指纹：`_dedup_key(task_id, fingerprint)` 有 `task_id` 优先用 task_id、否则用 `fp:<指纹>`；`_find_existing` **严格按 dedup key 匹配**（去掉指纹回退，避免「参数相同但 task_id 不同」的独立任务被永久禁止）。
  - 状态可靠：`append_task` 返回 `{ok,error,record}`（写失败**显式报告**）；`load_tasks` 返回 `(rows, errors)`（损坏行**不静默跳过**）；`_submit_unknown` / `reconcile` 传 task_id / dedup，成功确认与明确失败释放意图文件、submit_unknown **保留意图文件**。
- `skills/shenwa-hybrid/scripts/publish_plan.py`（**扩展**，`selftest` 92→**93 项**）——
  - 编号唯一：`_plan_fingerprint()` + `make_entry_id(plan_date, n, plan_suffix)`，`plan_id = PLAN-<日期>-<指纹>`、`entry_id = <日期>-<指纹>-<NN>`；`build(plan_token=...)` 支持 内容相同但用户明确独立的计划加盐；`restore_pending` **按 plan_id 精确匹配**（不回退）。
  - 状态可靠：`save_state` 返回 `{ok,error,path}`（原子写 + fsync）；`load_state` 返回 `(snaps, errors)`；`run_plan_flow` / `run_reuse_flow` 提交前 save_state 失败**中止发布**、每条结果后**及时 save_state**；`_restore_into_plan` 回填 accepted / submit_unknown / submitting 的 aid + 预算占用；`publish()` 中 `state='accepted'`（aid 只代表**受理**）。
  - 兼容旧记录：`returned_evidence_by_entry` 键按 `-NN` 后缀**唯一命中时回退匹配**（旧夹具 / 旧记录）。
- `skills/shenwa-hybrid/scripts/capability.py`：新增 `matches` 断言算子（正则整串匹配），供用例对含指纹的 entry_id 做格式断言。
- **测试**：`maintenance/expert-package-review/cases/15-发布防重与状态恢复/`（`reproduce.py` 4 反例、`test_round15.py` **12 场景 / 18 项断言**，含 S03 跨进程并发子进程）；`verify.py` 轮 12 / 13 断言改为动态 entry_id 格式断言；全量 `verify.py` 除「待同步镜像 / 缺备份路径」外 **0 失败**。

**验收**（离线替身 + 合成材料，**不产生任何真实付费发布**）：`shenwa_api.py selftest` 83 项、`publish_plan.py selftest` 93 项、`capability.py selftest` 101 项、`agent shenwa_api.py selftest` 56 项、`test_round15.py` 18 项，**全过**。

**未改**深挖 AI 服务端、接口路径与接口注册表（仍 **15 个**接口）、服务端模板与算法、第 1~5 轮既有实现。**明确未具备（留给后续）**：监测 / 历史基线 / 报告复用问题留待后续轮次；跨进程预算锁仍为**进程内原子**。


## 2026-10-04 — v1.14.0（第 5 轮：监测记录与跨期比较）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.13.0 → 1.14.0` | 新增**监测记录与跨期比较**：持续跟踪 / 与上次或指定日期比较 / 检验更新此前判断 / 已建立监测序列的任务进入监测流程，**每次研究的事实与判断落成可追溯记录**，**原始快照不覆盖、修订另存、重复归档不制造新观测**；**基线指定优先、否则取最近适用**（不按文件时间）；**先查可比性再算变化**（缺失不补 0、基数 0 不硬算、状态按迁移比较、区分数值差/百分比/百分点）；归因**六分类**（数据修订/规则调整不写成对象变化、允许无法判定）；**四问综合比较 + 两点不称持续趋势**；**比较在成稿前完成**；**资料足够不新增发布**、未接入真实调度 |
| shenwa-agent | 1.1.5（不变） | 本轮未改动（`skills/shenwa/` 与接口配置均未动） |

**背景（要修的病根）**：历次研究**做完就丢**——每次判断的事实基础、依据、假设、数据缺口都没有留下，
下次要「跟上月比」「检验之前的判断还成不成立」时，**没有可追溯的历史**，只能靠记忆或重新全查；
即使留了文件，也**按文件修改时间 / 下载顺序 / 返回顺序**乱选基线；比较时**口径没对过就比**，
把单位换算、统计期间差异、历史数据修订、规则调整**统统写成「研究对象变了」；两个时点连成一条线
就敢说「趋势」；比较结果**只在交付后存个没人看的文件**，没进成稿。

**变更**：

- `skills/shenwa-hybrid/scripts/monitor.py`（**新增**，`selftest` **40 项**）—— 本包**唯一的监测记录与跨期比较实现**：
  - `is_monitor_request()`：四类进入监测（track / compare / verify / series），普通一次性查询**沿用现有交付**。
  - `monitor_series_id()`：**稳定序列编号**（对象 + 范围指纹），**不凭对象简称 / 文件名 / 任务编号匹配历史**。
  - `make_record()`：记录**三分**（原始 / 修订 / 事后重建）及**12 项字段**；`save_record()` **原始不覆盖**、
    **同一数据时点重复归档拒绝**（不制造新观测）；`load_records()` **解析失败标 `load_error`（≠ 无历史）**。
  - `select_baseline()`：**指定优先 > 最近适用**，只按记录内 `data_asof`，**不按文件时间 / 下载 / 返回顺序**。
  - `check_comparability()`：**七维可比性**（对象 / 问题 / 单位 / 期间 / 样本 / 口径 / 规则），
    可统一则**重算另存**、不可统一则**标不可比**、部分不可比**不妨碍其余**。
  - `measure_change()`：**三量纲**（数值差 / 百分比 / 百分点），**缺失不补 0**、**基数 0 不硬算**。
  - `state_transition()` / `attribute_change()`：状态**按迁移比较**；归因**六分类**
    （object_change / data_revision / calibre_change / rule_change / mixed / undetermined）。
  - `compare()` / `build_answer()`：**四问**（哪些变了 / 延续性 / 判断支持度 / 归因），
    **两点不称持续趋势**，**比较在成稿前完成**。
  - `run_monitor_flow()`：**第 5 轮完整流程**（识别 → 读基线 → 资料 → 校验 → 可比性 → 比较 → 成稿 → 保存），
    需要新报告时继续走 `publish_plan.run_reuse_flow()` 真实入口；**资料足够 → 不新增发布**；
    **未接入真实调度**（`automation="manual_only"`）。
- **规则同步**：`router.py` 硬规则**九条 → 十条**（第 10 条「监测要留档、比较要在成稿前」）；
  hybrid `SKILL.md` 新增红线 **26**；`references/02`、`references/05` 硬规则表同步补第 10 行；
  **6 张任务卡**统一新增「**监测与跨期比较（v1.14.0）**」小节。

**验收**（离线替身 + 合成材料，**不产生任何真实付费发布**）：
`monitor.py selftest` **40 项断言**全过（S1~S11）；`router.selftest()` 131 项（十条硬规则）；
`maintenance/expert-package-review/cases/14-监测与跨期比较/`（**11 场景 / 25 项断言**）→ 0 失败；
`verify.py` 新增 `轮14-` 分组（实现 / 口径 / 自检 / 场景 / 三时点 / 触发词 / 条款 / 任务卡 /
五轮联合回归，共 **17 项断言**）；**五轮联合回归**：cases/11（28 项）、cases/12（108 项）、
cases/13 复用（73 项）+ 证据（15 项）、cases/05（24 项）**0 失败**。

**未改**深挖 AI 服务端、接口路径与接口注册表（仍 **15 个**接口）、服务端模板与算法、
第 1~4 轮参数校验 / 预算 / 来源关系 / 内容复用实现。**明确未具备（留给后续）**：
监测**未接入真实调度**（手动触发）；跨进程预算锁仍为**进程内原子**；来源关系**自动发现**未实现。

## 2026-10-03 — v1.13.0（第 4 轮：背景事实注入、来源关系与报告内容复用）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.12.0 → 1.13.0` | 深挖不再「裸问」：按问题组装**必要背景**（经核实数字带来源·时点·单位·口径，未核实材料标状态，用户观点写**待检验命题**），并**记录实际发送的参数**；建立**材料 / 来源登记**与**来源关系三态**（同源 / 独立 / 未知）——**复述已传入背景的数字不算新增独立证据**，**「两路独立验证一致」须先过准入**；历史报告按**内容六查**判**四档**（完整复用 / 部分复用 / 仅作历史参考 / 不适用），并接入同一条真实流程 |
| shenwa-agent | 1.1.5（不变） | 本轮未改动（`skills/shenwa/` 与接口配置均未动） |

**背景（要修的病根）**：① 发报告时**只告诉深挖一个标的和时点**，已有的材料、证据、用户观点
**一条都没带过去**，深挖只能从零重来，答复与手上材料脱节；② 报告回来以后，
**同一份信息被重复算成多路证据** —— 深挖复述我们刚发给它的数字、或两个接口其实读同一份底层材料，
都会被当成「两路独立验证一致」；③ 历史报告**只看列表 / 标题 / 模板 / 日期**就决定复用，
于是出现「同一天、同一个对象、同一个模板，但研究问题不同」的报告被直接拿来当当前结论；
④ 复用与发布、预算、交付**各做各的**，复用只体现为「少发一次」，被复用问题的证据与回答是空的。

**变更**：

- `skills/shenwa-hybrid/scripts/evidence.py`（**扩展**）—— 本包**唯一的材料 / 来源登记与独立性判定实现**：
  - `make_material()`：材料 / 来源记录 **15 个字段 / 14 项追踪要求**（对象与问题编号 / 材料编号 /
    来源名称与可核对位置 / 材料所属时间·公开或可用时间·获取时间 / 指标含义·单位·统计口径 /
    核实状态 / 性质 / 与其他材料的关系）；**时间未知显式标 `unknown`**（不冒充满）；
    核实状态**沿用第 3 轮五态**（不新增第六态）；材料性质**沿用既有陈述分类**
    （`primary_fact` / `computed` / `external_judgement` / `user_hypothesis`）。
  - `SourceGraph`：**来源关系六类**（引用 / 转述 / 底层同源 / 派生 / 独立 / 未知）与
    **独立性三态**（`independent` / `same_source` / `unknown`）—— 派生链经并查集**传递**；
    **未登记关系一律 `unknown`**（**不默认独立，也不武断认定同源**）；
    `per_evidence_independence()` **按具体证据**给出关系，**不因一处同源把整份报告其他证据全部归为同源**。
  - `echo_check()`：报告**复述已传入背景的数字** → `counts_as_new_evidence=False`（**不算新增独立证据**）。
  - `can_claim_two_way_independent()`：仅当**独立性已确认**且**两者都不是背景复述**时才允许写
    「两路独立验证一致」；`check_independence_claims()` 作为**交付前自检**逐条报出问题。
  - **来源关系是补充信息** —— 它回答「这算不算独立证据」，**不替代**五态证据校验。
- `skills/shenwa-hybrid/scripts/reuse.py`（**新增**，`selftest` **27 项**）—— 本包**唯一的报告内容复用判定实现**：
  - `question_equivalence()`：**语义等价**（对象 / 层级 / 指标族 / 提问种类），**不机械比对文字**。
  - `stale_impact()`：跨日**分开处理** —— 稳定结构性信息**可继续使用**、时效性数据**必须重新检查**；
    旧结论**依赖已经变化的数据** → `conclusion_must_be_redone`（**不得只替换数字、保留旧结论**）。
  - `check_candidate()`：**六项内容核查**（对象与层级 / 是否真正回答当前问题 / 研究截止时间 /
    关键假设·情景·输入材料是否仍成立 / 是否完成且可读取 / 关键证据及校验状态）。
  - `_derive()`：四档（`full_reuse` / `partial_reuse` / `reference_only` / `not_applicable`）
    **唯一定义处** —— 四档互不冒充，**未知不写成通过**；
    **同日同对象同模板但问题不同 → 不得直接复用**；用户**明确要求新时点 / 新情景 / 重做** → `not_applicable`。
  - `apply_reuse()` / `recompute_plan_cost()`：复用结果**关联问题编号**，
    **完整复用的内容进入该问题的证据与回答（不只是取消发布）**，部分复用**保留未解决的问题**并收窄条目；
    调整计划后**重新计算预计新增消耗**并核对已有授权；
    **已提交 / 结果未知条目的状态与预算占用一律保留**，**不得因复用清除**。
- `skills/shenwa-hybrid/scripts/publish_plan.py`（**扩展**）—— 背景组装接入唯一真实发布入口：
  - `background_item()` / `render_background_item()` / `background_from_materials()` /
    `assemble_background()`：五类背景（经核实数字 / 未核实材料 / 用户待检验命题 / 既有判断 / 预设结论），
    **渲染时一律显式标注状态与来源·时点·单位·口径**；
    **独立判断**模式下既有判断与预设结论**一律不注入**；**解释 / 验证**模式下可提供并**注明来源·时间·用途**。
  - **只选关键背景**，完整材料留本地；**不假设深挖能读本地路径 / 文件名 / 材料编号** ——
    实际发送的文字**必须能独立理解**；压缩背景**不得改变**数字 / 单位 / 时间 / 条件 / 核实状态，
    **不得机械截断**；超限时先丢**显式标可选**项并如实记录，仍超限 → `ok=False` + `requires_adjustment`
    （**不产出被截断的 text**，须**调整问题条目或执行方式并重新检查覆盖与预算**，
    **不得静默删除关键约束后继续发布**，`gate()` 以 `confirm_required` 拦下）。
  - `publish()`：**保存实际发送的参数与文字**（`sent_params` / `sent_text` / `sent_text_length` /
    `text_check`）—— **验收查的是这里，不是组装函数的输出**；未提交 → 无实际发送内容。
  - `recheck_after_reuse()`：计划因复用调整后**按剩余问题重新生成参数并重算 `plan_hash`**（＝重新检查），
    已锁定 / 已复用条目**跳过且不清状态**。
  - `run_reuse_flow()`：**第 4 轮完整流程**（问题清单与材料 → 来源登记及核对 → 历史报告**内容**检查
    → 确定复用部分与研究缺口 → 为必要的新报告组装背景 → 参数与发布检查 → 执行及结果回填
    → **证据处理** → 逐题交付），发布端为离线替身 `RecordingClient`。
  - **证据处理真的接通**：`returned_evidence_by_entry` / 替身 `evidence_map` 按 `entry_id` 提供报告返回的证据；
    返回证据的**规范编号统一为 `material_id`（退回 `id`）**，与来源关系图同键 ——
    否则「把复述 / 同源当两路独立验证」的拦截会因**键不一致而静默失效**（本轮实测修掉）。
    **没有回填就没有结论**：未提供返回证据时该步产出为空，**不得据此宣称「已确认没有复述 / 没有同源」**。
- **规则同步**：`references/05-输出规范与校验表.md` 新增「**一·补八 · 背景组装、来源关系与内容复用
  （v1.13.0 新增）**」，并把复用检查由「三态」改写为「**查询三态 + 内容判定四档**」（附六项核查表）；
  `references/01-模式总纲.md` 新增规则 **29**（来源关系）；hybrid `SKILL.md` 新增红线 **24**、
  **硬规则由八条扩为九条**（第 9 条）；`agents/shenwa-hybrid-research.md` 新增红线 **20**；
  **6 张任务卡**统一新增「**背景与来源（v1.13.0 新增）**」小节；`scripts/router.py` 规则清单同步补第 9 条。

**验收**（离线替身 + 合成材料，**不产生任何真实付费发布**）：
`publish_plan.py selftest` **92 项断言**全过（新增 T16~T24，其中 **T24** 专防「`id`≠`material_id`
时复述拦截静默失效」）；`reuse.py selftest` **27 项**全过；`evidence.py selftest` 全过；
`maintenance/expert-package-review/cases/13-背景与复用/`（**50 组 / 250 项断言**）→ 0 失败：
`input-checks.json` **162 项**（计划侧 20 组）、`input-evidence.json` **15 项**（证据侧 15 组）、
`input-reuse.json` **73 项**（复用侧 15 组），覆盖 **11 个场景**
（A 背景组装 / B 独立判断不注入预设结论 / C 超限不静默截断 / D 实际发送参数被记录 /
E 材料登记与未知项显式标记 / F 来源关系三态且按具体证据判定 / G 复述不算新增独立证据 /
H「两路独立验证一致」准入 / I 内容四档 / J 跨日与用户新要求 / K 混合任务贯穿实际流程）；
`verify.py` 新增 `轮13-` 分组（实现 / 口径 / 自检 / A~K 场景 / G′ / G″ / 旧措辞 / 条款 /
任务卡 / 三份清单 / 回归，共 **29 项断言**）；
**前三轮回归**：`cases/05-证据采信与交付范围`（24 项）与 `cases/12-发布计划与预算`（108 项）**0 失败**。

**未改**深挖 AI 服务端、接口路径与接口注册表（仍 **15 个**接口）、服务端模板与算法、
第 1 轮参数校验、第 3 轮预算与 `aid` 绑定、第 6 轮错误分类与副作用保护。

**明确未具备（留给后续轮次）**：本轮**未**实施第 5 轮**监测快照与跨期比较**；
来源关系的**自动发现**未实现（关系须由调用方显式登记，未登记一律 `unknown`）；
跨进程预算锁仍为**进程内原子**（`threading.RLock`）。

## 2026-10-03 — v1.12.0（第 3 轮：发布计划、预算检查与任务关联）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.11.0 → 1.12.0` | 报告发布从「SOP 里随手调一次 `report_publish`」改为**经唯一真实入口**：每条发布都有条目编号与任务/问题对应，提交前过**五项检查**，预算按**已结算 / 预留占用 / 拟新增**三分并**原子预留**，`aid` **绑定原条目**，结果未知**保留占用且不重发** |
| shenwa-agent | 1.1.5（不变） | 本轮未改动（`skills/shenwa/` 与接口配置均未动） |

**背景（要修的病根）**：报告发布此前没有任何条目编号、必要性检查、预算概念，
`aid` 按**返回顺序**认领，于是出现五类问题：① 已有材料足够的问题照样发一份报告（凑数）；
② 用户要研究 8 家，悄悄缩成 3 份；③ 两份并发提交各自「检查通过」后**合计超额**；
④ 提交超时后**换站重发**；⑤ `aid` 乱序返回时**串到别的对象 / 问题**。

**变更**：

- `skills/shenwa-hybrid/scripts/publish_plan.py`（**新增**，83 KB / 1658 行，`selftest` **52 项**）
  —— 本包**唯一的报告发布真实入口**，只做「查 → 决定 → 透传调用」，**不重写网络层**
  （网络处理一行不写，全部交既有 `Client.call()`，第 1 轮参数校验 / 第 6 轮错误分类 /
  `MUTATING_APIS` 副作用保护 / `submit_unknown` 防重发照旧生效）：
  - `build()`：问题清单 → **发布计划**（复用第 2 轮的问题编号 / 对象 / 研究时点 / 材料引用），
    每条含 **提交前生成**的条目编号 `entry_id`（`<YYYYMMDD>-<NN>`）与至少 9 类字段
    （所属任务与问题编号 / 研究对象·模板·用途 / 为什么需要新报告及缺什么 / 研究截止时间·输入材料·拟提交参数 /
    历史报告候选与复用检查状态 / 预计成本与依据 / 执行状态·`aid`·结果引用）；
    **一份报告可支持多个问题，一个问题也可需要多份报告**；直取 / 计算 / 已有资料足够的问题**不建条目**。
  - `gate()`：**五项检查**，返回**互斥**判定 `no_publish` / `reuse_all` / `two_stage` /
    `confirm_required` / `proceed` —— ① 必要性 ② 历史报告检查状态 ③ 计划是否完整覆盖用户要求
    ④ 授权与预算（**只读预检**）⑤ `plan_hash` 参数一致性重检；**只有 `proceed` 才进入 `client.call`**，
    其余分支提前返回，**发布调用次数恒为 0**。
  - `BudgetLedger`：**三态预算**（`_settled` / `_reserved` / `_unknown`），
    `reserve()` 在 **`threading.RLock`** 内同一临界区完成「读总额 → 判断 → 写入」——
    **「检查剩余额度 + 预留本次成本」不可被其他并发任务插入**；`settle()` 只认结算依据，
    `release()` 只认「确认未产生消耗」，`mark_unknown()` **保留占用**；
    `snapshot()` 增列 `unknown_entries`（结算状态未知的条目**可追踪**）。
  - `reuse_decision()`：**三态** `no_candidate` / `candidate_unverified` / `unknown`，
    恒带 `content_check="not_implemented_round4"`、`reuse_all_reachable=False`
    —— **内容有效性判定明确留第 4 轮，本轮不宣称已完成**。
  - `save_state()` / `load_state()` / `restore_pending()`：**复用**需求侧既有轻量 JSONL 机制
    （不另建平台）；恢复时**先读已有状态**，`accepted` / `submit_unknown` **不得当作新任务再次提交**。
  - `run_plan_flow()`：贯穿真实专家包流程的完整记录（问题清单 → 发布计划 → 最终检查 → 预算预留 →
    模拟提交 → `aid` 回填 → 状态更新 → 交付覆盖检查），发布端为离线替身 `RecordingClient`。
- **预算口径**：预计成本**逐条**从 `templates` 接口 `data[].templates[].cost` 读取并加总
  （**不写死每份 5 点**）；**成本取不到 → 标 `unknown`，绝不按 0 处理**；
  **账户余额 ≠ 用户允许本次花费的额度**（余额只作展示）；**数量提醒阈值不自动缩减研究范围**，
  也不替代预算检查；**已结算不再计入预留**；模板估价只用于**估算**，
  **只有获得扣点依据才标「实际消耗」**（否则 `cost_actual.state = "unknown"`）。
- **状态与 `aid`**：`pending` / `pending_authorization` / `submitting` / `accepted` / `done` /
  `failed` / `submit_unknown`，预留状态 `none` / `reserved` / `settled` / `released` / `unknown`；
  **`aid` 只代表服务端受理**（`accepted`，**不是** `done`，**更不等于问题已回答**）；
  超时 / 连接中断**沿用 `submit_unknown` 保护**、**不自动换线路重发**；
  **服务端生成失败也不等于未扣点**（分类 `business` → 占用**保留**并标 unknown）。
- **规则同步**：模式红线 **20 / 21 / 22**（hybrid `SKILL.md`）、模式约束 **25 / 26 / 27**
  （`references/01-模式总纲.md`）、Agent 红线 **17 / 18**、**硬规则由七条扩为八条**
  （第 8 条：每次发布都要有明确用途与任务对应）；**路由输出物新增「发布计划」栏**（`02`）；
  **SOP ④ 深挖侧改版**并新增「发布计划」小节（`04`）；新增「**一·补七 · 发布计划与预算**」
  与 4 条交付前自检 + 实现入口（`05`）；**6 张任务卡**统一新增「发布计划」栏（12 列）。
- **旧调用路径同步接入**：`skills/shenwa-hybrid/SKILL.md` ④、`agents/shenwa-hybrid-research.md` ④、
  `references/04-单项任务SOP与任务卡.md` ④ 三处均改写为经 `publish_plan.publish()`，
  并明写「**不得在 SOP 里直接调 `report_publish`**」；`scripts/router.py` 规则清单同步补第 8 条。
  **不存在「未经检查即可发布」的文档描述**（旧措辞「`report_publish` 异步提交」在包内 0 处）。

**验收**（离线替身 + 合成材料，**不产生任何真实付费发布**）：
`publish_plan.py selftest` **52 项断言**全过；
`maintenance/expert-package-review/cases/12-发布计划与预算/`（29 组 check / **108 项断言**）→ 0 失败，
覆盖 **11 个场景**（A 资料足够不发 / B 无必要性且改参数须重检 / C 研究 8 家不缩减 / D 成本逐项加总且未知不按 0 /
E 预算 10 两项各 6 并发只过 1 项 / F 超时不重发且业务失败≠未扣点 / G `aid` 乱序不串对象 / H 取 aid 仅 `accepted` /
I 参数变化须重检 / J 复用三态互不冒充 / K 正常任务按计划提交）；
`verify.py` 新增 `轮12-` 分组（实现 / 自检 / 计划 / 成本 / 预算 / 并发 / 入口 / 重检 / 未知 / 复用 /
恢复 / 绑定 / 真实链 / 回归 / 条款 / cases 共 **34 项断言**）；
其中「正常入口完整记录」由 `run_plan_flow()` 从**真实专家包入口**跑通，
「真实调用链」由 `shenwa_api.Client` + `ScriptedTransport` 经本入口发起（过检查实发 1 次且参数与计划一致，
未过检查实发 0 次）。

**未改**深挖 AI 服务端、接口路径与接口注册表（仍 **15 个**接口）、服务端模板与算法、既有错误分类与
参数校验实现。

**明确未具备（留给后续轮次）**：本轮**未**实现**内容有效性自动判定**（复用接口只接通并记三态，
`content_check` 恒为 `not_implemented_round4`）；**未**实施第 4 轮背景事实注入与内容复用，
**未**实施第 5 轮监测。跨进程预算锁为**进程内原子**（`threading.RLock`）。

## 2026-10-03 — v1.11.0（第 2 轮：多维需求识别及下游完整传递）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.10.0 → 1.11.0` | 用户一次提多个问题时**逐题理解、逐题处理、逐题回应**；`step3_metrics()` 不再只留优先级最高的那一个指标族；新增「需求清单」与「交付门」（覆盖 / 完成二分） |
| shenwa-agent | 1.1.5（不变） | 本轮未改动（`skills/shenwa/` 与接口配置均未动） |

**背景**：`router.step3_metrics()` 命中多个指标族时**只返回优先级最高的那一个**
（`for fam in prio: if fam in scores: return`），其余族被**静默丢弃**；下游消费者只读那个单一 `family` ——
用户问三件事，实际只做了一件，且**没有任何地方报错**。另：「出现分析二字」「命中两个以上指标族」
曾被当作「发深度报告」的机械触发条件。

**变更**：

- `skills/shenwa-hybrid/scripts/requirement.py`（**新增**，公共模块）：`split_questions()` 拆题、
  `classify_ask()` **只用于理解需求**（不决定是否发报告）、`choose_execution()` 按**证据缺口**选执行方式、
  `build_requirements()` 建**统一问题清单**（编号 / 原话 / 对象与时间 / 要查的事实与计算 / 指标与证据 / 执行方式 / 状态与回答位置）、
  `deliver_gate()` 做**覆盖 vs 完成二分**、`NullPublisher` + `run_flow()` 离线替身（**只计数不发布**）。
  `selftest` **66 项**。
- `scripts/router.py`：`step3_metrics()` **保留全部族**（新增 `families`），旧 `family` **仅作兼容保留**、
  不再作为执行多维任务的唯一依据；新增逐题模板候选与 `per_question_routing()`；多族 `MODE_RANK` 取**最强**；
  `template_for()` 逐题判层级；`route()` 支持 `answers=`，交付门不过时给出 `delivery_blocked`。
  `selftest` **131 项**（原有行为**零回归**）。
- **硬规则由五条扩为七条**（v1.7.0 五条 + v1.11.0 增补两条）：
  **6. 多维需求一条不丢**（**问题数量 ≠ 指标族数量**，同一族下多个问题不得按族去重）；
  **7. 执行方式按证据缺口选**（不得用「族数」或「分析」二字机械触发报告或改选 2001）。
- **新增 Q0.8「执行方式初判」**：`direct_fetch` / `compute` / `existing_material` / `report_candidate` / `blocked` 五档，
  **可按问题逐条不同**，一个任务**可组合**多种执行方式。
- **「需求清单」成为路由必填输出物**；**交付门**（**覆盖** = 是否登记并处理、有无静默遗漏；
  **完成** = 是否已有足够证据并实际回答）**进入实际交付流程**，不是只写在任务卡里；
  存在未完成的必需问题时整体**不得**标记为全部完成，**缺资料不计入完整回答**、也不得用其他问题的结论代替。
- 改选 2001 的**唯一合法触发**收窄为 `coverage_gap`（本层级专项模板覆盖不了用户已列出的问题）
  **且** `2001_eligible`（个股层级）；`switch_reason.by_family_count` **恒为 False**。

**验收**：`requirement.py selftest` **66 项**、`router.py selftest` **131 项**全过；
`maintenance/expert-package-review/cases/11-多维需求与下游传递/`（28 组 check / **148 项断言**）→ 0 失败；
`verify.py` 新增 `轮11-` 分组（实现 / 自检 / 多族保留 / 不丢族 / 执行方式 / 不自动 2001 / 覆盖与完成 /
入口 / 回归 / 条款 / cases 共 25 项断言）。
七场景全部经**真实生产函数**观测：S1 「同时查 PE 和资产负债率」两问均 `direct_fetch`、**发布调用 0 次**；
S2 「解释毛利率为什么下滑」材料足够 → `existing_material`、**不因属分析问题自动发报告**；
S3 「估值 + 财务风险 + 事件影响」三问逐题模板 `[9, 7, 8]`、**不因族数自动选 2001**；
S4 同一指标族下两问 `family_counts={'financial_compare': 2}`、**未按族去重**；
S5 多对象任务对象与问题一一对应、编号不串；S6 行业层级 `blocked_by_level`、`switched=False`；
S7 原有单一问题任务行为与改前一致（第 1 轮参数校验继续生效）。
`cases/07-能力边界与任务路由/input-checks.json` 的 `fallback.keep.指标` 期望值随本轮语义改为**全族列表** `["valuation"]`。

**回归**：`shenwa_api.py selftest` 79 项、`data_qc` / `right_side` / `valuation` / `preferences` /
`evidence` / `capability` 用例全部重跑通过；第 1 轮参数校验、既有数据与证据规则、能力边界、
错误分类与防重复提交**均未改动**。规则文档同步 **16 个**（`skills/shenwa-hybrid/SKILL.md` 与
`references/` 7 份 + `QUICKSTART` + 包/根 `README` + `agents/` 定义 + 6 张任务卡）。

**未改**深挖 AI 服务端、接口路径与接口注册表（仍 **15 个**接口）、服务端模板与算法。

**明确未具备（留给后续轮次）**：本轮**未**新增第 3 轮的发布计划与预算管理，
**未**实现第 4 轮的内容复用（涉及处一律显式登记为 `not_implemented_yet`，**不写成已具备**）。

## 2026-10-03 — v1.10.0（第 1 轮：发布参数本地校验与接口约定核实）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.9.3 → 1.10.0` | 在**真实发布入口**加参数前置校验：`tid` 必填且必须是字符串、`question` ≤60 字、`text` ≤500 字；非法参数在**发请求前**拦截（不发、不换站、不消耗点数） |
| shenwa-agent | 1.1.5（不变） | 本轮未改动 |

**背景**：`wfStartPublish` 的参数约定此前只写在文档里、**没有可执行校验**，非法参数要等服务端返回 400/SRV 才知道，
且 `text` 的必填性在「官方配置（required:false）」与「实测（缺省返回 `Missing required parameters`）」之间**存在冲突**。
另：本地校验必须落在**唯一调用入口**才真正生效。

**变更**：

- `skills/shenwa/shenwa_api.py` 新增 `PUBLISH_PARAM_SPEC` / `PUBLISH_TEXT_REQUIRED=False` / `PUBLISH_LEN_UNIT`
  与 `publish_param_check()`，并在 `validate_params()` 末尾接线 —— 而 `validate_params()` 由 `Client.call()`
  （`shenwa_api.py:1001`）调用，即**所有接口调用的唯一入口**（`--dry-run` 同样触发且不发请求）。
- 新增本地检查类型 `publish_gate` 与 `run_publish_gate()`：从真实入口发起一次发布、用离线替身记录调用次数，
  用于验收「未通过 → 0 次请求」「通过 → 1 次且提交对象一致」。
- `text` **必填性冲突登记为 C1**：官方标 `required:false`、实测缺省会 `SRV`，故本轮**只告警不阻断**，
  开关 `PUBLISH_TEXT_REQUIRED` 置 `True` 即切硬拦截。**不擅自把合法可选参数改成必填。**
- 长度口径 `PUBLISH_LEN_UNIT = "unicode_code_points"`（按 `len()` 计、**不 strip**）——
  服务端实际计数方式（码点/字节）标为**待验证推断**，不声称是永久保证。
- 校验**只读不改**：不机械截断问题、不删除关键限定条件、**不改写研究截止时间**来迁就接口。
- 既有 `MUTATING_APIS` / `submit_unknown` / 防重复提交 / 主备站回退与错误分类逻辑**未改动**。

**验收**：`shenwa_api.py selftest` → **79 项断言全过**（原 56 项零回归）；
`maintenance/expert-package-review/cases/10-参数校验与接口约定/`（26 组 check / **58 项断言**）→ 0 失败；
`verify.py` → 新增 `轮10-` 分组（实现 / 自检 / 约定 / 冲突 / 长度口径 / 边界 / 覆盖 / 适用 / 入口 / 不阻断 / 防重发 / 条款）。
规则文档同步：`skills/shenwa/SKILL.md`（新增「★ 发布参数本地校验与长度口径」）、
`skills/shenwa-hybrid/references/02-能力分工与路由.md`（新增两行 + 三档标注）。
**未改**深挖 AI 服务端、接口路径、服务端模板与算法。

## 2026-10-03 — v1.9.3（专家卡介绍文案加入版本号）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.9.2 → 1.9.3` | 介绍文案写入 `v1.9.3 · 接入深挖接口 1.0.6`，专家卡上可直接看到版本 |
| shenwa-agent | 1.1.5（不变） | 本轮未改动 |

**背景**：v1.9.2 只升了 `plugin.json` 的 `version` 字段、未动介绍文案，导致专家卡「介绍」里看不到版本号。

**变更**：`plugin.json` 的 `description` / `displayDescription.zh` / `displayDescription.en` 三处、
市场清单 `my-experts/.codebuddy-plugin/marketplace.json` 该条、仓库根 `marketplace.json` 的
`description` / `description_en` 同步写入版本号；`displayName` / `profession` / `categoryId` /
`tags` / `quickPrompts` **均未改动**，规则、脚本、接口配置与算法**均未改动**。

**文案定稿**（`displayDescription.zh` 必须落在官方推荐的 40~50 字）：

- `description`：真深挖综合研究 v1.9.3（接入深挖接口 1.0.6）：融合深挖AI与金融数据，支持公司研究、行业比较、趋势监测与关键数据核验。
- `displayDescription.zh`（42 字）：v1.9.3 · 接入深挖接口 1.0.6 ｜ 深挖写分析、数据源核数字，附校验表。
- `displayDescription.en`：v1.9.3 · Shenwa API 1.0.6 | Combines Shenwa AI with financial data to support company research, sector comparison, trend monitoring, and key-data verification, with a per-item validation table.

自检：官方 `validate_expert.py` → **✅ valid，无 warning**（首稿 zh 为 68 字、触发了长度告警，已收紧到 42 字）；
`verify.py --backup` → **全部通过**（维护源=市场源=加载副本 49 文件逐一致）；
热刷新日志 `experts.custom.plugin.changed` 已回（无需重启）。

## 2026-10-03 — v1.9.2（同步深挖官方接口 1.0.6）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.9.1 → 1.9.2` | 接口配置对齐官方 1.0.6（修正 `get_right_side` 路径笔误）+ 重打免邀请码补丁 |
| shenwa-agent | 1.1.5（不变） | 本轮未改动 |

**变更**：官方深挖接口发布 **1.0.6**，相对 1.0.5 只修正 `get_right_side` 的路径
（`/api/api/market/getRightSide` → `/api/market/getRightSide`），**无新增接口、无破坏性变更**。
本机据此把接口配置对齐为 `shenwa_config_1.0.6.json`（**被取代的 1.0.5 已从包内移除**，需要原文可随时用
`download_version version=1.0.5` 取回），
同步 `skills/shenwa/SKILL.md` 的版本跟踪与 `skills/shenwa-hybrid/` 的接口版本描述（能力清单已重新生成）；
**免邀请码补丁 `_local_patch` 已在 1.0.6 上重打**。
`plugin.json` 的 `description` / `displayDescription` / `displayName` / `profession` **均未改动**。
自检：`python shenwa_api.py selfcheck` → `drift: []`（此前 1.0.5 配置持续报 1 项漂移）。

## 2026-10-02 — v1.9.1（专家包介绍文案调整）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.9.0 → 1.9.1` | **仅改介绍文案**（不涉及任何规则、脚本、接口与算法） |
| shenwa-agent | 1.1.5（不变） | 本轮未改动 |

**变更**：`plugin.json` 的 `description` / `displayDescription.zh` / `displayDescription.en` **三处同时**改为
「**真深挖综合研究（True Shenwa Integrated Research）：融合深挖AI与金融数据，支持公司研究、行业比较、趋势监测与关键数据核验。**」；
仓库根 `marketplace.json` 该条 `description` / `description_en` 同步；`skills/shenwa-hybrid/SKILL.md` frontmatter `version` 跟随包版本。
`displayName`（真深挖综合研究）与 `profession`（综合投研分析师）**未动** —— 前者是卡片名、后者是分享链接 `expertname` 的来源，
一改会让已分发链接的语义漂移。

> 说明：原介绍里的「开通只要手机号，不用邀请码；主站打不开自动切备用站」不再出现在**门面文案**中，
> 但该行为**未改变**（仍写在 `skills/shenwa/SKILL.md` 与接入规则里）。

## 2026-10-02 — v1.9.0（调整偏好机制、任务组织与输出形式）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.8.0 → 1.9.0` | **偏好机制 + 任务组织 + 输出形态**（不改服务端、不改接口地址与算法） |
| shenwa-agent | 1.1.5（不变） | 本轮**未改动** `skills/shenwa`，故不升版 —— 避免无内容变更的版本号跳动 |

**背景（要解决的问题）**：

1. **偏好一锅烩**：把「语言 / 长度 / 图表 / 结构 / 关注重点」这类**表达偏好**与
   「研究期限 / 比较目标 / 风险限制 / 明确指定的方法」这类**任务约束**混在一起，
   于是「用户进取」被当成「结论可以更乐观」，甚至去改情景概率。
2. **风格改事实**：风险容忍度与结论语气被写进派生影响，出现
   「因为用户进取就增加上行情景概率或权重」这类规则 —— 同一套事实，因表达偏好而变样。
3. **篇幅决定配比**：按内容篇幅强制 50/50，偏离就「自动回退」，
   与本轮「关键问题回答完整、重要结论有依据、证据不足可见」的要求冲突。
4. **六段强制**：六段结构被当成所有任务的固定格式，短答任务被迫附上全部深挖原文。
5. **零散交付**：复杂任务被拆成一堆互不相干的小交付，重复取数、结论各说各话。
6. **自动扩写**：「仅查询」「仅整理用户提供材料」被自动扩展成新的深度研究。
7. **格式与来源混淆**：格式转换可能改动结论 / 数字；深挖官方原文 PDF 可能被当成「专家包综合报告」。

**本次变更**：

1. **新增 `skills/shenwa-hybrid/scripts/preferences.py`**（本包**唯一**的偏好实现；76 项自检）：
   - **偏好二分** `split_preferences()`：表达偏好 5 项（language / length / visual / structure / focus）+
     任务约束 4 项（horizon / comparison_target / risk_limit / method）；其余键登记为未分类（执行参数）。
   - **五项不变式** `FACT_INVARIANTS`（facts / key_numbers / evidence_state / verdict /
     method_applicability）+ `guard_invariants()`：**只比对这五项**，`PRESENTATION_ONLY`（顺序 / 措辞 /
     语气 / 强调 / 语言 / 形态）允许变。
   - **结论呈现** `render_conclusion(spec, bias)`：**只改** `order` / `lead` / `text`；
     审慎 = 先列反面证据、积极 = 先给方向、中性 = 并列利弊（`BIAS_ORDER` / `BIAS_LEAD`）。
   - **风险容忍度** `apply_risk_tolerance()`：`judgement` 与 `scenarios` **原样返回**，
     并置 `objective_unchanged=True` / `probability_unchanged=True`；只改风险段详略与强调。
   - **假设情景** `scenario_variation()`：被替换者标 `kind=hypothesis_variation` + `label=[假设变化]` +
     保留 `baseline_value`；`fact_changes_claimed` 恒空。
   - **权重语义** `weights_disclaimer()` / `weight_effect()`：`semantics=reading_attention_only`；
     只产出「勾选哪些能力」（`must_check` = D3 / D9），`produces_number=False`。
   - **交付形态** `choose_form()` / `form_spec()`：`short_answer` / `comparison_table` / `brief` /
     `full_report`；**只有 `full_report` 的 `six_section=True`**；`passthrough` 即便明确要 `full_report`
     也**降级并记 `refused`**。
   - **归档策略** `archive_policy()`：`body_attaches_full_text` 随形态，`archive_keeps_full_text` **恒为 True**；
     `entry` 含 aid + 生成时间 + 来源。
   - **复合任务** `composite_task()`：按键去重 → `duplicate_fetches`；同键异值 → `conflicts`（并列原值 + owners）；
     `unified_conclusion_required=True`。
   - **不自动扩展** `classify_task()`：命中 `PASSTHROUGH_WORDS` → `passthrough=True`、
     `expansion_allowed=False`、`forbidden=EXPANSION_FORBIDDEN`。
   - **格式转换** `convert_format()` 逐字带出 `conclusions` / `numbers` / `verification_state` 并给
     `invariants_ok`；`label_for_pdf()`（`shenwa_official`=原始报告 / `composite`=专家包综合报告）；
     `pdf_capability()` → `composite_pdf_supported=False`、`deliver=[markdown, table, xlsx]`。
   - **回归套件** `regression_suite()`：在 `data_qc` / `evidence` / `valuation` / `capability` / `router`
     各自 `selftest()` 上重跑 + `right_side.cut_cross_section()` 功能性「先切截面」验证，共 6 项。
   - **CLI**：`selftest` / `forms` / `split` / `rules`（`rules` 可打印现行规则与已删除规则）。
2. **文档同步（20 个文件 / 96 处）**：03 yaml、03A 选项库、03B 表单、01 总纲（红线 6 改「任务组织」、
   新增红线 20~22）、02 路由（路由输出物加「交付形态」、口径冲突不得因偏好改判）、04 SOP（② 偏好二分 /
   ⑥ 先定形态 / 任务卡加「交付形态」栏 / 复合任务与不自动扩展）、05 输出规范（新增「交付形态四档」并把六段
   收窄为「完整报告」的组织形式）、06 指南（新增 3 条 FAQ + 交付形态补充）、QUICKSTART、Agent 定义、
   `SKILL.md`（红线 6 改、新增红线 15~17、参考索引指向 `preferences.py`）、4 套预设、5 张任务卡。
   **清除的旧规则**：「增加上行情景权重 / 概率」「偏离基准未写明理由则自动回退 50/50」「否则按 baseline 执行」
   「不附校验表则自动回退到 full」「单一任务」；**修正 4 处悬空引用** `validation/` → `references/` / `examples/`。
3. **新增验证用例 `maintenance/expert-package-review/cases/09-偏好机制与交付形态/`**：
   `input-checks.json`（**39 个检查 / 展开 163 项断言**，含用例 A~H 与 4 条反例）+
   `expected.json`（A~H 逐项**实跑记录**、`cross_checks` R1~R7、`known_gaps`、恢复办法）。

**验证**：`maintenance/expert-package-review/verify.py` 新增 `check_preferences()`（分组 `轮7-`，18 段）：

- `轮7-自检`：`preferences.selftest()` **76 项断言**全过（离线，无任何外部调用）；
- `轮7-二分` / `轮7-不变式` / `轮7-风险` / `轮7-假设情景` / `轮7-权重` / `轮7-形态` / `轮7-归档` /
  `轮7-复合` / `轮7-不扩展` / `轮7-格式`：调用生产函数断言，逐项打印实得值；
- `轮7-回归`：回归套件 **6 项全过**（财务矛盾 / 指标混用 / 未核实证据 / 估值调整 / 任务路由 / 发布超时 /
  右侧日期切片）；
- `轮7-A~H`：`cases/09` **163 项全过**；
- `轮7-条款`：新条款在场，且旧规则（`增加上行情景权重` / `自动回退 50/50` / `否则按 baseline 执行`）已清除
  （md 与 yaml 都扫，历史 `examples/` 除外）。

**关键用例结果（离线，未发任何报告、未执行任何服务端写操作）**：

| 用例 | 设定 | 实际结果 |
|---|---|---|
| A | 同一事实，审慎 vs 积极 | 事实 / 关键数字 / 证据状态 / 结论判定**逐字一致**；仅顺序（counter-first vs direction-first）与措辞不同 |
| B | 风险容忍 保守 vs 进取 | `judgement` 与 `scenarios`（含概率 0.25 / 0.50 / 0.25）**逐字一致**，仅风险段呈现不同 |
| C | 只问一个事实 | 交付**短答**：结论 + 关键依据 + 必要限制；**正文不附全文**，归档仍完整并给入口 |
| D | 需要完整推演 | 交付**完整报告**：结论 / 证据 / 假设 / 反证 / 局限齐备；六段仅此形态使用 |
| E | 「扫描 + 比较 + 解释」复合任务 | 4 条取数 → 去重后 **2 条**（`duplicate_fetches=2`）；同键异值（PE 15.98 vs 16.4）**并列原值**；`unified_conclusion_required=True` |
| F | 「查一下 X」/「把我给的材料整理成表」 | `passthrough=True`、`expansion_allowed=False`；**即便明确要 `full_report` 也降级为短答并记 `refused`** |
| G | markdown / xlsx / docx 转换 | 结论 / 数字 / 验证状态**逐字一致**；`docx` 报不支持；官方原文 PDF 标签 = 「原始报告」；综合版 PDF **不支持** |
| H | 前几轮关键回归 | 财务矛盾 / 指标混用 / 未核实证据 / 估值调整 / 任务路由 / 发布超时 / 右侧日期切片 **6 项全过** |

## 2026-10-02 — v1.8.0（修正本地 API 客户端的错误分类与恢复行为）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.7.0 → 1.8.0` | **本地客户端 + 调用方文档变更**（不改服务端、不改接口地址与算法） |
| shenwa-agent | `1.1.4 → 1.1.5` | 同步同一份本地客户端与 `skills/shenwa/SKILL.md`（三份副本逐字一致） |

**背景（要解决的问题）**：

1. **「一遇异常就重登 / 就换站」**：旧客户端把 `success=false` 与 HTTP 401 一概当作 Token 过期，
   于是**权限不足**（`status=AUT` + 「你没有权限」）也会触发重登 —— 而重登对权限问题**永远无效**，
   只是把同一件事反复重试。`status=AUT` 其实有**两种含义**，旧实现分不开。
2. **有副作用的调用会被重发**：`report_publish` 超时后，旧逻辑按「网络异常」换站重试 ——
   可请求**可能已被服务端接收**，换站重发就是**重复发布**。客户端也从未声明「本地记录不是幂等保障」。
3. **错误信息不区分层次**：网络异常、HTTP 错误、业务失败、身份失效、权限不足、参数错误、
   内容不合法、空数据混在一起，上层无法据此决定「该重试、该换站、该重登、还是该直接如实上报」。
4. **测试与生产同钟**：重试上限只数次数、不算时间，验证「超时与退避」只能真等十几分钟。
5. **参数与内容未校验**：带前导零的代码可能被转成整数；下载回的错误 JSON / HTML 可能被存成「成功」的 PDF；
   非法的版本配置可能被当作可用更新。
6. **接口定义散落**：配置说明与脚本行为可能脱节（官方 1.0.5 配置里 `getRightSide` 写成双 `api` 的 404 路径）。

**本次变更**：

1. **`skills/shenwa/shenwa_api.py` 重写为本地客户端**（三份副本逐字一致，md5 `947f3fe8`）：
   - **10 类错误 + 1 个状态**：`ERROR_CLASSES` = `ok` / `empty` / `network` / `http` / `business` /
     `auth` / `permission` / `param` / `invalid_content` / `unknown`；另有非分类状态
     `submit_unknown`（「提交状态未知」）。每类都写明**恢复动作**，`shenwa_api.py errors` 可直接打印。
   - **分类优先级 = 业务负载优先于状态码**（`classify_response`）：只有负载能把 `status=AUT` 的
     两种含义分开 —— 含权限词 → `permission`（**零重登**）、含身份词 → `auth`（**有限重登**）、
     措辞不明 → `unknown`（**不猜**）。`classify_http` 单独映射 HTTP 码。
   - **恢复策略**：`auth` 有限重登（`max_relogin=1`）；`permission` / `param` / `business`
     **不重试、不换站、不重登**；`network` 有界重试 + 换站；`unknown` 保留诊断**不伪装成「无数据」**。
   - **首次登录纳入恢复流程**：无缓存 Token 时同样先主站、失败再走备用站（`login_on`），
     **不新增任何邀请码要求**。
   - **有副作用调用保护**（`MUTATING_APIS` = `report_publish` / `collect_report` / `delete_report`）：
     对某站发出后**不重发、不换站重发**；结果不确定 → `_submit_unknown()` 写本地最小任务记录 +
     `reconcile()` **只读核对** → 返回 `submit_state=submit_unknown`，**退出码 2**。
   - **本地最小任务记录**：请求特征（`_fingerprint`）· 时间 · **账号的安全引用**（`account_ref` = sha256 前 16 位）·
     已取得的 aid；`open_submissions()` 可列出未决提交。文档与代码均声明**不是服务端幂等保障**。
   - **轮询只查原 aid**（`wait_report`）：连续异常超上限 → `unknown`（**不判报告失败**）；
     **只有 `data.status == "error"` 才 `report_failed=True`**。
   - **有界重试 `RetryPolicy`**：默认 `max_attempts_per_base=2`、`max_total_seconds=30.0`、
     `backoff=(0.5, 1.0, 2.0)`、`max_relogin=1`；**等待与请求耗时都计入总预算**，超预算立即停。
   - **模拟时钟 `MockClock` + 脚本化传输层 `ScriptedTransport`**：全部 A~K 用例**离线**跑真实调用链，
     **不真等、不联网、不发任何写请求**。
   - **参数按字段定义转换**：`param_is_string()` / `STRING_PARAMS` / `STRING_LIKE_KEYWORDS` 保持字符串，
     且「以 0 开头且不止一位」的值**一律不转整数**（`000300` 不变 `300`）；`version` 不会变成 float。
     `validate_params()` 校验 `codes` 后缀 / `dayOffset∈[1,20]` / `download_version` 缺参；
     **空结果 + 参数告警改判 `param`**（不再静默返回空）。
   - **下载与更新先验内容**：`looks_like()` 识别 `%PDF-` / `<html` / `PK` / `{`；`validate_download()`
     拒绝错误 JSON / HTML / 空响应；`update_is_usable()` 要求**版本确实更新且配置合法**。
   - **Token 与账号绑定**：Token 记录为 `{"v":2,"account_ref":…,"token":…}`；**旧版裸 Token 一律不可用**
     （否则切换账号会沿用旧 Token）；`account_ref` **不含明文手机号**，日志/异常/测试输出均不回显凭据。
   - **注册表权威**：`APIS` 是**唯一权威**（15 个接口，含 `download_version`）；
     `API_REGISTRY_AUTHORITY` + `registry_report()` / `selfcheck` 报出配置与注册表的漂移。
   - **CLI**：`--json`（stdout 单 JSON、调试信息进 stderr）、`--dry-run`（不发任何请求）、`--verbose`；
     子命令 `list` / `errors` / `selfcheck` / `selftest` / `tasks` / `report_wait`；
     **退出码 0 成功（含 empty）/ 1 失败 / 2 提交状态未知**。
   - **兼容**：`call(name, params, timeout)` 仍返回 `(结果, base)`；`login(base)` 仍可单独调用。
     ⚠️ **已登记的行为变更**：`success=false` 时兼容层现在抛 `ApiError(CATEGORY)`（旧版静默返回结果）。
2. **调用方文档同步**（三份 `skills/shenwa/SKILL.md` 各 8 处 + `references/06` 1 处 + 本轮追加 6 处）：
   - 新增「**客户端错误分类与恢复（v1.8.0）**」分类表、`status=AUT ≠ 身份失效` 警示、
     有副作用调用安全约定、重试预算、机读输出、参数与下载、**接口定义由哪里维护**。
   - **清掉与分类规则直接冲突的旧条款**：原文「遇 401（`success=false, status=AUT`）须重新登录刷新 Token」
     → 改为按 `msg` 分流（`auth` 有限重登 / `permission` 重登无效）。
   - 接口清单表补 `download_version` 一行（此前表内 14 行，却在同文件写「注册表为 15 个接口」）。
   - 新增 CLI 示例：`--json` / `report_wait` / `errors` / `selfcheck`。
   - `references/06` 新增两条 FAQ：「为什么说提交状态未知」「报了几次网络异常会不会重复发布」。
3. **新增验证用例 `maintenance/expert-package-review/cases/08-客户端错误分类与恢复行为/`**：
   `input-checks.json`（**139 项检查**：分类表 / 恢复规则 / 重试预算 / HTTP 与负载分类 / 参数 / 下载 / 更新 /
   Token / 注册表 + A~K 十一场景 + K 收紧预算对照）+ `expected.json`（A~K 逐项**实跑记录**、
   `cross_checks` R1~R7、`known_gaps`、恢复方法）。

**验证**：`maintenance/expert-package-review/verify.py` 新增 `check_client()`（分组 `轮6-`，12 段 / 27 项断言）：

- `轮6-自检`：`shenwa_api.selftest()` **56 项断言**全过（离线，无写请求）；
- `轮6-分类表` / `轮6-分类` / `轮6-参数` / `轮6-下载` / `轮6-Token` / `轮6-注册表`：调用生产函数断言；
- `轮6-A~K`：`cases/08` **139 项全过**；
- `轮6-用例`：A~K **端到端离线实跑**，逐例记录分类 / 退出码 / 尝试数 / 重登数；
- `轮6-模拟时钟`：K 收紧预算后**更早停、等待更少**，全程未真实等待；
- `轮6-条款`：新条款在场，且旧条款（`须重新登录刷新 Token`）已清除。

**关键用例结果（全离线，未发生任何真实写请求）**：

| 用例 | 设定 | 实际结果 |
|---|---|---|
| A | 无缓存 Token、主站登录失败 | 进入**备用站**登录路径 → `ok`，退出码 0 |
| B | HTTP 401 身份失效 | **重登 1 次**后原请求成功 → `ok`，退出码 0 |
| C | `status=AUT` + 「你没有权限」 | → `permission`，**重登 0 次 / 登录请求 0 次**，退出码 1 |
| D | `report_publish` 响应超时 | **只发 1 次**（未换站重发）+ 只读核对 1 次 → `submit_unknown`，**退出码 2** |
| E | 已有 aid、查询连续异常 | 保留原任务，**不判失败、不新建**（发布 0 次），verdict=`unknown` |
| F | `success=false` | → `business`，**非 0 退出码**，上层可识别 |
| G | 下载回错误 JSON | → `invalid_content`，**未落盘**，不报成功 |
| H | `codes=000300.CSI` 前导零 | 原样发送（字符串），空结果给**参数告警** |
| I | 账号切换 | **未使用旧账号 Token** |
| J | 正常查询 / 下载 / 报告终态 | 不受影响（`ok`，**重登 0 次**） |
| K | 主站持续超时 | 尝试 **4 次**（每站 2 次）、模拟等待 3.0s；收紧预算到 3s → 尝试 **1 次**、等待 0s |

## 2026-10-02 — v1.7.0（核实能力边界，修正任务路由）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.6.0 → 1.7.0` | **规则 + 可执行实现变更**（非文案） |
| shenwa-agent | `1.1.4`（版本号不变） | 本轮同步 `skills/shenwa/SKILL.md`（该副本此前**滞后到 v1.0.4**，缺 `dayOffset` 面板语义与「先切截面再分桶」整段；已整份对齐规范副本，并含本轮右侧通道判据改版） |

**背景（要解决的问题）**：

1. **路由按词语机械分流**：旧路由用「排名 / 有没有 / 三个以上对象」这类**词语**决定走哪条通道，
   于是「比较三家公司的盈利质量」与「哪些板块发生右侧升级」被塞进同一条路（都当成横截面扫描），
   而「深入比较三个行业未来三年的竞争格局」又可能因为「三家 / 比较」被降级成一份名单。
2. **能力只有名字、没有边界**：原文档里 D1~D8 只有「编号 + 类别 + 示例 Skill」三列，
   **用途 / 输入 / 输出 / 对象层级 / 覆盖范围 / 必要依赖 / 权限 / 已验证程度 / 缺失时处理 全缺**。
   后果是「有画像名称 → 当成完整具备」（量化、回测、跨市场）、「文档写了 → 当成已验证支持」、
   「有接口 → 当成无门槛可用」。
3. **一个编号三种含义**：`D4` 同时代表 ① 数据能力「公告与事件」② 数据能力「技术指标」③ 偏好选项「可视化」。
4. **把「本地保存」与「服务端公开/私有」混为一谈**，并据此承诺「私有发布」「服务端不保存」——
   发布接口 `wfStartPublish` 的**参数只有 tid / question / text，没有任何可见性参数**。

**本次变更**：

1. **新增 `skills/shenwa-hybrid/scripts/capability.py`**（本包**唯一**的「能力边界」事实源，**101 项内置自检**）：
   - 24 项能力：`SHENWA_CAPS`（S1~S12）、`UTILITY_CAPS`（U1~U3）、`DATA_CAPS`（D1~D9），
     每项 9 个必备属性（`REQUIRED_FIELDS`），`missing_fields()` / `is_complete()` 可查缺。
   - 状态四态 `STATUS` + `by_status()` / `can_support()` / `refuse_reason()`；
     **`unavailable` 由「回测」承担，四态都有真实使用**（避免「写了四态、全用一态」）。
   - `capabilities_for(metric_family)` —— **路由层唯一**的「需求 → 能力」映射入口，路由模块**不硬编码**能力名。
   - `FORBIDDEN_PROMISES` + `scan_forbidden_promises()`：**私有发布 / 服务端不保存 / 删除即彻底清除 /
     全市场所有概念** 四类表述命中即违规。
   - `STORAGE_BOUNDARY`（`storage_boundary()`）：本地保存**完全可控**；服务端 `public` / `private` 是
     **结果态不是客户端选项**，依据指向 `wfStartPublish` 无可见性参数；3 条**不得承诺** + 可执行替代
     （想要私密 → 本地归档；想不落服务端 → **做不到**）。
   - `SPECIAL_CHAINS`（`special_chains()`）：量化 = 因子暴露与分位（conditional，链 = D2+D1+D9+D3+D6+D8，
     显式排除回测与 IC 计算）；**回测 = unavailable**；跨市场 = unverified（仅有配置项）。
   - `NUMBERING`（`numbering_note()`）：冲突、根因、3 条解法、不改写范围、编号总表。
   - `run_check_spec()` + 断言算子：供 `cases/07` 与 `verify.py` **真跑同一份生产逻辑**。
2. **新增 `skills/shenwa-hybrid/scripts/router.py`**（本包**唯一**的路由实现，**67 项内置自检**）：
   - **六步顺序不可颠倒**：`step1_research_question` → `step2_object_scope` → `step3_metrics` →
     `step4_time_range` → `step5_depth` → `step6_available`，由 `read_task()` 串起。
   - **语汇只影响批量方式**：`BATCH_WORDS`（排名 / 哪些 / 有没有 / 分布 / 全市场…）**只**喂给
     `_batch_mode()` 产出 `single` / `few` / `cross_section`，**永不参与** `metric_family` / 深度判定。
   - **深度判定顺序**：先深度/机制 → 再比对 → 再状态 → 最后名单 —— 保证「深入比较三个行业的竞争格局」
     不被「三家 / 比较」降级为名单。
   - `deliverable_for()`：`fact` / `state` → **直取**或**「数据 + 简析」**；`list` → 直取名单；
     `comparison` / `deep_research` → 发报告。非报告交付自动把 `S1` 移入 `not_needed`（**不强制另发报告**）。
   - `template_for()`：模板按**对象层级**选（红线），并给出 `2001_eligible`（**2001 仅个股级可用**）。
   - `preflight_fallback()`：回退**保持 对象层级 / 主题 / 指标 / 时间 四项不变**，四项被改掉则**不回退**。
   - `criteria_note()` / `rank_criteria()`：**排名 / Top-N 必须说明排序标准**；名单 / 分布类必须写明口径与截面。
   - `two_stage_plan()`：「先扫描、再解释候选」→ **同一总任务下的两个子任务**，不得只做一段。
   - `primary_blocked` → `action="honest_limit"`：首选能力不可用时**如实说明**，**不得改用别的问题充当完成**。
3. **新增 `skills/shenwa-hybrid/scripts/gen_capability_doc.py`**：由 `capability.py` 生成
   **`references/09-能力清单.md`**（人读版，九项属性明细 + 编号口径 + 存储边界 + 特殊链路 + 命令），
   `--check` 只比对不写盘；**文档与实现漂移由校验器断言**。
4. **文档同步**（Agent / hybrid SKILL / references 01·02·03A·03B·04·05·06·09 / QUICKSTART /
   templates 2 张 / presets 4 套 / 包内 README，逐文件保留原 EOL）：
   - `01-模式总纲`：新增**约束 18（能力边界清单为准）**、**约束 19（路由六步顺序与能力分流）**。
   - `02-能力分工与路由`：S 表补 **S12**、D 表补 **D9**；新增「**二·补 · 路由顺序与能力边界**」节
     （六步 + 五条硬规则 + 批量方式表 + 边界摘要）；**Q0.9 判据改版**（由「多标的横截面」改为
     「指标族 = 右侧状态」）；S11 行补对象层级与覆盖范围口径；模板全量表补「2001 为个股级」结论。
   - `05-输出规范与校验表`：新增「**一·补五 · 能力边界与任务路由**」6 小节；自检清单补 **7 项**。
   - `03A` / `03B`：偏好选项组 **D → F（F1~F6）** + 更名说明（配置字段不变）。
   - `04-SOP` / 2 张任务卡 / 4 套预设：技术指标的数据能力编号 **D4 → D9**；预设注释 `D1~D6 → F1~F6`。
   - `06-客户使用与修正指南`：能力边界表补 4 行（本地归档可控 / 服务端可见范围不可指定 /
     发布后删除 vs 不承诺不保存 / 因子分位 vs 回测 / A 股已验证 vs 港美股未实测）+ 「能不能只存本地」说明。
   - `skills/shenwa/SKILL.md`：右侧通道判据改版（**只服务「右侧状态」**），触发语汇降级为**批量方式提示**；
     交付物覆盖范围改为「按本次实际返回标注，不写死」。**三份副本同步**。
5. **新增验证用例 `maintenance/expert-package-review/cases/07-能力边界与任务路由/`**：
   `input-checks.json`（**173 项检查**：能力清单 18 组 + A~J 十例 + 4 组通用对比 + 路由顺序）
   + `expected.json`（A~J 逐项**实跑记录** / `cross_checks` R1~R7 / `known_gaps` / 恢复方法）。

**验证**：`maintenance/expert-package-review/verify.py` 新增 `check_capability()`（分组 `轮5-`）：

- `轮5-自检`：`capability.selftest()` 101 项 + `router.selftest()` 67 项 全过；
- `轮5-A~J`：`cases/07` 173 项全过（全部调用生产函数，不另写测试逻辑）；
- `轮5-用例`：A~J 端到端**实跑路由**，逐例打印 六步识别 → 候选 → 选用 → 交付形态 → action；
- `轮5-文档一致`：`09-能力清单.md` 与 `capability.py` **逐字一致**（生成器复算）；
- `轮5-条款`：新条款在场，且**旧表述已清除**（多标的横截面 / 先判数据形态 / 数据形态分流 / 触发语汇）。

**关键用例结果（离线路由演练，不新发报告）**：

| 用例 | 任务原话 | 指标族 | 候选 → 选用 | 交付 |
|---|---|---|---|---|
| A | 比较三家公司的盈利质量 | financial_compare | `['D2','S1']` → `['D2','S1']` | 报告（**不进右侧扫描**） |
| B | 哪些行业估值分位较低 | valuation | `['D1','D7','S1']` → `['D1','D7']` | 直取名单 + 排序标准 |
| C | 哪些板块乖离率改善最快 | bias_rate | `['S8','D9']` | 直取名单 + 「改善」定义 |
| D | 哪些板块发生右侧阶段升级 | right_side_state | `['S11','S1']` → `['S11']` | 直取名单 + 截面日期口径 |
| E | 查询一个板块现在处于什么阶段 | right_side_state | → `['S11']` | **数据 + 简析**（不发报告） |
| F | 深入比较三个行业未来三年的竞争格局 | deep_narrative | `['S1','S2']` | 报告（行业级，**2001 不可用**） |
| G | 先扫描全市场，再解释指定的两个候选 | 待澄清 | 两段式 + **先澄清指标族** | — |
| H | 某板块右侧阶段（无 VIP2） | right_side_state | S11 blocked → **honest_limit** | 如实限制 |
| I | 对某公司做估值分析（模板失败） | valuation | 回退保持四项、含 2001 | 报告 |
| J | 哪些板块值得看 | 未指明 | **先澄清**（不暗设风格） | — |

## 2026-10-01 — v1.6.0（修正本地估值方法及其适用条件）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.5.0 → 1.6.0` | **规则 + 可执行实现变更**（非文案） |
| shenwa-agent | `1.1.4`（不变） | 本轮不在范围内 |

**背景（要解决的三处一刀切 + 一处口径含混）**：

1. 包内把「有没有 D7」当成**能否估值的总开关** —— 缺 D7 即拒绝输出任何价格区间。实际上 D7 只是
   **相对估值类方法**的必要输入；绝对法（折现类、股息率法）只要自己的输入齐备，本可在边界内输出。
2. 「估值结论必须**至少两种方法**」把方法数量当成准入条件，单方法即使输入齐备也不输出。
3. 「任一锚点偏差 > 5% → 其后全部估值推演**整链作废**」作废范围过宽，会牵连与该锚点无关的结论。
4. 估值计算本身也含混：**FCFF 与 FCFE 未区分折现率**（WACC / 股权资本成本 Ke），
   企业价值 → 股权价值的**调整项未逐项列明**，历史 CAGR 外推被当作"未来增长率"使用。

**本次变更**：

1. **新增 `skills/shenwa-hybrid/scripts/valuation.py`**（本包**唯一**估值实现，31 项内置自检）：
   - **现金流 ↔ 折现率配对**：`classify_cashflow()` / `classify_rate()` / `check_discount_rate_match()`；
     FCFF 配 WACC（→ 企业价值）、FCFE 配 Ke（→ 股权价值）；错配即判错并说明方向性偏差
     （FCFF 配 Ke → 系统性低估；FCFE 配 WACC → 高估）。
   - **企业价值 → 股权价值桥**：`equity_bridge()` —— **未传参 = 未知**（`_MISSING`），
     **显式传 0 = 明确为零**；缺必要调整项（现金/非经营资产、有息债务、总股本）时
     `complete=False`、`per_share=None`，只回传不完整中间量并给出**高估/低估方向**；
     次要调整项未提供 → 按零处理但**显式声明**，`qualified=False`。
   - **永续公式守卫**：`perpetuity_value()` —— **g ≥ r 时阻止计算**（分母 ≤ 0），
     说明"会得到 ∞ 或负数"并给出三条替代做法。
   - **输入分类与外推标注**：`classify_inputs()`（历史观测 / 已披露事实 / 未来假设 / 估计值 / 计算结果，
     并抓出"历史值被当未来假设""假设被写成已披露事实"两类误用）；
     `historical_cagr_to_forecast()` —— 必须齐备 **预测期限 + 适用理由 + 敏感性**，
     且 `verified_future_growth` **恒为 False**。
   - **方法注册表与准入门**：`VALUATION_METHODS`（7 种方法 × 必要输入 × 适用对象 × 边界）、
     `select_methods()`（**按研究对象与数据条件选，不按风格强制**）、
     `valuation_gate()`（**取消「必须有 D7」「至少两种方法」**，改为逐方法检查必要输入；
     某方法输入不足**只停该方法**；全无齐备方法才不出数）。
   - **多方法差异**：`compare_methods()` —— 给离散度与差异来源；`dropped` **恒为空**，
     显式请求删除时记入 `refused_drops` 并拒绝；明示**接近程度 ≠ 准确度**。
   - **异常作用域**：`impact_scope()` / `scope_anchor_impact()` —— 只作废**真正依赖该输入**的推演，
     **不废弃整份报告**，替代原「整链作废」。
   - **历史材料政策**：`HISTORY_POLICY` + `revision_header()` —— 已发布历史材料**不覆盖**，
     错误示例**另建修订版**并写明「对应旧版 / 旧版问题 / 本版改动 / 两版并存」。
2. **规则文档同步**（Agent / hybrid SKILL / references 01·02·03A·04·05 / 2 类任务卡 /
   预设-价值稳健 / 包内 README，共 **10 个文件、25 处**，逐文件保留原 EOL）：
   - Agent 定义：红线 8 改写（去掉「≥ 2 种方法」）、④ 步取数改为「按方法必要输入」、
     ⑤ 步改为「按方法适用条件选择并解释差异」、新增**红线 13**。
   - hybrid SKILL：C15 段改写（D7 降级为"某类方法的必要输入"）、C17 段改为「多方法用于解释差异」，
     校验判定表与依据行同步改写、新增红线段。
   - 05-输出规范：C13 段（作废范围收窄）、C16 依据行改写、**C17 全节重写**、
     **新增「一·补四 · 估值方法与适用条件（v1.6.0）」共 7 小节**（配对 / 桥接与边界 / 永续守卫 /
     输入分类与外推 / 方法选择与准入 / 异常作用域 / 历史材料政策）+ 自检清单补 9 项。
   - 02-能力分工：原「D7 是开关」整节改写为「D7 只是相对估值类方法的必要输入」。
   - 04-SOP：锚点作废范围收窄、C17 段改为「估值门（v1.6.0）」、⑥ 约束补输入类别标注。
   - 01-模式总纲：新增**约束 16（估值方法与适用条件）**、**约束 17（方法准入与差异解释）**。
   - 03A-偏好选项库：取消「风格与估值方法相悖 → 强制并列」。
   - 任务卡（公司深度研究 / 行业对比）：新增「估值适用条件门（v1.6.0）」。
3. **新增修订示例** `examples/宁德时代-价值稳健/09-估值修订示例（v1.6.0）.md`：
   **不覆盖**历史材料 `07` / `08`，逐条对出旧版 5 处问题，并按 v1.6.0 口径重述同一条估值链
   （含桥梁反推复核：EV 13,303.40 亿 → 股权价值 15,531.14 亿 → 每股 335.70 元，
   并指出旧版未列次要调整项 → `qualified=False`，可能**高估**股权价值）。

**验证**：`maintenance/expert-package-review/verify.py` 新增 `check_valuation()`（分组 `轮4-`），
用例落在 `cases/06-估值方法与适用条件/`（**27 项检查**，含 2 条反例与 5 项附加），
全部调用**生产函数** `valuation.py`，不另写测试逻辑。

## 2026-10-01 — v1.5.0（统一证据采信 / 错误原文处理 / 资料不足时的交付）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.4.0 → 1.5.0` | **规则 + 可执行实现变更**（非文案） |
| shenwa-agent | `1.1.4`（不变） | 本轮不在范围内 |

**背景（要解决的规则冲突）**：包内同时写着「深挖原文必须**完整保留**，不摘要、不删改」与
「已确认错误必须**剔除**，不得以并列存疑变相保留」。两条字面上互斥 —— 执行者只能二选一，
于是要么把错误数字留在正文里，要么把原文删得没法审计。此外「有来源」被默认当成「已核实」，
导致只从某份报告转述的比例（如「站上均线比例 88.68%」）被当作已确认事实用来下判断。

**本次变更**：

1. **消解冲突：拆成两层**。**原始资料层**（归档件）完整保留全文，**含已确认错误的段落**，
   附报告编号（aid）/ 生成时间 / 来源；**研究层**（正文）只呈现本轮认可的事实、推断、假设与局限，
   **已确认错误的原文不得继续支持正文结论**（如需展示须标 `role=error_contrast` 且不进摘要）。
   字面冲突一律判为「**归档保留全文 + 正文禁用**」。
   规则优先顺序：**接入合规 > 归档完整性 > 正文采信 > 证据状态标注 > 结论确定程度 > 交付范围**。
2. **新增 `skills/shenwa-hybrid/scripts/evidence.py`**（本包**唯一**的证据分级实现）：
   证据五态（`verified` / `unverified` / `conflict` / `pending` / `missing`）+ **窗口归属**（窗口外 → 历史背景）；
   `archive_report()` / `build_body()` / `check_error_not_used()`（归档 vs 正文）；
   `bind_claims()`（结论↔证据对应 → 保留 / 降级 / 改写 / 撤回 / 历史背景）+ `hedge_policy()`（**反过度模糊**）；
   `classify_statement()`（已披露事实 / 情景假设 / 用户命题 / 估计）；`check_pair_comparability()`；
   `check_support_level_claim()`（乖离率 ≠ 价格前低）；`check_relative_absolute()`（相对强势与绝对下跌可并存）；
   `delivery_scope()`（资料不足收缩范围）；`check_statistic()`（样本 / 分母 / 去重 / 时间口径 / 归属）；
   `coverage_split()`（**已检查覆盖率 ≠ 已独立验证覆盖率**）；`resolve_status()`（交付状态阶梯）；
   `assemble_delivery()` + `check_delivery_consistency()`（**摘要 / 正文 / 表格 / 附件说明四者一致**）。
3. **规则文档同步**（Agent / hybrid SKILL / `shenwa` SKILL / references 01·02·04·05·06 / QUICKSTART /
   5 类任务卡 / 包内 README 共 12 个文件）：新增 Agent 红线 12、hybrid SKILL 红线 14、01-总纲约束 14·15；
   05-输出规范新增「一·补三 · 证据采信与交付范围」；04-SOP ⑥ 增「证据采信门」并补 4 行异常处理；
   任务卡补「证据采信门」段。**已确认错误的原文改为归档保留、正文剔除**（原文保留类表述一并改写）。

**验证**：`maintenance/expert-package-review/verify.py` 新增 `check_evidence()`，
用例落在 `cases/05-证据采信与交付范围/`（24 项检查，含 2 条反例 + 1 次端到端离线装配），
全部调用**生产函数** `evidence.py`，不另写测试逻辑。

## 2026-10-01 — v1.4.0（统一数据口径 / 落实确定性校验 / 取消结构化接口免检）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.3.1 → 1.4.0` | **规则 + 可执行实现变更**（非文案） |
| shenwa-agent | `1.1.4`（不变） | 本轮不在范围内 |

**背景**：进入分析的数据含义不统一（半年度/全年/单季/TTM 混用、MA 与 EMA 混用、指标水平与变化量混用），
且原规则把 `get_right_side` 的结构化输出当作「等同行情数据、可直接使用、无需二次校验」——
导致接口字段内部的矛盾（如接口自报涨幅与其前收/收盘不闭合）无法被识别，
`has_veto` 缺失还会被真值判断静默当成「无否决」。

**本次变更**：

1. **新增 `skills/shenwa-hybrid/scripts/data_qc.py`**（本包**唯一**的数据质量实现）：
   最小必要字段约定（缺失记未知、不补造）；期间与口径分类（半年度/全年/单季/TTM、MA/EMA、绝对/相对、水平/变化量、%/百分点）；
   `check_comparable()` **可比性先于偏差**；`relative_deviation()` **分母统一取基准值**，
   基准为零/近零/符号变化/不可比时**明确处理、不机械套比例阈值**；
   恒等式核对、涨跌幅复算、正文与表格一致性、金额与单位量级检查、摘要与表格一致性；
   `check_panel_quality()` 面板质量与**否决字段三态**；`classify_payload()` 区分空结果与明确业务失败。
2. **`right_side.py` 接入质量门**并修掉缺陷：`veto` 只认真值 `True`，新增 `veto_missing` 单列；
   `analyse()` 增加 `quality` / `section_quality` / `payload_status`；覆盖范围改为**按实际返回统计**，不再写死 62。
3. **删除「结构化接口免检」规则**（Agent / hybrid SKILL / shenwa SKILL / references 02·04·05 / 模板 / 客户指南 共 7 处）：
   改为 **观测数据 / 计算指标 / 模型判定** 三层分治，本地只查前两层，**模型判定照录不改判**。
4. **SOP 增「轮 0 · 数据口径前置」**（04 / hybrid SKILL ⑤），并把数据质量门写入任务卡（板块状态扫描 / 公司深度研究 / 行业对比）。
5. **覆盖范围不再写死 62**：改为「按本次实际返回填写 + 注明口径来源」，并明确**四类名单重叠、不得相加**。

**验证**：`maintenance/expert-package-review/verify.py` 新增数据质量用例断言（`cases/03`、`cases/04`），
全部调用**生产函数**（`data_qc` / `right_side`），不另写测试逻辑。

## 2026-10-01 — v1.3.1（统一维护源 / 版本记录 / 验证入口）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.3.0 → 1.3.1` | **修正并入 + 可移植化 + 建立维护入口**（内容变更，非仅文案） |
| shenwa-agent | `1.1.4`（不变） | 本轮不在范围内 |

**背景（为什么会有这一版）**：`get_right_side` 的「先按 `date` 切截面、再按 `transition_type` 分桶」
这条修正（2026-09-30 实测）此前**只落在本机市场源与全局 skill**，
**仓库与实际加载的缓存副本都缺**。同时本机市场源的 `skills/shenwa/SKILL.md`
被全局 skill 的内容整份覆盖过，带进了 4 处写死本机绝对路径。
即：**内容最新的那份不可分发，可分发的那份缺修正。**

**本次变更**：

1. **修正并入维护源**：把「先切截面再分桶」（含 `Upgrade=11/Breakdown=9` → 正确 `2/0` 的实测对照）、
   原始输出首行 `// base:` 注释须先剔除、`Breakdown=0 ≠ 结构健康`、字段路径
   `state.state_mapping` / `state.veto_check` 等**逐字并入仓库** `skills/shenwa/SKILL.md`
   与 `references/04-单项任务SOP与任务卡.md`。
2. **清除写死本机路径（4 处 → 0 处）**：调用示例改为
   `cd <专家包目录>/skills/shenwa` + `PY="$(command -v python3 || command -v python)"`；
   一键复跑骨架改为 `PY = sys.executable` + `_skill_dir()` 运行期自定位
   （环境变量 → 本包 `skills/shenwa` → 全局 skill）。**本包不再依赖共享 skill 的绝对路径。**
3. **建立维护入口** `maintenance/expert-package-review/`：`README.md`（维护源 / 加载方式 / 共同约定）、
   `progress.md`（各轮范围与验收）、`cases/`（脱敏样例与预期值）、`verify.py`（可执行的验证入口）。
4. **统一 skill frontmatter 的版本记录**：`skills/shenwa-hybrid/SKILL.md` 的 `version` 跟随包版本
   （`1.3.0 → 1.3.1`）；`skills/shenwa/SKILL.md` 的 `version` 跟随同目录最新配置
   （`1.0.4 → 1.0.5`，其正文第 111 行本就写着已升级至 1.0.5）。两项均已纳入 `verify.py` 断言。

**未变**：能力域、任务路由、接口注册表、`shenwa_api.py`、配置文件、Agent 定义、模板、预设。
深挖服务端功能与接口算法本轮未触及。

**分发提示**：`dist/` 下的交付包仍是 `1.3.0` 内容（**含写死路径**），需在下一次分发前重新打包。

本市场采用**独立版本号**：两个专家各自演进，`marketplace.json` 中的 `version` 始终与各专家包内
`plugin.json` 的版本一致。订阅方执行 `/plugin marketplace update shenwa-marketplace` 即可获取新版。

## 2026-09-30（稍晚）— v1.1.4（shenwa-agent 描述文案改中文）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-agent | `1.1.3 → 1.1.4` | **仅文案**：`plugin.json` 的 `description` 由英文改为中文 |
| shenwa-hybrid-research | `1.3.0`（不变） | — |

**为什么改**：本机「我的专家」市场清单（`marketplaces/my-experts/.codebuddy-plugin/marketplace.json`）
的条目描述，是客户端从各包 `plugin.json` 的 `description` **回写**的 —— 该字段为英文时，
清单里就只有这一条显示英文（`shenwa-hybrid-research` 那条本就是中文）。**改本机清单无效，必须改 `plugin.json` 本身。**

**同时**：`description` 统一为与其他包一致的「显示名（英文名）：一句话」格式，
内容与 `displayDescription.zh` 对齐。能力、流程、接口、技能内容**均无变化**。
属原地升级（`shenwa-agent` 标识不变），「我的专家」里**不会多出条目**。

## 2026-09-30 — v1.3.0 / v1.1.3（结构化直取路径：补上路由缺失的第三个维度）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.2.3 → 1.3.0` | **新增 Q0.9「数据形态分流」**：横截面任务走 `get_right_side` 接口直取（0 点、秒级、不发报告）；tid=3002 边界收窄为「单板块纵深」；新增 A 档产出规范与「板块状态扫描」任务卡 |
| shenwa-agent | `1.1.2 → 1.1.3` | 同步 `skills/shenwa/SKILL.md`（两包共用同一份）——补 `get_right_side` 横截面全量扫描口径 |

**本次变更**：

### 一、问题：路由缺了「数据形态」这一维

原路由决策树只有**标的层级**（Q0）与**任务类型**（Q1~Q4）两个维度。于是「帮我找右侧确认的行业和概念」会被判为「板块级 → tid=3002」，**为每个板块各发一份报告**——`5 点 × N`，成本随标的数线性膨胀。而深挖其实有一个直接通道：

| 对比项 | tid=3002（右侧区间监测助手） | `get_right_side`（接口直取） |
|---|---|---|
| 覆盖范围 | 单次一份报告，实测挂载 10 个标的 | **一次 GET = 62 个行业与主题指数全量** |
| 产出形态 | 4,761 字叙述性报告 | **结构化 JSON**（阶段/迁移/否决/资格） |
| 耗时 | 实测 5.5 分钟（须发布 + 轮询） | **秒级** |
| 成本 | **5 点 / 次** | **0 点** |
| 扫 62 个概念 | 需 **N 次发布** | **1 次调用** |

### 二、改动：新增 Q0.9，把横截面任务引到直取通道

1. **新增 `Q0.9 · 数据形态分流`（C19）**，位置紧接 Q0 之后、Q1 之前（它是"发不发报告"的分水岭）：
   - 【横截面】命中「全市场 / 所有行业 / 所有概念 / 哪些板块 / 排名 / 分布」，或需横向比较 ≥ 3 个标的，或只要结构化字段 → **走结构化直取通道，★禁止逐标的发布报告**
   - 【纵深】单一标的（或 ≤2 个）深度解读 → 维持原 Q1~Q6 模板流程
   - 【混合】既要名单又要解读 → **两段式**：第 1 段 0 点直取筛名单，第 2 段只对 top-N（默认 1~3）发报告
2. **能力域清单补 S11**：`GET /api/market/getRightSide`（ADM/VIP2，参数 `codes` / `dayOffset` 1~20）。
3. **路由输出物新增两个字段**：「数据形态」（纵深 / 横截面）与「交付档」（A 档·结构化直取 / 标准档）。
4. **新增红线 12（两个专家包同步）**：横截面任务不得逐标的发报告。含三条附带规定——交付物必须显式标注覆盖范围为「62 个行业与主题指数」；无 ADM/VIP2 权限时如实告知、不得偷偷退回逐标的发报告；**与红线 1「数字分层采信」不冲突**（红线管深挖*报告正文*的叙述性数字，`get_right_side` 是*接口原始输出*，等同行情数据，属数据底座）。
5. **新增 A 档 · 结构化直取档**（`05-输出规范与校验表.md`）：**豁免六段结构**，标准产出 = 四张表 + 计数 + 阶段分布 + 迁移分布；并附 A 档专属自检清单。
6. **新增开箱任务卡** `templates/任务卡-板块状态扫描.md`：数据形态=横截面、成本=0 点、产出=四张表，含自检项与定时差分建议。
7. **tid=3002 适用边界收窄**（`08-深挖模板全量表.md`「发现 3·补」）：定位是「**把一个板块看透**」，不是「**把一批板块扫一遍**」。
8. **任务卡标准结构新增「数据形态」字段**（`04`），并新增「二·补 · 横截面任务 SOP」（6 步 + 两段式 + 三条硬边界）。
9. **面向客户的文档同步**：`QUICKSTART` 加「横截面 vs 纵深」两类典型问法与对照表；`06` 加「怎么问全市场扫描」一节 + 2 条常见问答；`agents/shenwa-hybrid-research.md` 补 Q0.9 分支、核心能力第 4 条与红线 10。

### 三、顺带修掉的两处文档缺陷（本次改动中发现的）

| # | 位置 | 问题 | 修正 |
|---|---|---|---|
| 1 | `04-单项任务SOP与任务卡.md` ④ 步 | 仍写着「**发布回执校验**：读取返回记录的 `stocks`」——与 C10「发布回执的 `stocks` **恒为 null**」直接矛盾，会误导执行 | 改为「**发布前静态层级拦截**」，并显式标注不要读回执 |
| 2 | `README.md`（仓库根） | 写「两个专家**每次会话启动**都会在后台探测版本」——v1.2.1/v1.1.1 已移除钩子，此处为残留旧说法 | 改为「会话首次响应前手动跑一条命令」 |
| 3 | `agents/shenwa-hybrid-research.md` | 「核心能力」列表出现两个 `4.`（编号重复） | 重排为 1~7 |

### 四、落地

- **属原地升级**：两个包 `name`（id）均未变，白名单 `experts.json` 不用动。
- **登记点全同步**：市场源 / 新增缓存版本目录（`1.3.0`、`1.1.3`）/ `installed_plugins.json` / 仓库与市场清单 `marketplace.json` / 热刷新。
- **重新打包分发**：`dist/` 两个 zip 重出，分发说明与 `安装说明.html` 的版本号、SHA256 同步更新。

> ⚠️ **覆盖范围提醒**：`get_right_side` 的 62 个指数是官方**精选池**，**不是"A 股全部概念板块"**（后者有数百个）。交付物与文档一律不得写成"全市场所有概念"。
> 🔐 **权限门槛**：`get_right_side` 与 `get_guaili_rates` 均需 **ADM / VIP2**。无权限时**如实告知**并说明可联系 `shenwa4119` 升级，**不得**偷偷退回"逐个板块发报告"。

## 2026-09-29（深夜）— v1.2.3 / v1.1.2（补齐状态机文档：技能漂移修复）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.2.2 → 1.2.3` | `skills/shenwa` 补齐 `get_right_side` 状态机文档，并写明「每日状态迁移扫描」产出形态 |
| shenwa-agent | `1.1.1 → 1.1.2` | 同上（两包共用同一份 `shenwa` 技能） |

**本次变更**：

1. **修复技能漂移**：仓库源已含的状态机文档（30 行）此前**只补进仓库、未同步到本机部署**——
   仓库版 `skills/shenwa/SKILL.md` 为 **326 行**，本机市场源与缓存版本仅 **296 行**。本次一并同步。
2. **补进的内容**：`state.state_mapping`（`current_state` ∈ **S0~S5**、`state_action`）、
   `state.qualification_summary`（`q_overall` / `q_pass_days`）、`state.veto_check`
   （**`has_veto` + 五类 `triggered_flags`**）、`state_trans.transition_type`
   （**`Hold` / `Upgrade` / `Breakdown` / `Unknown`**），以及五类否决标志表。
   此前部署版把 `state` 描述为只含 `as_of` / `evidence_facts` / `disclaimer` 的元信息，
   **专家因此完全不知道它是一台状态机**，无法主动产出迁移名单。
3. **新增产出形态约定**：✅ **纯表格四名单（客户已确认）** —— 「每日状态迁移扫描」只交付
   升级 / 破坏 / 临界 / 否决**四张表 + 计数**，**不下分析结论、不套六段结构、不发布深挖报告**；
   分桶口径一律以 `state_trans.transition_type` 为准（`state.state_mapping.state_action` 是单标的自身口径）。
4. **属原地升级**：`name`（id）未变，白名单 `experts.json` 不用动。
5. **本次未重新打包**：`dist/` 下的 zip 仍是上一版本；如需对外分发须先重打包（客户本次选择只在本机生效）。

## 2026-09-29（晚 · 补）— v1.2.2（介绍文案精简）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.2.1 → 1.2.2` | 介绍文案精简：`displayDescription.zh` 91 字 → **49 字**，官方 `validate_expert.py` 的唯一软提示（建议 40-50 字）已消除 |
| shenwa-agent | `1.1.1` | 不变 |

**本次变更**：

1. **卡片简介精简**：`plugin.json` 的 `description` 与 `displayDescription.zh` 同步改为
   「报一家公司、一个行业或一件事，深挖写分析、数据源逐条核数字，交你带核对表的报告，对不上的一律作废。」
   （英文 `displayDescription.en` 同步精简。）
2. **属原地升级**：`name`（id）未变，白名单 `experts.json` 不用动，订阅方 `marketplace update` 即可拿到。
3. **改的字段说明**：`displayDescription.zh` 是「我的专家」卡片简介；`description` 会被客户端回写进本机
   `my-experts/marketplace.json`，两处必须一起改（只改本机清单无效，会被客户端覆盖回去）。

> ⚠️ 改介绍文案**不要动 `profession`**——它是分享链接 `expertname` 参数的来源，一改已分发链接的语义就漂移。

## 2026-09-29（晚）— v1.2.1 / v1.1.1（移除钩子 + 同步深挖接口 1.0.5）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.2.0 → 1.2.1` | **移除 `hooks/`**；版本检查改为手动 SOP；同步深挖接口 1.0.4 → 1.0.5 |
| shenwa-agent | `1.1.0 → 1.1.1` | 同上 |

**本次变更**：

1. **移除 `hooks/` 会话启动钩子（合规）**：官方校验器 `validate_expert.py` 明确把 `hooks/` 列为专家包**禁止目录**
   （`Forbidden directory found: hooks/`），官方打包脚本 `package_expert.py` 会先校验、不通过即**中止打包**。
   为避免接收方导入时被安全检测拒绝，v1.2.0 引入的钩子已**彻底移除**，包结构回归官方规范。
   同步移除了 `hooks/` 在插件缓存各版本目录中的副本。
2. **版本检查改为手动 SOP**：会话首次响应前跑一次 `shenwa_api.py latest_version`，与本地
   `shenwa_config_<版本>.json` 比对；发现新版主动提示用户。**「只报告、不自动覆盖」的设计不变**——
   官方至今仍把 `inviteCode` 标为必填，覆盖会使「反复索要邀请码」复发。
3. **同步深挖接口 1.0.4 → 1.0.5**：新增 `get_right_side`（右侧确认报告，返回结构化 JSON，含 q1~q5 检查点）。
   - 🔴 **官方配置的路径是错的**：写成 `/api/api/market/getRightSide`（双 `api`），主备两站实测均 **404**；
     真实路径 **`/api/market/getRightSide`**（实测 HTTP 200）。本仓库按**实测值**实现。
   - ⚠️ 官方配置的响应 schema 也写错了（写成 `data[].values[]` 嵌套，实测是扁平结构 `data[]`）。
   - ⚠️ 官方 1.0.5 配置文件的 JSON 本身不合法（`success_schema` 里有 4 处未加引号的占位符），解析前须先补引号。
   - 免邀请码补丁 `_local_patch` 已在 1.0.5 上**重打**。
4. **顺带修掉 `scripts/sync.py` 一处 bug**：`sync_marketplace_versions()` 原先被放在 `if not args.no_bump` 里，
   导致 `--no-bump` 时连版本对齐也一并跳过。现改为**无条件对齐**（对齐本身幂等）。
5. **补全接口注册表**：`shenwa_api.py` 此前漏注册 `download_version`（`GET /versions/shenwa_config_{version}.json`），
   实际端点总数应为 **15** 个。本次补齐，并为路径模板 `{version}` 增加通用替换逻辑（缺参时报错而非静默拼错）。
   - 官方 1.0.5 的配置 JSON 语法不合法，硬解析会失败 → 该接口新增 **JSON 解析容错**：
     解析失败时返回 `{_json_parse_error, _hint, _raw_text}`，并可用 `out=<路径>` 直接落盘原文（实测 44,588 bytes）。
   - 三个 `shenwa_api.py` 副本统一为同一 md5（`8732e377`），配置副本统一为 `0c2408ac`。
   - 实测三条路径均正常：`version=1.0.5 out=...` 落盘成功；无 `out` 时返回容错结构不崩溃；`version=1.0.4` 返回合法 JSON。

> 判断路径与字段是否可用，**唯一可信依据是实测接口**，不是官方配置文件、也不是文档。

## 2026-09-29 — v1.2.0 / v1.1.0（深挖接口升级自动检测）

> ⚠️ **本条目所述的 `hooks/` 钩子已在 v1.2.1 中移除**（官方禁止专家包带 `hooks/`），详见上一条。

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.1.1 → 1.2.0` | 新增会话启动钩子，自动探测深挖官方接口是否有新版本；发现新版在工作区落《版本检查报告》。**只报告、不自动改动任何文件。** |
| shenwa-agent | `1.0.6 → 1.1.0` | 同上，两个专家包均内置该钩子。 |

**本次变更**：

1. **新增 `SessionStart` 钩子**（`hooks/hooks.json` + `hooks/shenwa-version-check.mjs`，两个包各一份、内容相同）：
   - 每次会话启动时**后台异步**（命令尾部 `&`）请求 `GET /api/manage/getLatestVersion`（主站失败自动切备用站 `http://8.130.17.247:8088`），8 秒超时。
   - 与本地已跟踪版本（技能目录里的 `shenwa_config_<版本>.json`）比对；**仅当官方版本更高**时，写出 `<工作区>/深挖版本跟踪/版本检查报告.md`。
   - 不阻塞会话、不改动任何技能文件、网络异常静默吞掉（日志落在系统临时目录 `shenwa-version-check.log`）。
   - 零外部依赖，仅用 Node 内置模块（WorkBuddy 自带 Node 22）。
2. **为什么只报告、不自动升级（刻意设计）**：自动覆盖官方最新配置会抹掉本地补丁，其中 🔴 **官方（截至 1.0.5）仍把 `inviteCode` 标为必填**——覆盖后「反复索要邀请码」立刻复发，而新用户根本没有邀请码。此外还会抹掉本机路径脱敏与 `shenwa_api.py` 接口注册表扩展。因此升级动作**必须人工确认**。
3. **报告内容**：版本对照表、为什么要人工确认（含邀请码红线）、5 步升级步骤（下载 → **先修官方 JSON 语法缺陷** → 补丁式合并 → 同步全部 3 份副本 → 两个包升版重打包）、相关红线。
4. **专家侧**：`shenwa-hybrid/SKILL.md` 新增「十、深挖接口升级自动检测」章节，要求专家在会话开始时检查该报告并主动告知用户。
5. **版本**：`shenwa-hybrid` skill `1.1.1 → 1.2.0`；`shenwa-hybrid-research` 插件 `1.1.1 → 1.2.0`；`shenwa-agent` 插件 `1.0.6 → 1.1.0`。属 `--minor`（新增功能）。

## 2026-09-29 — v1.1.1（真深挖综合研究 · 改名 + 介绍通俗化）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.1.1` | 显示名由「真深挖混合投研」改回「**真深挖综合研究**」；四处介绍文案全部改写为面向普通用户的大白话；顺带修掉仓库 README 与凭据模板里残留的「邀请码必填」旧说法。 |

**本次变更**：

1. **改名（显示层）**：`displayName.zh` 「真深挖混合投研」→「**真深挖综合研究**」，`displayName.en` → `True Shenwa Integrated Research`；标签「混合投研」→「综合研究」。
   - ⚠️ 插件 id（`name`）保持 `shenwa-hybrid-research` **不变** → 已安装者原地升级，列表不会多出条目；白名单与安装记录无需变更。
   - ⚠️ `profession`（职称）保持「综合投研分析师」不变 → **分享链接的 `expertname` 参数不受影响**；但旧链接指向旧快照，改名后需**重新生成分享链接**才会显示新名称。
2. **介绍通俗化（四处同步）**：卡片简介 `displayDescription`、市场描述 `description`、Agent 开场白、包内 README 顶部；去掉了 50/50、tid=2001、SOP 等术语，改为「写分析 / 核数字 / 给交代」三段式，并把 v1.1 三项能力（免邀请码、自动切备用站、财报取数据源）用大白话表述。
   - 内部操作口径**未改动**：`agents/*.md` 的核心能力与红线、`skills/shenwa-hybrid/` 全部 references/templates 原样保留，仍以 50/50、tid=2001 等术语描述。
3. **顺带修复**：仓库根 `README.md` 第 2 步与 `shenwa_credentials.example.json` 仍写「手机号 + 邀请码」——与 v1.1 起「只需手机号」的实际行为矛盾，已改为只需 `userID`。
4. **版本**：`shenwa-hybrid` skill `1.1.0 → 1.1.1`；插件 `1.1.0 → 1.1.1`。属 `--patch`（改文案与显示层，无功能变更）。

## 2026-09-29 — v1.1.0（真深挖混合投研）

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-hybrid-research | `1.1.0` | 显示名由「真深挖综合研究」改为「**真深挖混合投研**」；新增三处规则（如下）。 |

**三处规则变更**：

1. **接入规则（长期生效，优先级最高）**
   - 深挖**只通过 API 访问**，任何情况下不得用浏览器抓取或访问；
   - 登录**只需注册手机号（userID）**，**不需要邀请码**——不得向使用者索要邀请码，也不得因缺邀请码阻断流程；
   - 主站 `https://www.shenwa.top` 不可达时，**自动切换备用站 `http://8.130.17.247:8088`**，无需人工确认；
   - 连续调用异常时在主备站间重试退避，仍失败只上报「无法获取状态」，不得谎报"报告失败"。
   - 落点：`agents/shenwa-hybrid-research.md` 红线 6、`shenwa-hybrid/SKILL.md` 红线 10 + 能力域、`references/01-模式总纲.md` 红线 10、`references/02-能力分工与路由.md`、`references/06-客户使用与修正指南.md`、`README.md`、`QUICKSTART-一页上手卡.md`。

2. **模板择优复核（tid=2001）**
   - 新增路由步骤 **Q6**：确定 tid 之前，必须把 **tid=2001（智能深度推理）** 与初选模板正面比较一次；
   - 任务跨多个分析维度 / 需求描述偏模糊 / 初选需发布多份拼凑 → **改用 2001**；初选在该维度上明显更专（9=偏公司估值、7=偏财务风险、8=偏新闻影响、1001=巴菲特视角、3002=板块右侧监测）→ **保留初选**；
   - **无论选谁，比较结论必须写入任务卡「模板选型」栏**。
   - 落点：`agents`（核心能力 3 + 红线 9）、`shenwa-hybrid/SKILL.md`（③ 路由 Q6 + 模板速查 ⑤）、`references/02-能力分工与路由.md`（决策树 Q6 + 模板全量表发现 5 + 路由输出物）、`references/08-深挖模板全量表.md`（新增「发现 5」）、`references/03A-偏好选项库.md`、`references/04-单项任务SOP与任务卡.md`（任务卡标准结构）、**5 张任务卡全部新增「模板择优复核」栏**。

3. **财报数据优先取数据源**
   - **财报类数据的第一来源是数据源（D2）**：三大报表科目、财报衍生指标（同比环比、周转率、负债率、覆盖倍数）、由财报科目直接算出的财务比率，**取数入口只能是数据源**；
   - 深挖正文中的同类数字**仅作对照物**（填入校验表「深挖正文值」列），**不得作为取数来源**，也不得因"深挖已给出"而跳过数据源取数；
   - 不改变"深挖原文完整引用"——正文照录，但财报数字一律从数据源取，两者并行不冲突。
   - 落点：`agents`（核心能力 2 + 红线 1）、`shenwa-hybrid/SKILL.md`（数字分层采信表 + ④ 数据源侧 + ⑤ 校验）、`references/01-模式总纲.md`（红线 1 采信表）、`references/02-能力分工与路由.md`（D2 + 深挖不承担边界）、`references/05-输出规范与校验表.md`（新增专节 + 交付前检查项）、`references/03A-偏好选项库.md`、`references/04-单项任务SOP与任务卡.md`、5 张任务卡 D2 行、`README.md`、`QUICKSTART-一页上手卡.md`。

**版本与兼容性**：

- 插件 id 保持 `shenwa-hybrid-research` **不变** → 已安装者原地升级，列表中**不会**多出一个条目。
- `shenwa-hybrid` skill 版本号 `1.0.1 → 1.1.0`；内嵌的 `shenwa` skill 版本保持 `1.0.4`（跟随上游深挖 skill，未改动其代码与补丁）。
- 版本号约定中本次属 `--minor`（新增规则与能力）。

## 2026-09-26 — 首次发布

| 专家 | 版本 | 说明 |
|---|---|---|
| shenwa-agent（深挖AI） | `1.0.5` | 完成凭据外置改造：包内不再内置任何账号，改为读取使用者本地的凭据文件 / 环境变量。 |
| shenwa-hybrid-research（深挖混合投研模式） | `1.0.2` | 同上。 |

**凭据外置改造要点**（两个包一致）：

- 删除脚本内硬编码的账号，新增 `load_cred()`，按「环境变量 → 包外凭据文件 → 明确报错」顺序解析，**不会回退到任何内置账号**。
- 文档中的明文手机号 / 邀请码改为占位符，并新增「凭据配置」章节。
- 修正与实测不符的旧描述：轮询上限 24 次 → **40 次 / 20 分钟**；`text` 字段由「可空」改为 **必填**；生成耗时预期改为 **1~5 分钟，个别模板可能超过 10 分钟**。

## 版本号约定

- `--major`：不兼容变更（如 skill 改名、凭据机制变更）
- `--minor`：新增能力或模板
- `--patch`：修 bug、改文档、调措辞（默认）

改完专家后执行 `python scripts/sync.py`，脚本会自动升 patch 版本、同步 `marketplace.json` 并做凭据扫描。

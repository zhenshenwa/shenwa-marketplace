# -*- coding: utf-8 -*-
"""capability.py · 能力清单（本包唯一的「能力边界」事实源）

v1.7.0 新增。解决的问题：
  · 原文档里 D1~D8 只有「编号 + 类别 + 示例 Skill」三列，**没有边界**：
    用途 / 输入 / 输出 / 对象层级 / 覆盖范围 / 必要依赖 / 权限 / 已验证程度 / 缺失时处理 全缺；
  · 于是「有画像名称」被当成「完整具备」（量化、回测、跨市场），
    「文档写了」被当成「已验证支持」，「有接口」被当成「无门槛可用」；
  · D4 一个编号同时代表「公告与事件」「技术指标」「可视化」三种东西。

本模块把上述全部写成**可执行数据**，供路由层与校验层调用。
不联网、不调用任何外部接口。

状态四态（`STATUS`）：
  verified_supported  已验证支持  —— 有实测证据，可直接承诺
  conditional         条件支持    —— 有实现但有前提（权限 / 口径 / 时效），须先满足前提
  unverified          未验证      —— 存在但不能承诺；**未验证 ≠ 不存在**
  unavailable         不可用      —— 无可执行依据，明确不做

CLI:
  python capability.py selftest
  python capability.py list [--status S]
  python capability.py show D4
  python capability.py boundary          # 本地保存 vs 服务端
  python capability.py verify-offline    # 量化 / 回测 / 跨市场 的实际链路
  python capability.py numbering         # D 编号冲突与解法
"""
from __future__ import annotations

import json
import sys

# ═══════════════════════════════════════════════════════════════════
# 0. 状态与常量
# ═══════════════════════════════════════════════════════════════════

STATUS = {
    "verified_supported": "已验证支持（有实测证据，可直接承诺）",
    "conditional": "条件支持（有实现但有前提：权限 / 口径 / 时效，须先满足前提）",
    "unverified": "未验证（存在但不能承诺；未验证 ≠ 不存在）",
    "unavailable": "不可用（无可执行依据，明确不做）",
}

STATUS_ORDER = ["verified_supported", "conditional", "unverified", "unavailable"]

# 九项必备属性（要求 1）。任一缺失即视为清单不完整。
REQUIRED_FIELDS = (
    "purpose",       # 用途
    "inputs",        # 输入
    "outputs",       # 输出
    "object_level",  # 对象层级
    "coverage",      # 覆盖范围
    "depends_on",    # 必要依赖
    "permission",    # 权限
    "status",        # 已验证程度
    "on_missing",    # 缺失时处理方式
)

# ⚠️ 无可执行依据、**禁止承诺**的说法（要求 4）。
# 命中即在交付物中出现即视为违规——不是「建议避免」，是「不得写」。
FORBIDDEN_PROMISES = {
    "私有发布": "wfStartPublish 无可见性参数，无法指定发布为 private；private 只是 report_status 的服务端结果态",
    "服务端不保存": "发布即入库，无「发布但不保存」参数；只能发布后调用 delete_report 删除",
    "删除即彻底清除": "删除是否彻底、是否残留服务端副本无证据，不得承诺（`delete_report` 存在，但彻底性无证据）",
    "全市场所有概念": "codes 留空覆盖官方精选池（实测 62 个行业与主题指数），不是 A 股全部概念板块",
}

# ═══════════════════════════════════════════════════════════════════
# 1. 深挖侧能力（S）
# ═══════════════════════════════════════════════════════════════════

def _c(**kw):
    return kw


SHENWA_CAPS = [

    _c(id="S1", name="深度报告生成",
       purpose="按模板异步生成结构化研究报告（本模式下「分析主体」的第一来源）",
       inputs="tid（模板，**必须字符串**）、question（≤60 字）、text（背景消息，**实为必填**）",
       outputs="aid（报告标识，含 @@ 分隔符，须整串保存）",
       object_level="随所选模板（个股 / 板块 / 行业 / 产业链 / 宏观）",
       coverage="由所选模板决定；本包实测覆盖 17/17 个模板",
       depends_on="S10（余额与有效期）；A1 画像不决定模板，对象层级决定模板",
       permission="全部（扣深挖点，按模板 cost 计）",
       status="verified_supported",
       on_missing="余额不足 → 如实提示充值与余额；发布失败 → 报出 `status=SRV/SUT` 原文，**不得**改发一份不存在的报告充数"),

    _c(id="S2", name="报告详情读取",
       purpose="读取报告全文（**归档层**：完整保留，不摘要）",
       inputs="aid",
       outputs="报告正文 / 标题 / aid / 时间",
       object_level="同报告",
       coverage="同报告",
       depends_on="报告已完成（S3）",
       permission="全部",
       status="verified_supported",
       on_missing="报告未完成 → 先轮询；已 error → 报失败，不读半成品"),

    _c(id="S3", name="报告状态轮询",
       purpose="确认报告生成完成",
       inputs="aid",
       outputs="data.status ∈ {inprogress, public, private, error}",
       object_level="—",
       coverage="—",
       depends_on="—",
       permission="全部",
       status="verified_supported",
       on_missing="**调用异常（超时/非 JSON/5xx）≠ 报告失败**；连续 5 次异常切站点，仍失败只报「无法获取状态」"),

    _c(id="S4", name="模板列表",
       purpose="按类型拉取模板清单，决定报告深度",
       inputs="type",
       outputs="模板清单（tid / 名称 / cost）",
       object_level="—",
       coverage="官方模板库",
       depends_on="—",
       permission="全部",
       status="verified_supported",
       on_missing="取不到 → 用本包**已实测的 17 个模板表**，并注明是本地实测值"),

    _c(id="S5", name="报告列表",
       purpose="查公共 / 自己 / 收藏 / 历史报告，复用以往研究",
       inputs="type ∈ {public, self, collect, history}、num、after（翻页，hasNext=true 时取当前最小 time）",
       outputs="报告列表（含 aid / stocks / time）",
       object_level="—",
       coverage="本账号可见范围（public 为公共池，self 为自己发布的）",
       depends_on="—",
       permission="全部",
       status="verified_supported",
       on_missing="空列表 = 该范围内的空结果，**不等于「服务端没有保存」**"),

    _c(id="S6", name="PDF 下载",
       purpose="下载深挖官方已排版的 PDF",
       inputs="aid",
       outputs="PDF 文件",
       object_level="同报告",
       coverage="同报告",
       depends_on="报告已完成",
       permission="全部",
       status="verified_supported",
       on_missing="官方未提供 → 用完整原文排版，**且不改正文**"),

    _c(id="S7", name="报告收藏",
       purpose="把报告沉淀到收藏库（在服务端标注，不是本地另存）",
       inputs="aid、action=collect / 取消",
       outputs="操作结果",
       object_level="—",
       coverage="本账号收藏库",
       depends_on="—",
       permission="全部",
       status="verified_supported",
       on_missing="失败 → 明确报出，**不得**声称「已收藏」"),

    _c(id="S8", name="乖离率数据",
       purpose="取乖离率序列（情绪与择时辅助）",
       inputs="date、maxDays",
       outputs="乖离率序列（含 bias_rate_20d 等）",
       object_level="板块 / 行业指数",
       coverage="由参数决定",
       depends_on="—",
       permission="**VIP2**",
       status="conditional",
       on_missing="无权限 → `status=AUT`，**如实告知无 VIP2**，并说明可升级路径；**不得**退回「逐个发报告」冒充完成"),

    _c(id="S9", name="知识库检索",
       purpose="检索新闻、脱水券商研报、卖方报告",
       inputs="keyword（**走 query string**，发 JSON body 会 400）",
       outputs="检索条目",
       object_level="任意（按关键词命中）",
       coverage="**检索命中范围**（不是全市场、不是全卖方共识）",
       depends_on="—",
       permission="**VIP2**",
       status="conditional",
       on_missing="无权限 / 命中为空 → **收缩交付范围**并说明；**不得**把有限检索样本写成「市场共识」"),

    _c(id="S10", name="用户信息",
       purpose="发布前检查深挖点余额与有效期、账号类型",
       inputs="—",
       outputs="user（含账号类型 / restPoint / expireTime）",
       object_level="—",
       coverage="本账号",
       depends_on="—",
       permission="全部",
       status="verified_supported",
       on_missing="取不到 → 发布前按未知处理，**不得**假定余额充足"),

    _c(id="S11", name="右侧确认结构化数据（横截面直取）",
       purpose="一次调用返回全量行业与主题指数的**右侧状态机**（阶段 / 迁移 / 否决 / 资格）",
       inputs="codes（行业/主题指数代码，逗号分隔；**留空 = 全量**）、dayOffset（**实测 1~20**，缺省 2）",
       outputs="扁平数组：每项 {date, code, name, data}；data 是**再一层 JSON 字符串**，内含 q1~q5 / state / state_trans",
       object_level="**板块 / 行业指数（指数级）** —— **不是个股**",
       coverage="官方**精选池**（实测 2026-09-30 为 **62** 个行业与主题指数 × dayOffset 天）；**不是 A 股全部概念板块**",
       depends_on="—",
       permission="**ADM 或 VIP2**",
       status="conditional",
       on_missing="无权限 → `status=AUT`，**如实告知**；**不得**私自退回「逐个板块发 tid=3002」（5 点 × N，且用户以为在做扫描）；`data==[]` 是空结果 ≠ 无异常"),

    _c(id="S12", name="报告删除",
       purpose="删除自己发布的报告（v1.7.0 登记进清单）",
       inputs="aid",
       outputs="操作结果",
       object_level="—",
       coverage="仅限本账号自己发布的报告",
       depends_on="—",
       permission="全部",
       status="conditional",
       on_missing="删除是否彻底、是否残留服务端副本**无证据** → **不得**据此承诺「服务端不保存」；`E3=discard` 只能实现为「发布后删除」"),
]

# 接入 / 运维（非研究能力，不进 S 编号，避免与能力编号混淆）
UTILITY_CAPS = [
    _c(id="U1", name="登录 / Token", purpose="手机号换 browserCode，Token 本地持久化",
       inputs="userID（注册手机号）", outputs="browserCode / .shenwa_token",
       object_level="—", coverage="—", depends_on="—", permission="全部",
       status="verified_supported", on_missing="401 → 自动重登；**不需要邀请码、不需要密码**"),
    _c(id="U2", name="版本检查", purpose="查官方最新版与配置文件路径",
       inputs="—", outputs="latestVersion / configFilePath",
       object_level="—", coverage="—", depends_on="—", permission="全部",
       status="verified_supported",
       on_missing="**官方升版不会自动同步**（硬编码注册表）→ 手工比对，**只报告不覆盖**"),
    _c(id="U3", name="版本配置下载", purpose="下载官方版本配置文件原文",
       inputs="version", outputs="配置 JSON 原文",
       object_level="—", coverage="—", depends_on="—", permission="全部",
       status="conditional",
       on_missing="官方 1.0.5 / 1.0.6 配置**本身 JSON 不合法**（占位符未加引号）→ 须容错解析，**不得**整份覆盖本地免邀请码补丁"),
]

# ═══════════════════════════════════════════════════════════════════
# 2. 数据源侧能力（D1~D9）
# ═══════════════════════════════════════════════════════════════════
# 编号口径（v1.7.0 统一，解决 D4 三义冲突）：
#   D1~D8 为原契约编号，含义**保持不变**；
#   新增 D9 = 技术指标（原被误标为 D4 的全部分必须改指 D9）。

DATA_CAPS = [

    _c(id="D1", name="行情与估值",
       purpose="价格、涨跌、市值，以及 PE/PB/PS 与估值分位",
       inputs="标的代码、日期", outputs="行情字段、估值倍数、历史分位",
       object_level="个股 / 指数 / 板块",
       coverage="沪深北（跨市场 HK/US 见 verify-offline）",
       depends_on="—", permission="全部",
       status="verified_supported",
       on_missing="取不到 → 标「未知」，**不补造**；未来日期会**静默返回旧数据**，必须比对 `time` 字段"),

    _c(id="D2", name="财务报表（财报类数据的第一来源）",
       purpose="三表关键科目、同比环比、财务指标",
       inputs="标的代码、报告期", outputs="科目值、财务比率",
       object_level="个股",
       coverage="A 股（实测）",
       depends_on="—", permission="全部",
       status="verified_supported",
       on_missing="深挖正文同类数字**仅作对照物**，不得反当取数来源；缺数据 → 记未知，不补造"),

    _c(id="D3", name="资金流向",
       purpose="主力 / 北向 / 龙虎榜、成交结构",
       inputs="标的代码、日期", outputs="净流入、持仓变动、龙虎榜明细",
       object_level="个股 / 板块",
       coverage="A 股",
       depends_on="—", permission="全部",
       status="conditional",
       on_missing="**退出码恒为 0**：失败时返回纯文本而非 JSON → 必须自检输出内容；不支持的市场返回提示文本，不得当作空数据"),

    _c(id="D4", name="公告与事件",
       purpose="公告、分红、股东变动、重大事项",
       inputs="标的代码、limit", outputs="公告条目（标题 / 时间 / 类型）",
       object_level="个股",
       coverage="A 股（实测取到 1826 条）",
       depends_on="—", permission="全部",
       status="verified_supported",
       on_missing="公告须**独立确认**后才可作事件驱动依据；取不到 → 标未知，不得据传闻补写"),

    _c(id="D5", name="研报与舆情",
       purpose="卖方评级、研报观点、新闻舆情",
       inputs="关键词", outputs="条目与观点",
       object_level="任意",
       coverage="**检索命中范围**，不是全卖方共识",
       depends_on="—", permission="全部",
       status="unverified",
       on_missing="**未独立验证**：存在「假成功」现象（乱码 query 仍返回 code=200 但回填错误实体）→ 必须校验 `apiRecall`/`docRecall` 同时空 + 实体匹配；缺数据 → 收缩范围，**不输出本包自行统计的总体百分比**"),

    _c(id="D6", name="标的筛选",
       purpose="按条件 / 策略 / 标签批量选股选基、截面排序",
       inputs="筛选条件 / 策略 / 排序字段", outputs="候选清单 + 关键指标",
       object_level="全市场（A 股）",
       coverage="A 股（服务端计算）",
       depends_on="—", permission="全部",
       status="conditional",
       on_missing="**服务端计算，未在本包独立复算** → 用于 Top-N 时**必须写明排序标准**；取不到 → 只给筛选维度建议，不编清单"),

    _c(id="D7", name="宏观与行业数据",
       purpose="宏观指标、行业经营数据、产业链图谱，含**板块估值分位**",
       inputs="指标 / 板块", outputs="时间序列、分位",
       object_level="宏观 / 行业 / 板块",
       coverage="宏观与行业（板块估值为 westock 独有）",
       depends_on="—", permission="全部",
       status="conditional",
       on_missing="**两源口径打架**（板块 PE 差 16.8%、PB 差 32%），且 **neodata 分位返回空** → 相对估值类方法**只停用该类**，绝对法（FCFF/FCFE/股息率）照常按边界输出"),

    _c(id="D8", name="结构化整理",
       purpose="数据清洗、透视、表格与图表生成",
       inputs="数据集", outputs="表格 / 图表 / 透视结果",
       object_level="任意",
       coverage="本地处理",
       depends_on="—", permission="全部",
       status="verified_supported",
       on_missing="不可用 → 手工整理并注明"),

    _c(id="D9", name="技术指标",  # ★ v1.7.0 新增，解决 D4 冲突
       purpose="由行情派生的技术面指标：均线 / 乖离率 / MACD / RSI / KDJ / 布林带 / ATR / 量能",
       inputs="标的代码、区间", outputs="指标序列与当前值",
       object_level="个股 / 指数",
       coverage="A 股",
       depends_on="D1（行情的派生量）", permission="全部",
       status="conditional",
       on_missing="**服务端内置计算，禁止用 K 线手算**；未逐项独立复算 → 用于结论时须标注来源与口径；乖离率**不含前低价信息**，**不得**据此宣布支撑位未破"),
]

ALL_CAPS = SHENWA_CAPS + UTILITY_CAPS + DATA_CAPS
_CAP_INDEX = {c["id"]: c for c in ALL_CAPS}

# ═══════════════════════════════════════════════════════════════════
# 3. 编号冲突与解法（要求 3）
# ═══════════════════════════════════════════════════════════════════

NUMBERING = {
    "conflict": "「D4」在原文档中同时代表三种东西：① 数据能力「公告与事件」（02 / SKILL / 04）"
                "；② 数据能力「技术指标」（趋势异动跟踪 / 量化因子分析 / 04 的数据清单）；"
                "③ 偏好选项「可视化」（03A / 03B，`output.include_visual`）。",
    "root_cause": "偏好选项组（A/B/C/D/E）与数据能力编号（D1~D8）**共用字母 D**，"
                  "且技术指标从未获得自己的编号，被就近写成了 D4。",
    "resolution": [
        "① 数据能力编号 D1~D8 **含义保持不变**（向后者兼容，不改语义）；",
        "② **新增 D9 = 技术指标**，原被误标为 D4 的全部位置改指 D9；",
        "③ **偏好选项组 D 更名为 F**（F1~F6，产出形态）——该字母只是**人读标签**，"
        "配置字段（`output.*` / `datasource.*`）不变，故是纯标签变更，不破坏配置兼容性。",
    ],
    "unchanged": "历史记录（examples/ 下的原始任务卡与报告）**一律不改写**——它们记录的是当时的口径。",
    "canonical": {
        "数据能力": {c["id"]: c["name"] for c in DATA_CAPS},
        "深挖能力": {c["id"]: c["name"] for c in SHENWA_CAPS},
        "偏好选项组": {"A": "投资风格与策略", "B": "分析口径", "C": "覆盖范围",
                       "F": "产出形态（原 D 组，v1.7.0 更名）", "E": "深挖侧"},
    },
}


def numbering_note() -> dict:
    return json.loads(json.dumps(NUMBERING, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════
# 4. 本地保存 vs 服务端（要求 4）
# ═══════════════════════════════════════════════════════════════════

STORAGE_BOUNDARY = {
    "question": "本地保存与服务端的「公开 / 私有」是不是一回事？",
    "answer": "**不是一回事，且不能互相承诺。**",
    "local_save": {
        "what": "报告原文由本包落在工作区，形成「归档件」（完整保留全文，含已确认错误段落）",
        "control": "**完全本地可控**",
        "evidence": "本包归档写入路径与文件名由本地流程决定（见 05-输出规范「归档件」节）",
        "status": "verified_supported",
    },
    "server_state": {
        "what": "`report_status` 返回 `public` / `private` / `error`；`report_list` 的 `type` 可取值 `public` / `self` / `collect` / `history`",
        "control": "**服务端结果状态，不是客户端选项**",
        "evidence": "`POST /api/manage/wfStartPublish` 的参数为 tid / question / text，"
                    "**无任何可见性（visibility / public / private / scope）参数**；"
                    "脚本 `shenwa_api.py` 的 APIS 注册表同样无该参数",
        "status": "verified_supported",
    },
    "cannot_promise": [
        "**不能承诺「私有发布」** —— 没有可指定的发布参数；`private` 是服务端给出的结果态。",
        "**不能承诺「服务端不保存」** —— 发布即入库，没有「发布但不落库」的参数。",
        "**不能承诺「删除即彻底清除」** —— 有 `delete_report`，但删除是否彻底、是否残留副本**无证据**。",
    ],
    "executable_alternative": {
        "想要私有可见": "在交付物中**本地归档**（可控），并说明服务端可见范围由服务端决定、本包不承诺。",
        "想要不落服务端": "**做不到**。可选的最近似动作是发布后调用 `delete_report`，"
                          "须**如实告知**「已发布并随后删除，删除彻底性无证据」。",
    },
    "preference_mapping": "偏好项 `E3 archive_mode` 的 `private` / `public` / `discard` 三档，"
                          "**只有 `discard` 需要额外说明**：它无法实现为「服务端不保存」，"
                          "只能实现为「发布后删除」。",
}


def storage_boundary() -> dict:
    return json.loads(json.dumps(STORAGE_BOUNDARY, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════
# 5. 量化 / 回测 / 跨市场 的实际链路（要求 5）
# ═══════════════════════════════════════════════════════════════════

SPECIAL_CHAINS = {
    "量化 / 因子": {
        "claim": "「量化因子」画像**只有画像名称，不等于完整具备量化能力**。",
        "chain": ["D2 财务质量因子原始科目", "D1 估值倍数与历史分位",
                  "D9 动量 / 波动率 / 技术类因子", "D3 流动性 / 资金面因子",
                  "D6 股票池构建与截面排序", "D8 因子表与分位整理"],
        "supported": "**因子暴露与历史 / 同业分位**（可拼装，属于条件支持）",
        "not_supported": ["**回测**（无净值 / 组合 / 换手成本链路）",
                          "**多因子回归 / IC 计算**（本包无实现，服务端无对应接口）"],
        "status": "conditional",
        "honesty_rule": "**不得因存在「量化因子」画像名称就声称完整具备量化能力**；"
                        "产出限于「因子暴露表 + 分位 + 经济逻辑解释」。",
    },
    "回测": {
        "claim": "本包**不提供回测**。",
        "chain": [],
        "supported": None,
        "not_supported": ["历史净值回放", "交易成本建模", "组合再平衡", "绩效归因"],
        "status": "unavailable",
        "honesty_rule": "不得以任何措辞暗示具备回测能力；被问到时如实说明并给替代（因子暴露 + 分位）。",
    },
    "跨市场": {
        "claim": "跨市场**仅有配置项，未经实测**。",
        "chain": ["偏好 C1 = [A] / [A,HK] / [A,HK,US]"],
        "supported": "**仅 A 股**为已验证支持",
        "not_supported": ["港股财务 / 行情（未实测）", "美股财务 / 行情（未实测）",
                          "AH 溢价对比（未实测）"],
        "status": "unverified",
        "honesty_rule": "被问及港美股时，**先声明未验证**，再说明可尝试、结果不作承诺；"
                        "**不得**因配置项支持 `[A,HK,US]` 就宣称具备跨市场分析能力。",
    },
}


def special_chains() -> dict:
    return json.loads(json.dumps(SPECIAL_CHAINS, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════
# 6. 查询接口
# ═══════════════════════════════════════════════════════════════════

def all_capabilities():
    return [dict(c) for c in ALL_CAPS]


def get(cap_id: str) -> dict | None:
    c = _CAP_INDEX.get(cap_id)
    return dict(c) if c else None


def by_status(status: str):
    if status not in STATUS:
        raise ValueError(f"未知状态 {status!r}；应为 {list(STATUS)}")
    return [dict(c) for c in ALL_CAPS if c["status"] == status]


def missing_fields(cap: dict):
    return [f for f in REQUIRED_FIELDS if not cap.get(f)]


def is_complete(cap: dict) -> bool:
    return not missing_fields(cap)


def capabilities_for(metric_family: str):
    """按指标族给出候选能力（路由层用）。

    ⚠️ 这是**唯一**能把需求映射到能力的入口；路由层不得自行硬编码能力名。
    """
    fam = (metric_family or "").strip()
    table = {
        "right_side_state": ["S11", "S1"],
        "bias_rate": ["S8", "D9"],
        "valuation": ["D1", "D7", "S1"],
        "financial_compare": ["D2", "S1"],
        "price_quote": ["D1"],
        "technical": ["D9"],
        "fund_flow": ["D3"],
        "announcement": ["D4"],
        "research_opinion": ["D5", "S9"],
        "screen_rank": ["D6"],
        "macro_industry": ["D7"],
        "tech_indicator": ["D9"],
        "structure": ["D8"],
        "deep_narrative": ["S1", "S2"],
    }
    return table.get(fam, [])


def can_support(cap_id: str, *, assume_preconditions: bool = True) -> bool:
    """某能力是否可用于「承诺支持」。

    conditional 在**前提已满足**时可支持；unverified / unavailable 一律不可承诺。
    """
    c = _CAP_INDEX.get(cap_id)
    if not c:
        return False
    if c["status"] == "verified_supported":
        return True
    if c["status"] == "conditional":
        return bool(assume_preconditions)
    return False  # unverified / unavailable


def refuse_reason(cap_id: str) -> str | None:
    c = _CAP_INDEX.get(cap_id)
    if not c:
        return f"未知能力 {cap_id}"
    if c["status"] == "verified_supported":
        return None
    if c["status"] == "conditional":
        return f"{cap_id} 为**条件支持**，需先满足前提（{c['permission']}）；缺失时：{c['on_missing']}"
    if c["status"] == "unverified":
        return f"{cap_id} **未验证**，不能承诺；缺失时：{c['on_missing']}"
    return f"{cap_id} **不可用**（无可执行依据）"


def scan_forbidden_promises(text: str):
    hits = []
    for phrase, why in FORBIDDEN_PROMISES.items():
        if phrase in (text or ""):
            hits.append({"phrase": phrase, "why": why})
    return hits


def rules_text() -> str:
    lines = ["# 能力边界规则（capability.py · v1.7.0）", ""]
    lines.append("## 状态四态")
    for k in STATUS_ORDER:
        lines.append(f"  {k:<20} {STATUS[k]}")
    lines.append("")
    lines.append("## 编号口径")
    for x in NUMBERING["resolution"]:
        lines.append("  " + x)
    lines.append("")
    lines.append("## 禁止承诺")
    for k, v in FORBIDDEN_PROMISES.items():
        lines.append(f"  ✗ {k} —— {v}")
    lines.append("")
    lines.append("## 三条诚实规则")
    for k, v in SPECIAL_CHAINS.items():
        lines.append(f"  · {k}：{v['honesty_rule']}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 6.5 检查清单执行器（供 cases/07 与 verify.py 真跑，不另写测试逻辑）
# ═══════════════════════════════════════════════════════════════════

def _dig(obj, path):
    """按点号路径取值；空路径返回对象本身。列表用数字下标。"""
    if path in (None, "", "."):
        return obj
    cur = obj
    for seg in str(path).split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def judge(cur, op, want):
    """单项断言判定，返回 (是否通过, 实际值说明)。"""
    if op == "eq":
        return cur == want, f"实际 {cur!r}"
    if op == "ne":
        return cur != want, f"实际 {cur!r}"
    if op == "contains":
        if isinstance(cur, str):
            return want in cur, f"实际 {cur[:80]!r}"
        if isinstance(cur, (list, tuple)):
            return want in cur, f"实际 {list(cur)!r}"
        return False, f"{cur!r} 不支持 contains"
    if op == "not_contains":
        if isinstance(cur, str):
            return want not in cur, f"实际 {cur[:80]!r}"
        if isinstance(cur, (list, tuple)):
            return want not in cur, f"实际 {list(cur)!r}"
        return True, f"{cur!r} 不含（非容器）"
    if op == "startswith":                     # 字符串前缀 / 列表前缀
        if isinstance(cur, str):
            return cur.startswith(want), f"实际 {cur[:60]!r}"
        return (isinstance(cur, (list, tuple)) and list(cur[:len(want)]) == list(want)), f"实际 {cur!r}"
    if op == "all_contains":                   # 列表中包含 want 的全部元素
        have = set(cur or [])
        return all(x in have for x in want), f"实际 {sorted(have)!r}"
    if op == "any_str_contains":               # 字符串列表中**任一元素**含 want 子串
        if not isinstance(cur, (list, tuple)):
            return False, f"{cur!r} 不是列表"
        hit = [x for x in cur if isinstance(x, str) and want in x]
        return bool(hit), f"命中 {hit[:2]!r}（共 {len(hit)} 条）"
    if op == "in":                             # 标量属于给定集合
        return cur in want, f"实际 {cur!r}"
    if op == "len_ge":
        return (isinstance(cur, (list, tuple, str, dict)) and len(cur) >= want), \
               f"实际长度 {len(cur) if hasattr(cur, '__len__') else '—'}"
    if op == "none_of":                        # 列表中不含 want 的任一元素
        have = set(cur or [])
        return not any(x in have for x in want), f"实际 {sorted(have)!r}"
    if op == "is_none":
        return cur is None, f"实际 {cur!r}"
    if op == "not_none":
        return cur is not None, f"实际 {cur!r}"
    if op == "true":
        return cur is True, f"实际 {cur!r}"
    if op == "false":
        return cur is False, f"实际 {cur!r}"
    if op == "truthy":
        return bool(cur), f"实际 {cur!r}"
    if op == "len_eq":
        return (isinstance(cur, (list, tuple, str, dict)) and len(cur) == want), f"实际长度 {len(cur) if hasattr(cur, '__len__') else '—'}"
    if op == "ge":
        return (isinstance(cur, (int, float)) and not isinstance(cur, bool) and cur >= want), f"实际 {cur!r}"
    if op == "matches":                        # 正则整串匹配（`want` 为 pattern）
        import re
        ok = isinstance(cur, str) and bool(re.fullmatch(want, cur))
        return ok, f"实际 {cur!r}"
    return False, f"未知断言算子 {op!r}"


def run_asserts(result, asserts):
    out = []
    for a in asserts:
        p, op = a.get("path", ""), a.get("op", "eq")
        cur = _dig(result, p)
        ok, det = judge(cur, op, a.get("value"))
        out.append({"path": p, "op": op, "pass": ok, "detail": det,
                    "expect": a.get("value")})
    return out


#: 由 capability.py 独立承担（不需要路由器参与）的检查类型
LOCAL_CHECK_TYPES = ("cap", "cap_map", "cap_status", "forbidden",
                     "boundary", "chain", "numbering", "field_completeness")


def _local_result(kind: str, ch: dict):
    if kind == "cap":
        return get(ch["id"]) or {}
    if kind == "cap_map":
        return capabilities_for(ch["family"])
    if kind == "cap_status":
        return [c["id"] for c in by_status(ch["status"])]
    if kind == "forbidden":
        hits = scan_forbidden_promises(ch.get("text", ""))
        return {"n": len(hits), "phrases": sorted(h["phrase"] for h in hits)}
    if kind == "boundary":
        return storage_boundary()
    if kind == "chain":
        return special_chains().get(ch["name"], {})
    if kind == "numbering":
        return numbering_note()
    if kind == "field_completeness":
        bad = {c["id"]: missing_fields(c) for c in ALL_CAPS if missing_fields(c)}
        return {"n_caps": len(ALL_CAPS), "incomplete": bad, "all_complete": not bad}
    raise ValueError(f"未知检查类型 {kind!r}")


def run_check_spec(spec: dict) -> dict:
    """执行 cases 里的检查清单，返回 {pass, n_checks, n_fail, results}。

    与轮次 2~4 的做法一致：**真跑生产数据/函数**，不另写一套测试逻辑。
    """
    items = []
    for ch in spec.get("checks", []):
        kind = ch.get("type")
        label = ch.get("label") or kind
        if kind not in LOCAL_CHECK_TYPES:
            items.append({"label": label, "pass": False,
                          "detail": f"capability 不处理的检查类型 {kind!r}（应由 router 处理）"})
            continue
        try:
            result = _local_result(kind, ch)
        except Exception as e:                                  # noqa: BLE001
            items.append({"label": label, "pass": False, "detail": f"执行失败：{e}"})
            continue
        for r in run_asserts(result, ch.get("assert", [])):
            items.append({
                "label": f"{label} · {r['path'] or '(根)'} {r['op']}",
                "pass": r["pass"],
                "detail": r["detail"] if r["pass"] else
                          f"{r['detail']}（期望 {r['expect']!r}）",
            })
    n_fail = sum(1 for x in items if not x["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail, "results": items}


# ═══════════════════════════════════════════════════════════════════
# 7. 自检
# ═══════════════════════════════════════════════════════════════════

def selftest() -> int:
    fails = []
    n = 0

    def ck(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fails.append(msg)

    # 1. 每项能力九属性齐备
    for c in ALL_CAPS:
        mf = missing_fields(c)
        ck(not mf, f"{c['id']} 缺属性：{mf}")
    ck(n == len(ALL_CAPS), "属性完整性检查未覆盖全部能力")

    # 2. 状态取值合法
    for c in ALL_CAPS:
        ck(c["status"] in STATUS, f"{c['id']} 状态非法：{c['status']}")

    # 3. 四态都被实际使用（避免「只写了四态、全用一态」）
    #    unavailable 由特殊链路（回测）承担，故一并计入。
    used = {c["status"] for c in ALL_CAPS} | {v["status"] for v in SPECIAL_CHAINS.values()}
    for s in STATUS_ORDER:
        ck(s in used, f"状态 {s} 从未被使用")

    # 4. D 编号唯一且连续 1~9
    ids = [c["id"] for c in DATA_CAPS]
    ck(ids == [f"D{i}" for i in range(1, 10)], f"D 编号应为 D1~D9，实际 {ids}")

    # 5. D4 = 公告与事件；D9 = 技术指标（D4 冲突已解）
    ck(_CAP_INDEX["D4"]["name"].startswith("公告与事件"), "D4 必须是「公告与事件」")
    ck(_CAP_INDEX["D9"]["name"] == "技术指标", "D9 必须是「技术指标」")
    ck("技术指标" not in _CAP_INDEX["D4"]["name"], "D4 不得再含「技术指标」")

    # 6. S 编号唯一
    sids = [c["id"] for c in SHENWA_CAPS]
    ck(len(sids) == len(set(sids)), "S 编号重复")
    ck("S11" in sids, "S11 必须存在（横截面直取）")

    # 7. S11 的对象层级必须是「指数级」而非个股
    ck("指数" in _CAP_INDEX["S11"]["object_level"], "S11 对象层级须为指数级")
    ck("个股" in _CAP_INDEX["S11"]["object_level"] and "不是" in _CAP_INDEX["S11"]["object_level"],
       "S11 须显式声明「不是个股」")

    # 8. S11 覆盖范围不得写成「全市场所有概念」
    ck("全市场所有概念" not in _CAP_INDEX["S11"]["coverage"], "S11 覆盖范围不得写「全市场所有概念」")

    # 9. 权限门槛：S8/S9/S11 标 VIP2 或 ADM
    for cid in ("S8", "S9", "S11"):
        ck("VIP2" in _CAP_INDEX[cid]["permission"] or "ADM" in _CAP_INDEX[cid]["permission"],
           f"{cid} 权限列须含 VIP2/ADM")
    ck(_CAP_INDEX["S11"]["permission"].count("VIP2") >= 1, "S11 权限须含 VIP2")

    # 10. 禁止承诺清单在场且被引用
    for k in ("私有发布", "服务端不保存"):
        ck(k in FORBIDDEN_PROMISES, f"禁止承诺须含 {k}")

    # 11. 扫描器工作
    hits = scan_forbidden_promises("本包支持私有发布，且服务端不保存")
    ck(len(hits) >= 2, f"禁止承诺扫描应命中 ≥2，实际 {len(hits)}")
    ck(scan_forbidden_promises("本地归档，服务端可见范围由服务端决定") == [],
       "正常表述不得被误判")

    # 12. 存储边界：明确「不是一回事」且给出不可承诺项
    sb = STORAGE_BOUNDARY
    ck("不是" in sb["answer"], "存储边界须明确「不是一回事」")
    ck(len(sb["cannot_promise"]) >= 3, "存储边界须列出 ≥3 条不可承诺项")
    ck("wfStartPublish" in sb["server_state"]["evidence"], "须给出可核查的接口依据")

    # 13. 量化 ≠ 完整具备
    q = SPECIAL_CHAINS["量化 / 因子"]
    ck("回测" in " ".join(q["not_supported"]), "量化链路须显式排除回测")
    ck(SPECIAL_CHAINS["回测"]["status"] == "unavailable", "回测状态须为 unavailable")
    ck(SPECIAL_CHAINS["跨市场"]["status"] == "unverified", "跨市场状态须为 unverified")

    # 14. can_support 语义
    ck(can_support("D2") is True, "D2 应可支持")
    ck(can_support("D5") is False, "D5 未验证，不得承诺")
    ck(can_support("D9") is True, "D9 条件支持、前提满足应可支持")
    ck(can_support("D9", assume_preconditions=False) is False, "D9 前提未满足不得承诺")
    ck(can_support("NOPE") is False, "未知能力不得支持")

    # 15. 指标族映射（路由层唯一入口）
    ck(capabilities_for("right_side_state") == ["S11", "S1"], "right_side_state 映射错误")
    ck(capabilities_for("bias_rate") == ["S8", "D9"], "bias_rate 映射错误")
    ck(capabilities_for("financial_compare") == ["D2", "S1"], "financial_compare 映射错误")
    ck("S11" not in capabilities_for("financial_compare"), "财务比较不得进右侧扫描")
    ck(capabilities_for("未知族") == [], "未知指标族应返回空，不得默认给 S11")

    # 16. 编号说明在场
    ck(len(NUMBERING["resolution"]) >= 3, "编号解法须 ≥3 条")
    ck("F" in NUMBERING["canonical"]["偏好选项组"], "偏好选项组须含更名后的 F 组")
    ck("D" not in NUMBERING["canonical"]["偏好选项组"], "偏好选项组不得再含 D 组")

    # 17. 检查清单执行器（cases/07 与 verify.py 走同一条路径）
    ck(_dig({"a": {"b": [1, 2]}}, "a.b.1") == 2, "_dig 点号路径取值错误")
    ck(_dig({"a": 1}, "a.x") is None, "_dig 缺失路径应返回 None")
    ck(_dig({"a": 1}, "") == {"a": 1}, "_dig 空路径应返回对象本身")
    ck(judge(["D2", "S1"], "startswith", ["D2", "S1"])[0], "startswith 判定错误")
    ck(not judge(["S11"], "not_contains", "S11")[0], "not_contains 判定错误")
    ck(judge("数量只决定批量方式，不决定分析类型", "contains", "不决定分析类型")[0], "contains 判定错误")
    ck(judge(["a", "b"], "none_of", ["c"])[0], "none_of 判定错误")
    ck(judge(None, "is_none", None)[0], "is_none 判定错误")
    ck(not judge(None, "eq", "x")[0], "eq 对 None 判定错误")
    demo = run_check_spec({"checks": [
        {"label": "demo", "type": "cap", "id": "D9",
         "assert": [{"path": "name", "op": "eq", "value": "技术指标"}]},
        {"label": "demo2", "type": "cap_map", "family": "financial_compare",
         "assert": [{"path": "", "op": "none_of", "value": ["S11"]}]},
    ]})
    ck(demo["pass"] and demo["n_checks"] == 2, f"run_check_spec 自跑未通过：{demo}")
    bad = run_check_spec({"checks": [{"label": "bad", "type": "cap", "id": "D4",
                                      "assert": [{"path": "name", "op": "eq", "value": "技术指标"}]}]})
    ck(not bad["pass"] and bad["n_fail"] == 1, "反例应被判为失败")
    ck(run_check_spec({"checks": [{"type": "unknown_kind", "label": "x"}]})["n_fail"] == 1,
       "未知检查类型应判失败，不得静默跳过")

    print(f"selftest: {'全部通过' if not fails else '失败 %d 项' % len(fails)}（{n} 项）")
    for f in fails:
        print("  ✗ " + f)
    return 1 if fails else 0


# ═══════════════════════════════════════════════════════════════════
# 8. CLI
# ═══════════════════════════════════════════════════════════════════

def _main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "selftest":
        return selftest()

    if cmd == "list":
        st = None
        if "--status" in rest:
            st = rest[rest.index("--status") + 1]
        items = by_status(st) if st else all_capabilities()
        print(f"{'ID':<5}{'能力':<34}{'状态':<20}对象层级")
        print("-" * 100)
        for c in items:
            print(f"{c['id']:<5}{c['name'][:32]:<34}{c['status']:<20}{c['object_level'][:40]}")
        print(f"\n共 {len(items)} 项")
        return 0

    if cmd == "show":
        if not rest:
            print("用法: show <ID>")
            return 2
        c = get(rest[0])
        if not c:
            print(f"未找到能力 {rest[0]}")
            return 2
        print(f"# {c['id']} · {c['name']}")
        for f in REQUIRED_FIELDS:
            print(f"  {f:<14}{c[f]}")
        return 0

    if cmd == "boundary":
        print(json.dumps(storage_boundary(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "verify-offline":
        print(json.dumps(special_chains(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "numbering":
        print(json.dumps(numbering_note(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "rules":
        print(rules_text())
        return 0

    print(f"未知子命令 {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

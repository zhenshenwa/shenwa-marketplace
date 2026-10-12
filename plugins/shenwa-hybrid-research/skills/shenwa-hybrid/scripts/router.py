# -*- coding: utf-8 -*-
"""router.py · 任务路由引擎（本包唯一的路由实现）

v1.7.0 新增。要修的旧行为：
  ① 按「排名 / 有没有 / 三个以上对象」等**词语**机械分流 → 词语只影响**批量方式**，不决定分析类型；
  ② 把「对象数量」当成「分析类型」的判据 → 数量只决定 `batch_mode`；
  ③ 把一切「多标的」都塞给 `get_right_side` → 该接口**只用于右侧状态**；
     乖离率、估值、财务比较**各自进入适配能力**；
  ④ 模板回退时悄悄换掉研究问题 → 回退必须保持 对象层级 / 主题 / 指标 / 时间 四项不变。

v1.11.0（第 2 轮）再修一个**更隐蔽**的病根：
  ⑤ `step3_metrics()` 命中多个指标族时**只返回第一个，其余静默丢弃** → 用户同时提的
     其它问题凭空消失（「估值贵不贵 + 财务有无风险 + 事件影响」只留下一个）。
     现改为**保留全部** `families`，`family` 仅为首选族（语义不变），下游逐个适配。
  ⑥ 模板选型**不得**因「指标族数量 ≥ 2」就改用 `tid=2001` —— 那是机械规则；
     只有「本层级专项模板覆盖不了用户已列出的问题」且层级允许时才改选。

路由顺序（**不可颠倒**，要求 6）：
  ① 研究问题  →  ② 对象与范围  →  ③ 指标  →  ④ 时间范围  →  ⑤ 所需深度  →  ⑥ 可用能力

能力候选一律来自 `capability.py::capabilities_for()`，本模块**不硬编码**能力名。
不联网、不调用任何外部接口。

CLI:
  python router.py selftest
  python router.py route "<任务原话>"
  python router.py cases [A|B|...]
  python router.py order        # 打印路由顺序与规则
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capability as cap        # noqa: E402
import requirement as req_mod   # noqa: E402  ← v1.11.0：统一问题清单（第 2 轮）

# ═══════════════════════════════════════════════════════════════════
# 0. 词表 —— 只用于「识别」，不用于「分流」
# ═══════════════════════════════════════════════════════════════════
# ⚠️ 关键设计：`BATCH_WORDS`（排名/哪些/有没有/分布…）**只影响 batch_mode**，
#    永不参与 analysis_type / metric_family 的判定。这正是本轮要修的病根。

METRIC_WORDS = {
    "right_side_state": ["右侧", "阶段升级", "阶段破坏", "阶段降级", "右侧确认", "右侧结构",
                         "迁移", "否决", "临界", "资格", "state", "处于什么阶段", "什么阶段",
                         "哪个阶段", "升级", "破坏", "降级"],
    "bias_rate": ["乖离率", "偏离度", "bias"],
    "valuation": ["估值", "市盈率", "市净率", "PE", "PB", "PS", "估值分位", "贵不贵", "便宜",
                  "低估", "高估", "合理价值", "目标价"],
    "financial_compare": ["盈利质量", "财务", "营收", "净利", "毛利率", "净利率", "ROE", "ROIC",
                          "资产负债", "现金流", "三表", "报表", "同比", "环比", "利润"],
    "price_quote": ["股价", "报价", "涨跌", "市值", "收盘", "现价", "行情"],
    "technical": ["均线", "MACD", "RSI", "KDJ", "布林", "BOLL", "量能", "动量", "波动率",
                  "技术指标", "技术面"],
    "fund_flow": ["资金流向", "主力", "北向", "龙虎榜", "净流入", "净流出", "成交额", "资金面"],
    "announcement": ["公告", "事件", "分红", "股东变动", "重大事项", "减持", "增持"],
    "research_opinion": ["研报", "评级", "观点", "舆情", "新闻", "卖方"],
    "macro_industry": ["宏观", "行业数据", "产业链", "景气", "产能", "库存", "产业"],
    "deep_narrative": ["竞争格局", "格局", "竞争", "战略", "推演", "驱动", "未来三年", "未来",
                       "趋势", "影响链条", "情景"],
}

OBJECT_WORDS = {
    "个股": ["公司", "个股", "股票", "股价", "标的", "这家"],
    "板块": ["板块", "概念"],
    "行业": ["行业", "赛道"],
    "产业链": ["产业链", "上游", "下游", "中游"],
    "宏观": ["宏观", "大盘", "全市场", "市场整体", "经济"],
    "指数": ["指数"],
}

# ⚠️ 这些词**只**决定 batch_mode（批量方式），**永不**决定分析类型。
BATCH_WORDS = ["排名", "排行", "前几", "Top", "TOP", "哪些", "有哪些", "有没有", "分布",
               "集中在哪", "一片", "全市场", "所有行业", "所有概念", "扫一遍", "筛一遍"]

DEPTH_WORDS = {
    "deep_research": ["深入", "深度", "详细", "系统性", "全面研究", "研究报告", "分析", "研究"],
    "comparison": ["比较", "对比", "孰优", "哪个更", "孰强"],
    "state": ["处于什么", "现在是什么", "是什么状态", "处于哪个", "怎么样"],
    "list": ["哪些", "名单", "列表", "有哪些", "筛选", "选一批", "选出"],
}

# 数量词 → count（只影响 batch_mode）
COUNT_WORDS = [("三家", 3), ("3 家", 3), ("三个", 3), ("三只", 3),
               ("两家", 2), ("2 家", 2), ("两个", 2), ("两只", 2),
               ("多家", None), ("若干", None), ("一批", None)]

AMBIGUOUS_WORDS = ["值得看", "值得关注", "看看有什么", "怎么样好", "随便看看", "有什么机会",
                   "什么样的好", "推荐"]

# ═══════════════════════════════════════════════════════════════════
# 1. 模板层级映射（实测值，与 02-能力分工与路由 的模板全量表一致）
# ═══════════════════════════════════════════════════════════════════

TEMPLATES_BY_LEVEL = {
    "个股": [0, 1, 7, 8, 9, 10, 2001],
    "板块": [3002, 3001],
    "行业": [4, 5, 6],
    "产业链": [3, 11],
    "宏观": [12, 2],
    "指数": [3002, 3001],
}

# 专项模板的「专长维度」——用于择优，不用于层级
SPECIALTY = {
    0: "简要", 1: "事件（宏观）", 2: "事件（市场影响）", 3: "产业链图谱",
    4: "行业竞争格局", 5: "概念炒作", 6: "概念炒作阶段", 7: "财务风险",
    8: "新闻影响", 9: "公司估值", 10: "产业链公司独立分析", 11: "产业链框架",
    12: "投研日报", 1001: "巴菲特视角", 2001: "通用自适应", 3001: "关键词实时跟踪",
    3002: "板块右侧监测",
}

# ═══════════════════════════════════════════════════════════════════
# 2. 第 1~5 步：识别
# ═══════════════════════════════════════════════════════════════════

def _hits(text, words):
    return [w for w in words if w in text]


def step1_research_question(text: str) -> dict:
    """① 研究问题：这是「要事实」「要机制」「要比对」还是「要筛选」。"""
    if _hits(text, ["为什么", "原因", "成因", "驱动", "归因", "意味着", "影响"]):
        kind, why = "mechanism", "含因果/归因语汇"
    elif _hits(text, DEPTH_WORDS["comparison"]):
        kind, why = "comparison", "含比对语汇"
    elif _hits(text, BATCH_WORDS):
        kind, why = "screening", "含筛查/排名语汇（**仅表示要在集合内定位，不代表要横向扫描接口**）"
    elif _hits(text, ["处于什么", "现在是什么", "是什么状态", "怎么样"]):
        kind, why = "state", "含状态询问语汇"
    elif _hits(text, ["是多少", "多少钱", "多少", "请问"]):
        kind, why = "fact", "含取数语汇"
    else:
        kind, why = "unknown", "未识别出明确问题类型"
    return {"kind": kind, "why": why}


def step2_object_scope(text: str) -> dict:
    """② 对象与范围：层级 + 数量 + **批量方式**。

    ⚠️ 数量只产出 `batch_mode`，**不产出** `analysis_type`。
    """
    level = None
    for lv, words in OBJECT_WORDS.items():
        if _hits(text, words):
            level = lv
            break
    if level is None and _hits(text, ["比较", "对比"]):
        level = "多对象"
    if level is None:
        level = "未指明"

    count = None
    for w, n in COUNT_WORDS:
        if w in text:
            count = n
            break

    import re
    codes = re.findall(r"\b\d{6}\b", text)
    if codes:
        count = len(codes)

    batch = _batch_mode(text, level, count)
    return {"level": level, "count": count, "batch_mode": batch,
            "entities_hint": codes, "why": f"层级={level}；数量={count}；批量方式={batch}"}


def _batch_mode(text, level, count):
    """批量方式：single / few / cross_section。**只回答「怎么批量」，不回答「分析什么」。**"""
    if _hits(text, ["全市场", "所有行业", "所有概念", "一片", "扫一遍"]):
        return "cross_section"
    if count is None and _hits(text, BATCH_WORDS):
        return "cross_section"
    if count is not None and count >= 3:
        return "cross_section"
    if count is not None and count == 2:
        return "few"
    if level in ("多对象",):
        return "cross_section"
    return "single"


#: 指标族优先序 —— ⚠️ **只用于定「首选族」**，**不再用于丢弃其它族**（v1.11.0）
METRIC_PRIORITY = ["right_side_state", "bias_rate", "valuation", "financial_compare", "technical",
                   "fund_flow", "announcement", "research_opinion", "macro_industry",
                   "deep_narrative", "price_quote"]


def families_for_text(text: str) -> list:
    """全部命中的指标族（按优先序排列，**一个都不丢**）。

    ★ v1.11.0 新增。这是本包**唯一**的「文本 → 指标族」映射入口：
    `step3_metrics()` 与 `requirement.build_requirements()` 都调用它，**不另写一套**。
    """
    scores = {}
    for fam, words in METRIC_WORDS.items():
        if _hits(text, words):
            scores[fam] = True
    ordered = [f for f in METRIC_PRIORITY if f in scores]
    ordered += [f for f in scores if f not in METRIC_PRIORITY]
    return ordered


def families_of(reading) -> list:
    """从一个 reading（或 `3_metrics` 节点）取**全部**指标族；兼容旧的单族结构。"""
    m = reading.get("3_metrics") if isinstance(reading, dict) and "3_metrics" in reading else reading
    if not isinstance(m, dict):
        return []
    fams = m.get("families")
    if fams:
        return list(fams)
    f = m.get("family")
    return [f] if f else []


def step3_metrics(text: str) -> dict:
    """③ 指标：决定**用哪个能力**（本轮核心正确的分流依据）。

    ⚠️ v1.11.0 修正：命中多个指标族时**保留全部**。
    旧行为是「按优先序取第一个就 return，其余静默丢弃」——
    那会让用户同时提的其它问题消失。
    `family` 仍是**首选族**（取值与改前完全一致），供既有单族消费者使用；
    `families` / `dropped` / `per_family` 是本轮新增，供多维消费者使用。
    """
    scores = {}
    for fam, words in METRIC_WORDS.items():
        h = _hits(text, words)
        if h:
            scores[fam] = h

    fams = [f for f in METRIC_PRIORITY if f in scores] + \
           [f for f in scores if f not in METRIC_PRIORITY]
    if not fams:
        return {"family": None, "families": [], "n_families": 0, "dropped": [],
                "matched": [], "per_family": {}, "why": "未识别出指标族"}

    primary = fams[0]
    return {
        "family": primary,                       # 首选族（**语义与改前一致**）
        "families": fams,                        # ★ 全命中，不丢
        "n_families": len(fams),
        "dropped": fams[1:],                     # ★ 显式登记「除首选外还有哪些」
        "matched": scores[primary],
        "per_family": {f: scores[f] for f in fams},
        "why": (f"命中指标词 {scores[primary]}；按优先序取首选 {primary}"
                + (f"；**另有 {fams[1:]} 同样命中，全部保留**" if len(fams) > 1 else "")),
    }


def step4_time_range(text: str) -> dict:
    import re
    m = re.search(r"(近|最近|过去)\s*([一二三四五六七八九十\d]+)\s*(天|日|周|个月|月|年)", text)
    if m:
        return {"spec": m.group(0), "kind": "relative", "why": "识别到相对时间窗"}
    m = re.search(r"(未来|今后|往后)\s*([一二三四五六七八九十\d]+)\s*(年|个月|月|季|季度)", text)
    if m:
        return {"spec": m.group(0), "kind": "forecast", "why": "识别到前瞻时间窗"}
    m = re.search(r"(20\d{2})[-/年]", text)
    if m:
        return {"spec": m.group(0), "kind": "absolute", "why": "识别到绝对日期"}
    if _hits(text, ["现在", "当前", "目前", "今日", "今天"]):
        return {"spec": "当前", "kind": "as_of_now", "why": "识别到「当前」"}
    return {"spec": None, "kind": "unspecified", "why": "未识别到时间范围（**缺失时按「当前截面」为透明默认**）"}


def step5_depth(text: str, metric_family, question_kind) -> dict:
    """⑤ 所需深度：list / state / comparison / deep_research。

    ⚠️ 判定顺序：**先看是否要求深度/机制，再看是否比对，再看是否要状态，最后才看是否要名单**。
    这样「深入比较三家行业的竞争格局」不会被「比较/三家」降级成名单。

    `metric_family` 可传 `str` **或** `list`（v1.11.0：多族时**任一**要求深度即为深度 —— 取最强）。
    """
    fams = [metric_family] if isinstance(metric_family, str) else list(metric_family or [])
    if _hits(text, DEPTH_WORDS["deep_research"]) or "deep_narrative" in fams:
        return {"level": "deep_research", "why": "含深度/机制语汇，或指标族为推演类"}
    if question_kind == "mechanism":
        return {"level": "deep_research", "why": "研究问题为机制类"}
    if question_kind == "comparison" or _hits(text, DEPTH_WORDS["comparison"]):
        return {"level": "comparison", "why": "含比对语汇"}
    if question_kind == "state" or _hits(text, DEPTH_WORDS["state"]):
        return {"level": "state", "why": "含状态询问语汇"}
    if _hits(text, DEPTH_WORDS["list"]) or question_kind == "screening":
        return {"level": "list", "why": "含名单/筛选语汇"}
    return {"level": "fact", "why": "仅取数"}


def step6_available(metric_family, *, available=None, denied=None) -> dict:
    """⑥ 可用能力：候选一律来自 capability 清单；权限/状态在此过滤。

    `metric_family` 可传 `str` **或** `list`（v1.11.0：多族取**并集**，一族一能力都保留，
    并附 `per_family` 便于逐问题分派）。
    """
    fams = [metric_family] if isinstance(metric_family, str) else list(metric_family or [])
    cand, per_family = [], {}
    for f in fams:
        cs = cap.capabilities_for(f) or []
        per_family[f] = cs
        for c in cs:
            if c not in cand:
                cand.append(c)
    usable, blocked = [], []
    for cid in cand:
        c = cap.get(cid)
        if not c:
            continue
        if denied and cid in denied:
            blocked.append({"id": cid, "name": c["name"], "why": "本次运行被显式禁用", "status": c["status"]})
            continue
        if available is not None and cid not in available:
            blocked.append({"id": cid, "name": c["name"], "why": "当前环境未提供该能力", "status": c["status"]})
            continue
        if c["status"] in ("unverified", "unavailable"):
            blocked.append({"id": cid, "name": c["name"],
                            "why": f"{c['status']}：不能承诺", "status": c["status"]})
            continue
        usable.append({"id": cid, "name": c["name"], "status": c["status"],
                       "permission": c["permission"], "precondition": c["on_missing"]})
    primary = cand[0] if cand else None
    primary_blocked = bool(primary and any(b["id"] == primary for b in blocked))
    return {"candidates": cand, "usable": usable, "blocked": blocked,
            "families": fams, "per_family": per_family,
            "primary": primary, "primary_blocked": primary_blocked}


# ═══════════════════════════════════════════════════════════════════
# 3. 读任务（6 步）
# ═══════════════════════════════════════════════════════════════════

def read_task(text: str, *, available=None, denied=None) -> dict:
    q = step1_research_question(text)
    o = step2_object_scope(text)
    m = step3_metrics(text)
    t = step4_time_range(text)
    fams = m["families"]                                   # ★ v1.11.0：全部指标族
    d = step5_depth(text, fams, q["kind"])
    a = step6_available(fams, available=available, denied=denied)

    amb = []
    if _hits(text, AMBIGUOUS_WORDS):
        amb.append({"kind": "vague_intent", "matched": _hits(text, AMBIGUOUS_WORDS),
                    "why": "「值得看 / 有什么机会」未给出筛选维度；**不得暗设用户投资风格**",
                    "resolution": "先澄清筛选维度；客户不答则用**透明默认**（估值分位 + 盈利质量 + 资金面，等权）并写明"})
    if not fams:
        amb.append({"kind": "no_metric", "matched": [],
                    "why": "未识别出指标族，无法据此选能力",
                    "resolution": "澄清「要看哪一类指标」；不答则不猜能力，只给数据可得性清单"})
    if o["level"] == "未指明":
        amb.append({"kind": "no_object", "matched": [],
                    "why": "未识别出对象层级",
                    "resolution": "澄清对象层级（个股 / 板块 / 行业 / 产业链 / 宏观）"})

    return {
        "1_research_question": q,
        "2_object_scope": o,
        "3_metrics": m,
        "families": fams,                                  # ★ 便捷入口（= m["families"]）
        "4_time_range": t,
        "5_depth": d,
        "6_available_capabilities": a,
        "ambiguities": amb,
        "clarify": [x for x in amb if x["kind"] in ("vague_intent", "no_metric", "no_object")] or None,
    }


# ═══════════════════════════════════════════════════════════════════
# 4. 交付形态与模板（要求 8 / 9）
# ═══════════════════════════════════════════════════════════════════

#: 交付形态的强度序 —— 多族合并时取**最强**（保守：不得把报告降级成直取）
MODE_RANK = {"direct": 0, "direct_list": 1, "data_plus_brief": 2, "report": 3}


def deliverable_for(reading) -> dict:
    """按「深度 + 可用能力」定交付形态；**不按对象数量**，**也不按指标族数量**。

    v1.11.0：多族时**逐族**求形态，再取**最强**的一个 —— 需求不会被优势族掩盖。
    """
    depth = reading["5_depth"]["level"]
    fams = families_of(reading) or [None]
    usable = [u["id"] for u in reading["6_available_capabilities"]["usable"]]

    per_family = {}
    for fam in fams:
        # ⚠️ 未识别指标族时**不得声称要发报告**（不知道看哪类指标，只能先给数据可得性）
        if fam is None and depth in ("fact", "state"):
            base = "direct"
        elif depth in ("fact", "state") and fam in ("right_side_state", "price_quote", "bias_rate",
                                                    "technical", "fund_flow", "announcement",
                                                    "valuation", "financial_compare", "macro_industry"):
            base = "direct"
        elif depth == "list":
            base = "direct_list"
        else:
            base = "report"
        # 已有数据足够解释 → 允许「数据 + 简析」，不强制另发报告
        if base == "direct" and depth == "state":
            base = "data_plus_brief"
        per_family[fam] = base

    base = max(per_family.values(), key=lambda x: MODE_RANK[x]) if per_family else "direct"
    return {"mode": base, "per_family": per_family,
            "why": f"深度={depth}，指标族={fams}，可用能力={usable}；多族取最强 → {base}"}


#: 「指标族 → 该族在**个股层级**的专项模板」。`None` = 该层级没有对应专项模板。
#: ⚠️ 用于判断**覆盖缺口**（「本层级专项模板覆盖不了的问题」），
#:    **不是**用来按「指标族数量」决定是否改选 2001（v1.11.0 明确）。
FAMILY_TEMPLATE = {
    "valuation": 9, "financial_compare": 7, "research_opinion": 8, "announcement": 8,
    "right_side_state": 3002, "macro_industry": 4,
    "deep_narrative": None, "bias_rate": None, "technical": None,
    "fund_flow": None, "price_quote": None,
}


def template_for(reading) -> dict | None:
    """模板选型：**按对象层级**（红线），不按对象数量，**也不按指标族数量**。

    v1.11.0：只有**当该层级确实存在「专项模板覆盖不了的问题」**（`coverage_gap` 非空）
    **且层级允许**（2001 是个股级）时，才考虑改用 2001。
    🔴 **不得**仅因「指标族数量 ≥ 2」就改用 2001。
    """
    lv = reading["2_object_scope"]["level"]
    depth = reading["5_depth"]["level"]
    if depth in ("fact", "state", "list"):
        return None  # 不发报告
    tids = TEMPLATES_BY_LEVEL.get(lv)
    if not tids:
        return None

    fams = families_of(reading)
    per_family = {}
    for fam in fams:
        hint = FAMILY_TEMPLATE.get(fam)
        per_family[fam] = hint if hint in tids else None
    missing = [f for f in fams if per_family[f] is None]      # ← 覆盖缺口（与族数无关）

    primary = None
    for fam in fams:
        if per_family[fam] is not None:
            primary = per_family[fam]
            break
    if primary is None:
        primary = tids[0] if lv != "个股" else 2001

    eligible = lv == "个股"
    switched = False
    switch_reason = {"by_family_count": False,
                     "note": "**指标族数量本身不构成选 2001 的理由**"}
    if missing and eligible:
        # 唯一合法触发：**本层级专项模板覆盖不了用户已列出的问题**（与族数无关）
        primary, switched = 2001, True
        switch_reason["coverage_gap"] = missing
        switch_reason["why"] = f"本层级专项模板覆盖不了 {missing} → 2001 优于强行拼凑多份报告"
    elif missing and not eligible:
        switch_reason["blocked_by_level"] = missing
        switch_reason["why"] = f"覆盖缺口 {missing} 存在，但本层级**不得**转 2001 → 如实说明能力边界"

    return {
        "level": lv,
        "allowed": tids,
        "初选": primary,
        "初选专长": SPECIALTY.get(primary, "—"),
        "per_family": per_family,
        "coverage_gap": {"families_without_template": missing, "by_family_count": False,
                         "note": "覆盖缺口 = 「该层级没有对应专项模板的问题」，**不是**「族数 ≥ 2」"},
        "switched_to_2001": switched,
        "switch_reason": switch_reason,
        "multi_family": len(fams) > 1,       # ← 只是**记录**，不参与决策
        "2001_eligible": eligible,           # ★ 2001 是个股级；非个股层级**不得**转 2001
        "rule": "先定层级再选模板；**每次都要与 tid=2001 正面比一次**（仅个股层级可用 2001）；"
                "**指标族数量不构成选 2001 的理由** —— 只有「本层级专项模板覆盖不了的问题」"
                "且层级允许时才改选；比较结论写入任务卡「模板选型」栏",
    }


def criteria_note(text: str, reading) -> dict | None:
    """「名单 / 横截面」类交付的**口径声明**（要求 10 的前半）。

    与 `rank_criteria` 的分工：
      · `rank_criteria` 管**排名 / Top-N**（必须有排序标准）；
      · `criteria_note` 管**名单 / 分布**（即使不排名，也必须写明口径与截面）。
    """
    depth = reading["5_depth"]["level"]
    o = reading["2_object_scope"]
    if depth not in ("list", "fact") and o["batch_mode"] != "cross_section":
        return None
    fams = families_of(reading) or [None]
    need = {
        "right_side_state": "**截面日期**（`dayOffset` 或指定交易日）+ 覆盖范围（本次实际返回的指数条数，**不写死 62**）+ "
                            "迁移类型口径（以 `state_trans.transition_type` 为准，不用 `state_action`）",
        "bias_rate": "**时间窗口**与**「改善」的定义**（由负转正 / 绝对值收窄 / 上穿阈值）",
        "valuation": "**估值分位窗口**与口径来源（哪个数据源、哪一天）",
        "financial_compare": "**报告期**与**科目口径**（半年 / 全年 / 单季 / TTM）",
        "macro_industry": "**指标口径**（数据点 + 最新日期）与**行业分类口径**",
    }
    default = "**口径声明**：时间截面 + 覆盖范围 + 指标定义"
    per_family = {f: need.get(f, default) for f in fams}
    merged = "；".join(dict.fromkeys(per_family.values()))
    return {
        "need": merged,
        "per_family": per_family,
        "rule": "名单 / 分布类交付**必须写明口径与截面**；口径缺失时先澄清，不答则用透明默认并写明。",
    }


def two_stage_plan(text: str, reading) -> dict | None:
    """「先扫描，再解释」的混合任务 → **同一总任务下的两个子任务**（要求 G）。"""
    has_scan = bool(_hits(text, ["扫描", "扫一遍", "筛一遍", "筛出", "选出名单", "先扫", "先筛", "全市场"]))
    has_read = bool(_hits(text, ["再解释", "再解读", "再深入", "再分析", "然后解释", "然后深入",
                                 "并对", "并对其中", "候选", "指定的"]))
    if not (has_scan and has_read):
        return None
    fams = families_of(reading)
    cid = (cap.capabilities_for(fams[0]) or [None])[0] if fams else None
    mf = "、".join(fams) if fams else "**待澄清（扫描哪一类指标）**"
    stage1 = {
        "stage": 1,
        "goal": "在全量里筛出候选名单",
        "metric_family": mf,
        "metric_families": fams,
        "capability": cid,
        "batch_mode": "cross_section",
        "cost": "0 点 / 秒级（右侧状态族走 S11 时）" if "right_side_state" in fams else "按所选能力计",
    }
    stage2 = {
        "stage": 2,
        "goal": "只对第 1 段筛出的候选做纵深解读",
        "object": "候选名单（默认 top 1~3）",
        "capability": "S1（模板报告，层级随候选对象）",
        "cost": "5 点 / 标的",
    }
    return {
        "mode": "two_stage",
        "stages": [stage1, stage2],
        "rule": "**同一总任务下的两个子任务，最终统一回答**；"
                "不得只做第 1 段（等于只给名单）或只做第 2 段（等于跳过了筛）；"
                "第 2 段只对第 1 段筛出的候选做，**不重复全量扫描**。",
        "pooling": "点数从 `N标的 × 5` 降到 `1~3 × 5`。",
    }


def rank_criteria(text: str, reading) -> dict | None:
    """排名 / Top-N 必须说明排序标准（要求 10）。多族时逐族声明，**不丢任一族的排序口径**。"""
    fams = families_of(reading)
    is_rank = bool(_hits(text, ["排名", "排行", "Top", "TOP", "前几", "最快", "最慢", "最高", "最低",
                                "较低", "较高", "偏低", "偏高", "最差", "最好", "值得看", "优选",
                                "改善最快", "排序"]))
    if not is_rank:
        return None
    defaults = {
        "bias_rate": "乖离率**改善幅度**（本期 − 上期的变化量）降序；须同时写明「改善」的定义（由负转正 / 绝对值收窄 / 上穿阈值），不得只写「改善」",
        "valuation": "估值分位（历史分位）升序（分位越低越靠前）；须写明分位窗口与口径来源",
        "right_side_state": "阶段迁移类型（Upgrade 优先）+ 迁移次序；须写明截面日期",
        "financial_compare": "盈利质量综合序（经营现金流/净利润、毛利率稳定性、应收账款周转）；须写明权重与口径",
        "screen_rank": "**须由客户指定或经澄清确认**；未指定则用透明默认并写明",
    }
    generic = "**须明确排序字段与方向**；未给则澄清"
    per_family = {f: defaults.get(f, generic) for f in (fams or [None])}
    merged = "；".join(dict.fromkeys(per_family.values()))
    return {
        "required": True,
        "standard": merged,
        "per_family": per_family,
        "rule": "排名 / Top-N **必须说明排序标准**；标准缺失时先澄清，不答则用透明默认并写明",
    }


def preflight_fallback(text, reading, failed_tid) -> dict:
    """模板回退：**必须保持 对象层级 / 主题 / 指标 / 时间 四项不变**（要求 9）

    v1.11.0：「指标」一项为**全部**指标族（多族时不得只保首选族）。
    """
    lv = reading["2_object_scope"]["level"]
    fams = families_of(reading)
    t = reading["4_time_range"]["spec"]
    cands = [t_ for t_ in (TEMPLATES_BY_LEVEL.get(lv) or []) if t_ != failed_tid]
    viable = [t_ for t_ in cands if t_ != 2001 or lv == "个股"]
    return {
        "failed_tid": failed_tid,
        "keep": {"对象层级": lv, "主题": fams[0] if fams else None,
                 "指标": fams, "指标族全集": fams, "时间": t},
        "candidates": viable,
        "rule": "**只有替代能力仍能回答原问题**时才回退（层级/主题/指标/时间四项不变）；"
                "四项中任一被改掉 → **不回退**，交付已完成部分并说明；"
                "**不得**把复杂任务无条件转给 2001（2001 只在个股层级可用）。",
        "on_no_viable": "可回退候选为空 → 交付已完成部分，如实说明缺什么，**不改问题**。",
    }


def cross_section_note(reading) -> dict | None:
    """把「批量方式」与「分析类型」明确分开（本轮核心）。

    v1.11.0：多族时**逐族分派**（右侧状态走 S11，其余各走适配能力），不再只看首选族。
    """
    o = reading["2_object_scope"]
    if o["batch_mode"] not in ("cross_section", "few"):
        return None
    fams = families_of(reading)
    if fams == ["right_side_state"]:
        return {"batch_mode": o["batch_mode"], "uses": "S11（get_right_side）", "families": fams,
                "note": "**只有「右侧状态」这一指标族**才走 get_right_side；"
                        "批量方式=cross_section 时 `codes` 留空（全量精选池），"
                        "=few 时 `codes` 逐个指定。"}
    if "right_side_state" in fams:
        others = [f for f in fams if f != "right_side_state"]
        return {"batch_mode": o["batch_mode"], "uses": "按族分派：right_side_state→S11，其余→各自适配能力",
                "families": fams,
                "note": f"本任务含**右侧状态族** → 该族走 S11（get_right_side）；其余 {others} 各走适配能力"
                        f"（{ {f: cap.capabilities_for(f) for f in others} }）。"
                        f"对象数量只决定**批量方式**（{o['batch_mode']}），**不决定分析类型**。"}
    return {"batch_mode": o["batch_mode"], "uses": "该指标族对应的适配能力", "families": fams,
            "note": f"对象数量只决定**批量方式**（{o['batch_mode']}），"
                    f"**不决定分析类型**；本任务的指标族是 `{fams}`，"
                    f"因此**不进** get_right_side，走 "
                    f"{ {f: cap.capabilities_for(f) for f in fams} }。"}


# ═══════════════════════════════════════════════════════════════════
# 5. 总入口
# ═══════════════════════════════════════════════════════════════════

def per_question_routing(reading, questions) -> list:
    """把**每一个问题**单独走一遍下游判定（v1.11.0 新增）。

    这是「多维需求完整传递」的落地点：交付形态 / 模板 / 能力 / 排序口径 / 口径声明
    都**逐问题**给出，避免用首选族的结论代表全部问题。
    """
    o = reading["2_object_scope"]
    depth = reading["5_depth"]["level"]
    caps = reading["6_available_capabilities"]
    rows = []
    for q in questions or []:
        qfams = list(q.get("metric_families") or [])
        shim = {"2_object_scope": o, "5_depth": {"level": depth},
                "3_metrics": {"families": qfams, "family": qfams[0] if qfams else None},
                "6_available_capabilities": caps}
        qdlv = deliverable_for(shim)
        qtpl = template_for(shim)
        qcaps = step6_available(qfams, available=None, denied=None)["candidates"]
        rows.append({
            "question_id": q.get("question_id"),
            "object_id": q.get("object_id"),
            "metric_families": qfams,
            "execution_mode": q.get("execution_mode"),
            "status": q.get("status"),
            "answer_slot": q.get("answer_slot"),
            "deliverable_mode": qdlv["mode"],
            "template": ({"初选": qtpl["初选"], "level": qtpl["level"],
                          "2001_eligible": qtpl["2001_eligible"],
                          "switched_to_2001": qtpl["switched_to_2001"]} if qtpl else None),
            "capabilities": qcaps,
            "rank_criteria": (rank_criteria(q.get("source_text") or "", shim) or {}).get("standard"),
            "criteria_note": (criteria_note(q.get("source_text") or "", shim) or {}).get("need"),
        })
    return rows


def route(text: str, *, available=None, denied=None, style=None,
          failed_tid=None, requested_drops=None, objects=None, object_level=None,
          questions=None, materials=None, answers=None) -> dict:
    """总入口。v1.11.0 新增关键字（**只增不改**，旧调用方不受影响）：
    `objects` / `questions` / `materials` / `answers` —— 用于多维需求与交付门。
    """
    reading = read_task(text, available=available, denied=denied)
    dlv = deliverable_for(reading)
    tpl = template_for(reading)
    rc = rank_criteria(text, reading)
    cn = criteria_note(text, reading)
    plan = two_stage_plan(text, reading)
    cs = cross_section_note(reading)
    fams = families_of(reading)
    usable = reading["6_available_capabilities"]["usable"]
    blocked = reading["6_available_capabilities"]["blocked"]
    primary_blocked = reading["6_available_capabilities"]["primary_blocked"]
    primary = reading["6_available_capabilities"]["primary"]

    honesty = []
    for b in blocked:
        honesty.append(f"{b['id']}（{b['name']}）不可承诺：{b['why']}")
    for u in usable:
        if u["status"] == "conditional":
            honesty.append(f"{u['id']}（{u['name']}）为**条件支持**，须先满足前提：{u['permission']}")

    # 已有数据足够解释时，不强制另发报告：非报告交付不必列 S1
    chosen = [u["id"] for u in usable]
    not_needed = []
    if dlv["mode"] in ("direct", "direct_list", "data_plus_brief") and "S1" in chosen:
        chosen.remove("S1")
        not_needed.append({"id": "S1", "why": "本任务交付形态为「数据（+简析）」，"
                                              "**不强制另发报告**；客户若要纵深解读时再启用"})

    # ★ 第 2 轮：统一问题清单（多问题分别处理，**不静默遗漏**）
    req = req_mod.build_requirements(
        text, reading=reading, objects=objects, object_level=object_level, questions=questions,
        families_of=families_for_text, materials=materials, available=available,
        template_ok=bool(tpl) or dlv["mode"] == "report")

    out = {
        "task": text,
        "task_reading": reading,
        "candidates": reading["6_available_capabilities"]["candidates"],
        "chosen": chosen,
        "not_needed": not_needed,
        "blocked": blocked,
        "deliverable": dlv,
        "template": tpl,
        "batch_vs_analysis": cs,
        "rank_criteria": rc,
        "criteria_note": cn,
        "plan": plan,
        "honesty": honesty,
        "primary": primary,
        "primary_blocked": primary_blocked,
        # ★ v1.11.0 新增（只增不改）
        "families": fams,
        "n_families": len(fams),
        "dropped_families": reading["3_metrics"].get("dropped", []),
        "requirements": req,
        "per_question": per_question_routing(reading, req["questions"]),
        "style_applied": False,   # 风格只作偏好提示，不据此强制方法（沿用 v1.6.0 口径）
        "style_note": "方法/能力按**研究对象与数据条件**选择；风格只作偏好提示，"
                      "不因风格与可用能力相悖而强制并列。",
    }

    if primary_blocked:
        honesty.append(
            f"**首选能力 {primary} 不可用** → 必须**如实说明**这一限制（缺什么、为什么、"
            f"若要可用需满足什么）；**不得**改用别的问题（如改发一份报告）充当完成。")
        out["action"] = "honest_limit"
        out["reason"] = (f"指标族 {fams} 的首选能力 {primary} 不可用 → "
                         f"如实限制能力，不改成其他问题充当完成")
    elif reading["clarify"]:
        out["action"] = "clarify"
        out["clarify_questions"] = [c["resolution"] for c in reading["clarify"]]
        if plan:
            out["reason"] = ("已识别为**两段式混合任务**（扫描 + 解读），子任务划分见 plan；"
                             "但关键歧义会实质改变结果 → 先澄清：" +
                             "；".join(out["clarify_questions"]))
        else:
            out["reason"] = "关键歧义会实质改变结果 → 先澄清"
    elif not usable:
        out["action"] = "honest_limit"
        out["reason"] = ("候选能力全部不可承诺 → **如实限制能力**，"
                         "说明缺什么、为什么，**不得改成其他问题充当完成**")
    else:
        out["action"] = "proceed"
        out["reason"] = (f"指标族={fams}（共 {len(fams)} 族，**全部保留**）；"
                         f"层级={reading['2_object_scope']['level']}；"
                         f"深度={reading['5_depth']['level']}；"
                         f"交付={dlv['mode']}；问题数={req['n_questions']}")

    if failed_tid is not None:
        out["fallback"] = preflight_fallback(text, reading, failed_tid)

    if requested_drops:
        out["refused_drops"] = list(requested_drops)
        out["refuse_reason"] = "不得为求收敛/为对齐排名而删除已适用的能力或结果"

    # ★ 交付门：提供回答即对照问题清单逐条检查（**进入实际交付流程**）
    if answers is not None:
        out["coverage"] = req_mod.deliver_gate(req, answers)
        out["delivery_blocked"] = not out["coverage"]["complete"]
        out["delivery_reason"] = ("全部必需问题均已实际回答 → 可标记交付完成"
                                  if out["coverage"]["complete"]
                                  else "存在未完成的必需问题 → **不得**标记为全部完成；"
                                       "须逐条给出状态与原因")

    return out


def order_text() -> str:
    return """# 路由顺序（不可颠倒）

① 研究问题   → 事实 / 机制 / 比对 / 筛选 / 状态
② 对象与范围 → 对象层级（红线）+ 数量 → **只产出 batch_mode**
③ 指标       → 指标族（**唯一的「选哪个能力」依据**）；**命中多族时全部保留，不丢**
④ 时间范围   → 绝对 / 相对 / 前瞻 / 当前
⑤ 所需深度   → fact / state / list / comparison / deep_research
⑥ 可用能力   → 按权限与状态过滤（unverified / unavailable 不得承诺）；**多族取并集**

# 十条硬规则（v1.7.0 五条 + v1.11.0 增补两条 + v1.12.0 一条 + v1.13.0 一条 + v1.14.0 一条）
1. 「排名 / 哪些 / 有没有 / 三个以上」**只影响 batch_mode**，永不决定分析类型。
2. `get_right_side`(S11) **只用于右侧状态**；乖离率→S8/D9，估值→D1/D7，财务比较→D2。
3. 单对象可直取；多对象可深入分析；数据足够解释时用「数据 + 简析」，不强制发报告。
4. 模板选择与回退都要重查 对象层级/主题/指标/时间；不得无条件转 2001（2001 仅个股层级）。
5. 排名 / Top-N 必须说明排序标准；关键歧义先澄清，否则用透明默认并写明。

# 第 2 轮（v1.11.0）新增两条硬规则
6. **多维需求一条不丢**：`step3_metrics()` 命中多族时**全部保留**（`families`）；
   下游一律按「问题清单」逐条传递 —— **不得**用首选族的结论代表全部问题。
   注意 **问题数量 ≠ 指标族数量**：同族可含多问，一问也可涉多族，**不得按族去重**。
7. **不得用指标族数量（或「出现分析二字」）机械触发深度报告或改用 2001**。
   执行方式按**证据缺口**选（直取 / 计算 / 已有资料 / 报告候选）；
   2001 只在「本层级专项模板覆盖不了用户已列出的问题」且层级允许时改选。

# 第 3 轮（v1.12.0）新增一条硬规则
8. **每次报告发布都要有明确用途与任务条目对应**：发布一律经
   `skills/shenwa-hybrid/scripts/publish_plan.py` 的 `publish()` 真实入口（**不得在 SOP 里
   直接调 `report_publish`**）；条目编号 `entry_id` **提交前生成**并与 aid 绑定；
   份数由**模板能力 / 具体问题 / 有效复用**共同决定（**不得默认「一家公司一份报告」、
   不得静默缩减对象数**）；**成本未知不按 0**、**账户余额 ≠ 授权额度**、
   **aid ≠ 报告完成 ≠ 问题已回答**；结果未知**保留占用**且**不换站重发**。

# 第 4 轮（v1.13.0）新增一条硬规则
9. **背景要有据、来源要判独立、复用要看内容**：发送给深挖的背景一律经
   `publish_plan.assemble_background()` 组装并**记录实际发送的文字** ——
   已核实数字**字带来源 / 时间 / 单位 / 口径**、未核实材料**必须标状态**、
   用户观点写成**待检验命题**、独立判断时**不注入预设结论**；**不得假定深挖能读本地路径
   / 文件名 / 材料编号**；超限**不得静默截断**（须调整条目 / 执行方式并重检覆盖与预算）。
   **深挖复述已传入的背景数字不算新增独立证据**；**不同接口指向同一底层材料不算独立验证**；
   来源关系未知一律标 `unknown`（**不默认独立、不武断同源**），**不得**只因两个结果一致
   就写「两路独立验证一致」。历史报告能否复用**按内容判定**（`scripts/reuse.py`）：列表 /
   标题 / 模板 / 对象 / 日期**只用于找候选**；**同日同对象同模板但问题不同不得直接复用**，
   表述不同而问题相同且条件有效**可以复用**；跨日**稳定信息可用、时效数据须重查**、
   旧结论依赖已变数据**必须重新分析**；用户要求新时点 / 新情景 / 重做**不得用旧报告替换**。

# 第 5 轮（v1.14.0）新增一条硬规则
10. **监测要留档、比较要在成稿前**：持续跟踪 / 与上次或指定日期比较 / 检验更新此前判断
    / 属于已建立监测序列的任务，一律经 `scripts/monitor.py` 的 `run_monitor_flow()`
    真实入口（识别监测任务 → 读取历史基线 → 获取或复用本次资料 → 来源与数据校验
    → 检查可比性 → 比较事实和判断 → 生成交付内容 → 保存本次记录及比较结果）。
    **首次监测从本次建立基线，不编造历史变化**；基线**指定优先、其次最近适用**，
    **不得按文件修改时间 / 下载顺序 / 报告返回顺序选择**；**读取失败 ≠ 无历史**。
    **先检查可比性再计算变化**（单位 / 统计期间 / 样本范围 / 分析规则），口径差异
    **不得直接归因为对象变化**；**缺失值不当作 0、基数为 0 不硬算变化率**；
    **数据修订 / 规则调整不写成研究对象变化**；**只有两个时点不得宣称持续趋势**；
    **同一数据时点重复归档不制造新观测**（原始快照保存后**不覆盖**，修订 `supersedes`
    旧记录并保留原始值）；**已有资料足够完成比较 → 不新增多余的报告发布**（需要新报告
    仍走第 3 轮 `publish()` 真实入口）；**未接入真实调度**，不宣称自动定时运行或主动推送。
"""


# ═══════════════════════════════════════════════════════════════════
# 5.5 检查清单执行器（cases/07 与 verify.py 走同一条真实路径）
# ═══════════════════════════════════════════════════════════════════

#: 用例输入的口径与 selftest / `cases` 子命令**共用同一份**，避免两处漂移
CASE_KWARGS = {
    "H": {"denied": ["S11"]},
    "I": {"failed_tid": 9},
}


def _route_for_case(key, **extra):
    kw = dict(CASE_KWARGS.get(key, {}))
    kw.update(extra)
    return route(CASE_INPUTS[key], **kw)


def run_check_spec(spec: dict) -> dict:
    """执行 cases 的检查清单。

    支持的类型：
      · 能力类（cap / cap_map / cap_status / forbidden / boundary / chain /
        numbering / field_completeness）→ **委派给 capability.py**，本模块不重复实现；
      · `route`   —— 单个用例走一遍 `route()`，按点号路径断言；
      · `compare` —— 两段输入各走一遍，比较同一路径（或 `other` 指定路径）；
      · `case`    —— 直接引用 `CASE_INPUTS` 的 A~J（kwargs 由 `CASE_KWARGS` 决定）；
      · `order`   —— 路由顺序文本；
      · `requirement` —— 走 `requirement.build_requirements()`（v1.11.0 新增 · 第 2 轮）；
      · `flow`    —— 走 `requirement.run_flow()`（问题清单 → 执行 → 证据 → 交付门，
                     发布端恒为**离线替身**，可断言 `publish_calls`）。
    """
    items = []

    def add(label, ok, detail):
        items.append({"label": label, "pass": bool(ok), "detail": detail})

    for ch in spec.get("checks", []):
        kind = ch.get("type")
        label = ch.get("label") or kind

        if kind in cap.LOCAL_CHECK_TYPES:
            sub = cap.run_check_spec({"checks": [ch]})
            items.extend(sub["results"])
            continue

        if kind in ("route", "case"):
            text = CASE_INPUTS[ch["input"]] if kind == "case" else ch["input"]
            kw = dict(CASE_KWARGS.get(ch.get("input"), {})) if kind == "case" else {}
            kw.update(ch.get("kwargs") or {})
            try:
                result = route(text, **kw)
            except Exception as e:                              # noqa: BLE001
                add(label, False, f"路由执行失败：{e}")
                continue
            for r in cap.run_asserts(result, ch.get("assert", [])):
                add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                    r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")
            continue

        if kind == "requirement":
            kw = dict(ch.get("kwargs") or {})
            kw.setdefault("families_of", families_for_text)
            try:
                result = req_mod.build_requirements(ch["input"], **kw)
            except Exception as e:                              # noqa: BLE001
                add(label, False, f"需求清单构造失败：{e}")
                continue
            for r in cap.run_asserts(result, ch.get("assert", [])):
                add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                    r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")
            continue

        if kind == "flow":
            kw = dict(ch.get("kwargs") or {})
            try:
                result = req_mod.run_flow(ch["input"], **kw)
            except Exception as e:                              # noqa: BLE001
                add(label, False, f"流程用例执行失败：{e}")
                continue
            for r in cap.run_asserts(result, ch.get("assert", [])):
                add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                    r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")
            continue

        if kind == "compare":
            a = route(ch["input_a"], **(ch.get("kwargs_a") or {}))
            b = route(ch["input_b"], **(ch.get("kwargs_b") or {}))
            for a_ in ch.get("assert", []):
                pa, pb, op = a_.get("path", ""), a_.get("other", a_.get("path", "")), a_.get("op", "eq")
                va, vb = cap._dig(a, pa), cap._dig(b, pb)
                ok = (va == vb) if op == "eq" else (va != vb)
                add(f"{label} · {pa} {op} {pb}", ok,
                    f"a={va!r} / b={vb!r}" + ("" if ok else "（应" + ("相等" if op == "eq" else "不同") + "）"))
            continue

        if kind == "order":
            result = {"order_text": order_text()}
            for r in cap.run_asserts(result, ch.get("assert", [])):
                add(f"{label} · {r['path']} {r['op']}", r["pass"],
                    r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")
            continue

        add(label, False, f"router 不处理的检查类型 {kind!r}")

    n_fail = sum(1 for x in items if not x["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail, "results": items}


# ═══════════════════════════════════════════════════════════════════
# 6. 自检 + 用例
# ═══════════════════════════════════════════════════════════════════

CASE_INPUTS = {
    "A": "比较三家公司的盈利质量",
    "B": "哪些行业估值分位较低",
    "C": "哪些板块乖离率改善最快",
    "D": "哪些板块发生右侧阶段升级",
    "E": "查询一个板块现在处于什么阶段",
    "F": "深入比较三个行业未来三年的竞争格局",
    "G": "先扫描全市场，再解释我指定的两个候选",
    "H": "帮我看一下某个板块的右侧阶段（本账号无 VIP2 权限）",
    "I": "对某公司做估值分析（专项模板生成失败）",
    "J": "哪些板块值得看",
}


def _r(text, **kw):
    return route(text, **kw)


def selftest() -> int:
    fails = []
    n = 0

    def ck(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fails.append(msg)

    # ── A：比较三家公司盈利质量 → 财务比较，不进右侧扫描 ──
    a = _r(CASE_INPUTS["A"])
    ck(a["task_reading"]["3_metrics"]["family"] == "financial_compare", "A 指标族应为财务比较")
    ck("S11" not in a["candidates"], "A 不得把右侧接口列为候选")
    ck(a["task_reading"]["6_available_capabilities"]["candidates"][:2] == ["D2", "S1"],
       f"A 候选应为 D2/S1，实际 {a['candidates']}")
    ck(a["batch_vs_analysis"] is not None and "不决定分析类型" in a["batch_vs_analysis"]["note"],
       "A 应给出「数量不决定分析类型」的说明")
    ck(a["task_reading"]["2_object_scope"]["batch_mode"] == "cross_section",
       "A 三对象应为 cross_section 批量方式")

    # ── B：哪些行业估值分位较低 → 估值 + 排序标准 ──
    b = _r(CASE_INPUTS["B"])
    ck(b["task_reading"]["3_metrics"]["family"] == "valuation", "B 指标族应为估值")
    ck("S11" not in b["candidates"], "B 不得进右侧扫描")
    ck(b["rank_criteria"] and b["rank_criteria"]["required"] is True, "B 必须有排序标准")
    ck("分位" in b["rank_criteria"]["standard"], "B 排序标准应含「分位」")
    ck(b["task_reading"]["2_object_scope"]["level"] == "行业", "B 层级应为行业")

    # ── C：哪些板块乖离率改善最快 → 乖离率 + 明确的改善定义 ──
    c = _r(CASE_INPUTS["C"])
    ck(c["task_reading"]["3_metrics"]["family"] == "bias_rate", "C 指标族应为乖离率")
    ck("S11" not in c["candidates"], "C 不得进右侧扫描")
    ck(c["candidates"][:2] == ["S8", "D9"], f"C 候选应为 S8/D9，实际 {c['candidates']}")
    ck(c["rank_criteria"] and "改善" in c["rank_criteria"]["standard"] and "定义" in c["rank_criteria"]["standard"],
       "C 排序标准必须给出「改善」的定义")

    # ── D：哪些板块发生右侧阶段升级 → S11 + 指定日期截面 ──
    d = _r(CASE_INPUTS["D"])
    ck(d["task_reading"]["3_metrics"]["family"] == "right_side_state", "D 指标族应为右侧状态")
    ck(d["candidates"][:2] == ["S11", "S1"], f"D 候选应为 S11/S1，实际 {d['candidates']}")
    ck(d["batch_vs_analysis"] and d["batch_vs_analysis"]["uses"].startswith("S11"),
       "D 应走 S11")
    ck(d["criteria_note"] and "截面日期" in d["criteria_note"]["need"],
       "D 口径声明须写明截面日期")
    ck(d["criteria_note"] and "不写死 62" in d["criteria_note"]["need"],
       "D 口径声明须含「覆盖范围不写死 62」")

    # ── E：查询一个板块现在处于什么阶段 → 直取 + 简析，不强制报告 ──
    e = _r(CASE_INPUTS["E"])
    ck(e["task_reading"]["2_object_scope"]["batch_mode"] == "single", "E 应为单对象")
    ck(e["candidates"][0] == "S11", "E 应用 S11 直取")
    ck(e["deliverable"]["mode"] == "data_plus_brief", f"E 应为数据+简析，实际 {e['deliverable']['mode']}")
    ck(e["template"] is None, "E 不应发模板报告")

    # ── F：深入比较三个行业未来三年竞争格局 → 不因三对象退成状态名单 ──
    f = _r(CASE_INPUTS["F"])
    ck(f["task_reading"]["5_depth"]["level"] == "deep_research",
       f"F 深度应为 deep_research，实际 {f['task_reading']['5_depth']['level']}")
    ck("S11" not in f["candidates"], "F 不得退成右侧状态名单")
    ck(f["deliverable"]["mode"] == "report", "F 应发报告")
    ck(f["template"] is not None, "F 应有模板选型")
    ck(f["template"]["level"] == "行业", "F 层级应为行业")
    ck(f["template"]["初选"] in (4, 5, 6), f"F 初选应为行业级模板，实际 {f['template']['初选']}")
    ck(f["template"]["2001_eligible"] is False, "F 为行业层级 → 2001 不可用")
    ck(f["task_reading"]["2_object_scope"]["count"] == 3 or
       f["task_reading"]["2_object_scope"]["batch_mode"] == "cross_section",
       "F 应识别出三个对象（且仅体现为批量方式）")

    # ── G：先扫描再解释两个候选 → 两段式，不二选一 ──
    g = _r(CASE_INPUTS["G"])
    ck(g["plan"] is not None, "G 应识别为两段式混合任务")
    ck(g["plan"]["mode"] == "two_stage", "G 的 plan 模式应为 two_stage")
    ck(len(g["plan"]["stages"]) == 2, "G 应有两个子任务")
    ck(g["plan"]["stages"][0]["batch_mode"] == "cross_section", "G 第 1 段应为横截面筛选")
    ck("S1" in g["plan"]["stages"][1]["capability"], "G 第 2 段应为模板报告")
    ck("不得只做第 1 段" in g["plan"]["rule"], "G 须禁止只做一段")
    # 指标族未指明 → 第 1 段须标「待澄清」而非默认猜一个
    ck("待澄清" in g["plan"]["stages"][0]["metric_family"], "G 指标未指明时第 1 段须标「待澄清」")
    ck(g["action"] == "clarify", "G 指标未指明 → 应先澄清")

    # ── H：无权限 → 如实限制，不改问题 ──
    h = _r(CASE_INPUTS["H"], denied=["S11"])
    ck("S11" not in h["chosen"], "H 无权限时 S11 不得入选")
    ck(any(x["id"] == "S11" for x in h["blocked"]), "H 应把 S11 列入 blocked")
    ck(h["action"] == "honest_limit", f"H 首选能力不可用时应如实限制，实际 {h['action']}")
    ck(h["primary_blocked"] is True, "H 应标记 primary_blocked")
    ck(any("不可承诺" in x or "条件支持" in x for x in h["honesty"]), "H 必须给出诚实声明")
    ck(any("如实说明" in x and "不得" in x for x in h["honesty"]), "H 须声明「如实说明 + 不得改问题」")
    h2 = _r(CASE_INPUTS["H"], available=["D1"])   # 只给 D1 → 该指标族无可用能力
    ck(h2["action"] == "honest_limit", "H 全无可用能力时必须如实限制")
    ck("不改成其他问题" in h2["reason"], "H 的说明须含「不改成其他问题充当完成」")

    # ── I：专项模板失败 → 只有替代能力能回答原问题才回退 ──
    i = _r(CASE_INPUTS["I"], failed_tid=9)
    ck("fallback" in i, "I 应给出回退方案")
    ck(i["fallback"]["keep"]["对象层级"] == "个股", "I 回退须保持对象层级")
    ck(2001 in i["fallback"]["candidates"], "I 个股层级可用 2001")
    ck("不回退" in i["fallback"]["rule"], "I 须说明「四项被改掉就不回退」")
    # 非个股层级失败时不得转 2001
    f2 = _r(CASE_INPUTS["F"], failed_tid=4)
    ck(f2["fallback"]["candidates"] == [5, 6], f"F 回退候选应为 5/6，实际 {f2['fallback']['candidates']}")
    ck(2001 not in f2["fallback"]["candidates"], "行业层级回退**不得**转 2001")

    # ── J：模糊的「哪些板块值得看」→ 澄清或透明默认，不暗设风格 ──
    j = _r(CASE_INPUTS["J"])
    ck(j["action"] == "clarify", f"J 应触发澄清，实际 {j['action']}")
    ck(any("澄清" in q for q in j.get("clarify_questions", [])), "J 应给出澄清项")
    ck(j["style_applied"] is False, "J 不得暗设用户投资风格")
    ck(any("不得暗设用户投资风格" in x["why"] or "暗设" in x["why"]
           for x in j["task_reading"]["ambiguities"]), "J 须显式声明「不暗设风格」")

    # ── 通用：数量不决定分析类型 ──
    t1 = _r("三家公司的盈利质量")
    t2 = _r("一家公司的盈利质量")
    ck(t1["task_reading"]["3_metrics"]["family"] == t2["task_reading"]["3_metrics"]["family"],
       "对象数量变化不得改变指标族（分析类型）")
    ck(t1["task_reading"]["2_object_scope"]["batch_mode"] !=
       t2["task_reading"]["2_object_scope"]["batch_mode"],
       "对象数量应当**只**改变批量方式")

    # ── 通用：排名词不决定分析类型 ──
    t3 = _r("哪些板块发生右侧阶段升级")
    t4 = _r("板块发生右侧阶段升级")
    ck(t3["task_reading"]["3_metrics"]["family"] == t4["task_reading"]["3_metrics"]["family"] == "right_side_state",
       "「哪些」不得改变指标族")

    # ── 通用：100% 走 capability 清单，不硬编码 ──
    for fam, exp in (("right_side_state", ["S11", "S1"]), ("bias_rate", ["S8", "D9"]),
                     ("financial_compare", ["D2", "S1"]), ("valuation", ["D1", "D7", "S1"])):
        ck(cap.capabilities_for(fam) == exp, f"capabilities_for({fam}) 应为 {exp}")

    # ══════════════════════════════════════════════════════════════
    # 第 2 轮（v1.11.0）：多维需求一条不丢 + 下游完整传递
    # ══════════════════════════════════════════════════════════════

    # ── K1：step3_metrics 命中多族时**全部保留**，family 首选语义不变 ──
    m3 = step3_metrics("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响")
    ck(m3["family"] == "valuation", f"首选族应仍为 valuation（语义不变），实际 {m3['family']}")
    ck(m3["families"] == ["valuation", "financial_compare", "announcement"],
       f"三族应全部保留，实际 {m3['families']}")
    ck(m3["n_families"] == 3, "n_families 应为 3")
    ck(m3["dropped"] == ["financial_compare", "announcement"],
       f"dropped 应显式登记被保留的其余族，实际 {m3['dropped']}")
    ck(set(m3["per_family"]) == {"valuation", "financial_compare", "announcement"},
       "per_family 应逐族给出命中词")
    m1 = step3_metrics("比较三家公司的盈利质量")
    ck(m1["families"] == ["financial_compare"] and m1["dropped"] == [] and m1["n_families"] == 1,
       "单族时应与改前完全一致（families=[family]，dropped=[]）")
    ck(families_for_text("同时查询PE和资产负债率") == ["valuation", "financial_compare"],
       "families_for_text 应按优先序给出全部命中族")
    ck(families_for_text("毫无指标词的一句话") == [], "无命中时应返回空列表")

    # ── K2：多族不因优先级排序而丢弃其它问题（用户要求 3）──
    k = _r("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响",
           available=["D1", "D7", "D2", "D4", "D9"], objects=["某公司"])
    ck(k["n_families"] == 3 and k["dropped_families"] == ["financial_compare", "announcement"],
       "多族信息必须完整传到 route() 输出")
    ck(k["requirements"]["n_questions"] == 3,
       f"三问应各留一条，实际 {k['requirements']['n_questions']}")
    ck([q["question_id"] for q in k["requirements"]["questions"]] == ["某公司-Q1", "某公司-Q2", "某公司-Q3"],
       "问题编号应稳定且唯一")
    ck(len(k["per_question"]) == 3, "per_question 应逐问题给出")
    ck([pq["template"]["初选"] for pq in k["per_question"]] == [9, 7, 8],
       f"逐问题模板应为 9/7/8，实际 {[pq['template']['初选'] for pq in k['per_question']]}")
    ck([pq["deliverable_mode"] for pq in k["per_question"]] == ["report"] * 3,
       "三个问题都应保留报告形态（不得被首选族掩盖）")

    # ── K3：模板不得因**指标族数量**改用 2001（本轮要修的机械规则）──
    ck(k["template"]["multi_family"] is True, "应登记 multi_family=True（仅记录）")
    ck(k["template"]["coverage_gap"]["by_family_count"] is False,
       "coverage_gap 不得由族数产生")
    ck(k["template"]["switch_reason"]["by_family_count"] is False,
       "switch_reason 必须显式写明「族数不构成选 2001 的理由」")
    ck(k["template"]["switched_to_2001"] is False,
       f"三族且专项模板齐备（9/7/8）→ **不得**改用 2001，实际 {k['template']['初选']}")
    ck(k["template"]["初选"] == 9, "应保留首选族对应的专项模板 9")
    ck("指标族数量不构成选 2001 的理由" in k["template"]["rule"],
       "模板规则文本须写明族数不是理由")
    # 族数变化不得改变结论：1 族与 3 族在「专项模板齐备」时都**不**转 2001
    k1 = _r("深入分析这家公司的估值", available=["D1", "D7", "S1"], objects=["某公司"])
    ck(k1["template"]["switched_to_2001"] is False and k["template"]["switched_to_2001"] is False,
       "族数 1 与 3 在同一「无覆盖缺口」条件下结论一致（都不转 2001）")
    # 真正触发 2001 的条件是**覆盖缺口**（本层级没有对应专项模板）
    kg = _r("深入分析这家公司的竞争格局与未来趋势", available=["S1", "S2"], objects=["某公司"])
    ck(kg["template"]["coverage_gap"]["families_without_template"] == ["deep_narrative"],
       f"个股 + 推演族 → 覆盖缺口应为 deep_narrative，实际 {kg['template']['coverage_gap']}")
    ck(kg["template"]["switched_to_2001"] is True and kg["template"]["初选"] == 2001,
       "存在覆盖缺口且个股层级 → 才可改选 2001")
    ck(kg["template"]["switch_reason"]["by_family_count"] is False,
       "即便改选 2001，也必须声明**不是**因族数")

    # ── K4：非个股层级不得越级 2001（红线保留）──
    ki = _r("行业层面：景气度与竞争格局如何演变", available=["D7"], objects=["电池行业"])
    ck(ki["template"]["level"] == "行业" and ki["template"]["2001_eligible"] is False,
       "行业层级 2001_eligible 必须 False")
    ck(2001 not in ki["template"]["allowed"], "行业层级允许集不得含 2001")
    ck(ki["template"]["switched_to_2001"] is False, "行业层级即使有覆盖缺口也不得转 2001")
    ck("blocked_by_level" in ki["template"]["switch_reason"],
       "行业层级有覆盖缺口时应如实登记「不得转 2001」")
    ck(ki["template"]["初选"] in (4, 5, 6), "行业层级应选行业级模板")

    # ── K5：多对象 → 对象 × 问题逐条对应，不串对象 ──
    km = _r("比较以下三家公司的盈利质量", objects=["甲公司", "乙公司", "丙公司"],
            object_level="个股", available=["D2"])
    ck(km["requirements"]["n_questions"] == 3 and km["requirements"]["multi_object"] is True,
       "3 对象 × 1 问 → 3 条记录")
    ck([q["object_id"] for q in km["requirements"]["questions"]] == ["甲公司", "乙公司", "丙公司"],
       "每个对象各自成条，不得串对象")
    ck(len({q["question_id"] for q in km["requirements"]["questions"]}) == 3, "问题编号必须唯一")
    ck(all(pq["template"]["level"] == "个股" for pq in km["per_question"]),
       "多对象任务不得调用不适用层级的模板")

    # ── K6：同一指标族下的多个问题不得被按族去重 ──
    ks = _r("这家公司毛利率高不高，以及净利率有没有改善", objects=["某公司"], available=["D2"])
    ck(ks["requirements"]["n_questions"] == 2, "同族两问应各留一条")
    ck(ks["requirements"]["n_families"] == 1, "两问同族 → n_families 应为 1")
    ck(ks["requirements"]["family_counts"].get("financial_compare") == 2,
       "family_counts 应计为 2（未被按族去重）")
    ck(len({q["question_id"] for q in ks["requirements"]["questions"]}) == 2, "两问编号不同")

    # ── K7：一个问题可涉及多个指标族 ──
    kd = _r("这家公司估值与财务风险的判断", objects=["某公司"])
    ck(len(kd["requirements"]["questions"]) == 1, "该句式应对应 1 个问题")
    ck(len(kd["requirements"]["questions"][0]["metric_families"]) == 2,
       "该问题应同时挂 valuation 与 financial_compare")

    # ── K8：交付形态多族取最强；但不得**无端**升成报告 ──
    ckd = _r("某公司：查询PE和资产负债率", available=["D1", "D7", "D2"])
    ck(ckd["deliverable"]["mode"] == "direct", f"取数类应为 direct，实际 {ckd['deliverable']['mode']}")
    ck(ckd["deliverable"]["per_family"] == {"valuation": "direct", "financial_compare": "direct"},
       "per_family 应逐族给出")
    ck(ckd["template"] is None, "不发报告 → 无模板选型")
    ck(ckd["requirements"]["n_questions"] == 2, "两问应各留一条")
    ck(ckd["requirements"]["questions"][0]["object_id"] == "某公司",
       "应把原话的对象前缀作为研究对象（不丢原始需求映射）")
    # 未识别指标族 + 取数深度 → **不得**声称要发报告
    kn = _r("这家公司研发投入的会计处理", objects=["某公司"])
    ck(kn["task_reading"]["3_metrics"]["families"] == [], "该文本无指标族命中")
    ck(kn["deliverable"]["mode"] != "report",
       f"未识别指标族时**不得**声称发报告，实际 {kn['deliverable']['mode']}")

    # ── K9：回退保持的「指标」为**全族** ──
    kf = _r("对某公司做估值分析（专项模板生成失败）", failed_tid=9, objects=["某公司"])
    ck(kf["fallback"]["keep"]["指标"] == ["valuation"], "回退 keep 的指标应为全族列表")
    kf3 = _r("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响",
             failed_tid=9, objects=["某公司"], available=["D1", "D7", "D2", "D4"])
    ck(kf3["fallback"]["keep"]["指标"] == ["valuation", "financial_compare", "announcement"],
       f"多族回退必须保持**全部**指标族，实际 {kf3['fallback']['keep']['指标']}")

    # ── K10：交付门接在 route() 上（**进入实际交付流程**）──
    kq = k["requirements"]["questions"]
    ans = {q["question_id"]: {"status": "answered"} for q in kq}
    kr = _r("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响",
            available=["D1", "D7", "D2", "D4"], objects=["某公司"], answers=ans)
    ck("coverage" in kr and kr["coverage"]["complete"] is True,
       "三问都有回答 → 交付门应判完成")
    ck(kr["delivery_blocked"] is False, "完成时不得标记阻塞")
    ans2 = dict(ans)
    ans2[kq[2]["question_id"]] = {"status": "unfinished", "reason": "material_insufficient"}
    kr2 = _r("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响",
             available=["D1", "D7", "D2", "D4"], objects=["某公司"], answers=ans2)
    ck(kr2["coverage"]["overall"] == "partial" and kr2["delivery_blocked"] is True,
       "一问未完成 → 整体部分完成且交付被阻塞")
    ck(kr2["coverage"]["coverage"]["ok"] is True,
       "覆盖情况应通过（问题都登记了），与完成情况分开")
    ans3 = {q["question_id"]: {"status": "answered"} for q in kq[:2]}
    kr3 = _r("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响",
             available=["D1", "D7", "D2", "D4"], objects=["某公司"], answers=ans3)
    ck(kr3["coverage"]["coverage"]["unregistered"], "漏答的问题应被识别为未登记（静默遗漏）")
    ck(kr3["coverage"]["complete"] is False, "有静默遗漏 → 不得判为完成")

    # ── K11：单族回归——行为与改前一致 ──
    kreg = _r("比较三家公司的盈利质量")
    ck(kreg["task_reading"]["3_metrics"]["family"] == "financial_compare", "单族首选族不变")
    ck(kreg["candidates"][:2] == ["D2", "S1"], "单族候选不变")
    ck(kreg["template"]["初选"] == 7, "单族模板初选不变（7）")
    ck(kreg["deliverable"]["mode"] == "report", "单族交付形态不变")
    ck(kreg["rank_criteria"] is None, "单族排序标准判定不变")
    ck("families" in kreg and kreg["families"] == ["financial_compare"], "新增 families 与单族一致")

    # ── K12：路由顺序文本含第 2 轮两条硬规则 ──
    ot = order_text()
    ck("多维需求一条不丢" in ot, "路由顺序文本须含「多维需求一条不丢」")
    ck("问题数量 ≠ 指标族数量" in ot, "路由顺序文本须含「问题数量 ≠ 指标族数量」")
    ck("不得用指标族数量" in ot, "路由顺序文本须含「不得用指标族数量」机械触发")
    ck("③ 指标" in ot and "① 研究问题" in ot, "六步顺序不得改变")

    # ── 检查清单执行器（cases/07 与 verify.py 走同一条路径）──
    demo = run_check_spec({"checks": [
        {"label": "demo-A", "type": "route", "input": CASE_INPUTS["A"],
         "assert": [{"path": "task_reading.3_metrics.family", "op": "eq", "value": "financial_compare"},
                    {"path": "candidates", "op": "none_of", "value": ["S11"]}]},
        {"label": "demo-case", "type": "case", "input": "H",
         "assert": [{"path": "action", "op": "eq", "value": "honest_limit"}]},
        {"label": "demo-cmp", "type": "compare",
         "input_a": "三家公司的盈利质量", "input_b": "一家公司的盈利质量",
         "assert": [{"path": "task_reading.3_metrics.family",
                     "other": "task_reading.3_metrics.family", "op": "eq"},
                    {"path": "task_reading.2_object_scope.batch_mode",
                     "other": "task_reading.2_object_scope.batch_mode", "op": "ne"}]},
        {"label": "demo-order", "type": "order",
         "assert": [{"path": "order_text", "op": "contains", "value": "③ 指标"}]},
        {"label": "demo-cap", "type": "cap_map", "family": "bias_rate",
         "assert": [{"path": "", "op": "eq", "value": ["S8", "D9"]}]},
    ]})
    ck(demo["pass"] and demo["n_checks"] == 7, f"run_check_spec 自跑未通过：{demo['n_fail']} 失败 / {demo['n_checks']} 项")
    for x in demo["results"]:
        if not x["pass"]:
            ck(False, f"执行器用例失败：{x['label']} —— {x['detail']}")
    ck(run_check_spec({"checks": [{"label": "bad", "type": "no_such"}]})["n_fail"] == 1,
       "未知检查类型应判失败，不得静默跳过")
    ck(run_check_spec({"checks": [{"label": "bad-a", "type": "route", "input": CASE_INPUTS["A"],
                                   "assert": [{"path": "candidates", "op": "contains", "value": "S11"}]}]})["n_fail"] == 1,
       "反例（A 含 S11）应被判为失败")

    print(f"selftest: {'全部通过' if not fails else '失败 %d 项' % len(fails)}（{n} 项）")
    for x in fails:
        print("  ✗ " + x)
    return 1 if fails else 0


def _print_case(key):
    text = CASE_INPUTS[key]
    kw = {}
    if key == "H":
        kw["denied"] = ["S11"]
    if key == "I":
        kw["failed_tid"] = 9
    r = route(text, **kw)
    tr = r["task_reading"]
    print(f"── 用例 {key}：{text}")
    print(f"   ① 研究问题 : {tr['1_research_question']['kind']}（{tr['1_research_question']['why']}）")
    print(f"   ② 对象范围 : 层级={tr['2_object_scope']['level']} 数量={tr['2_object_scope']['count']} "
          f"批量={tr['2_object_scope']['batch_mode']}")
    print(f"   ③ 指标     : {tr['3_metrics']['family']}（{tr['3_metrics']['why']}）")
    print(f"   ③ 全量族   : {r['families']}  ← 全部保留（dropped={r['dropped_families']} 已回填，不丢）")
    print(f"   ④ 时间     : {tr['4_time_range']['spec']}")
    print(f"   ⑤ 深度     : {tr['5_depth']['level']}")
    print(f"   ⑥ 候选     : {r['candidates']}  → 选用 {r['chosen']}"
          + (f" ｜ blocked={[b['id'] for b in r['blocked']]}" if r["blocked"] else ""))
    print(f"   交付形态   : {r['deliverable']['mode']}")
    if r.get("template"):
        t = r["template"]
        print(f"   模板       : 层级={t['level']} 初选={t['初选']}（{t['初选专长']}） 允许={t['allowed']} 2001可用={t['2001_eligible']}")
    if r.get("rank_criteria"):
        print(f"   排序标准   : {r['rank_criteria']['standard'][:70]}…")
    if r.get("criteria_note"):
        print(f"   口径声明   : {r['criteria_note']['need'][:70]}…")
    if r.get("plan"):
        for s in r["plan"]["stages"]:
            print(f"   子任务 {s['stage']}   : {s['goal']} ｜ 能力={s['capability']}")
    if r.get("batch_vs_analysis"):
        print(f"   批量说明   : {r['batch_vs_analysis']['note'][:70]}…")
    rq = r.get("requirements") or {}
    print(f"   需求清单   : {rq.get('n_questions')} 问 / {rq.get('n_objects')} 对象 / "
          f"族数={rq.get('n_families')}（**问题数 ≠ 族数**）")
    for q in rq.get("questions", []):
        print(f"     {q['question_id']:<14} {str(q['metric_families']):<40} "
              f"{q['execution_mode']:<17} slot={q['answer_slot']}")
    if r.get("not_needed"):
        print(f"   非必需     : {[x['id'] for x in r['not_needed']]}（本任务不强制发报告）")
    if r.get("fallback"):
        print(f"   回退       : 保持={r['fallback']['keep']} 候选={r['fallback']['candidates']}")
    print(f"   action     : {r['action']} —— {r['reason']}")
    if r.get("honesty"):
        for x in r["honesty"]:
            print(f"   边界声明   : {x}")
    print()


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "selftest":
        return selftest()
    if cmd == "order":
        print(order_text())
        return 0
    if cmd == "route":
        if not rest:
            print("用法: route \"<任务原话>\"")
            return 2
        print(json.dumps(_r(rest[0]), ensure_ascii=False, indent=2))
        return 0
    if cmd == "cases":
        keys = rest or list(CASE_INPUTS)
        for k in keys:
            if k in CASE_INPUTS:
                _print_case(k)
        return 0
    print(f"未知子命令 {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

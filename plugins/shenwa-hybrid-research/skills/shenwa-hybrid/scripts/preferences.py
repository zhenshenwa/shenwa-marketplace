# -*- coding: utf-8 -*-
"""preferences.py · 偏好语义与交付形态（本包唯一的偏好实现）

v1.9.0 新增。要修的旧行为：

  ① **偏好一锅烩** —— 把「语言 / 长度 / 图表 / 结构 / 关注重点」这类**表达偏好**
     与「研究期限 / 比较目标 / 风险限制 / 明确指定的方法」这类**任务约束**混在一起，
     于是「用户进取」被当成「结论可以更乐观」，甚至去改情景概率；
  ② **风格改事实** —— 风险容忍度与结论语气被写进派生影响，出现
     「因为用户进取就增加上行情景概率或权重」这类规则；
  ③ **篇幅决定配比** —— 按内容篇幅强制 50/50，偏离就「自动回退」，与本轮
     「关键问题回答完整、重要结论有依据、证据不足可见」的要求冲突；
  ④ **六段强制** —— 六段结构被当成所有任务的固定格式，短答任务被迫附上全部深挖原文；
  ⑤ **零散交付** —— 复杂任务被拆成一堆互不相干的小交付，重复取数、结论各说各话；
  ⑥ **自动扩写** —— 「仅查询」「仅整理用户提供材料」被自动扩展成新的深度研究。

本模块把上述口径变成**可执行事实源**：文档由它复述，`selftest()` 与
`cases/09` 走同一条真实路径。不联网、不调用任何外部接口、不读写任何凭据。

CLI:
  python preferences.py selftest
  python preferences.py forms
  python preferences.py split            # 打印偏好二分
  python preferences.py rules            # 打印现行规则与已删除规则
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capability as cap  # noqa: E402

# ═══════════════════════════════════════════════════════════════════
# 0. 偏好二分（要求 1）
# ═══════════════════════════════════════════════════════════════════
# ⚠️ 关键设计：**表达偏好只影响「怎么说」，任务约束只影响「研究什么」**。
#    两者都**不得**改写事实、关键数字、证据状态或方法适用条件（要求 2）。

EXPRESSION_PREFS = {
    "language": "语言（zh / en / bilingual）—— 只影响行文语言",
    "length": "长度（brief / standard / dd）—— 只影响呈现详略，**不改配比、不改结论强度**",
    "visual": "图表（none / key / full）—— 只影响附几张图",
    "structure": "结构（短答 / 比较表 / 简析 / 完整报告）—— 只影响组织形式",
    "focus": "关注重点（focus_metrics）—— 只影响**先看哪一类指标**，不改指标口径",
}

TASK_CONSTRAINTS = {
    "horizon": "研究期限（long / medium / short）—— 决定取数区间与前瞻窗口",
    "comparison_target": "比较目标（与谁比、比什么口径）—— 决定可比性检查的对象",
    "risk_limit": "风险限制（可承受回撤 / 禁投范围）—— 决定**是否输出**与**是否需要额外警示**，不改客观判断",
    "method": "明确指定的方法（valuation_method 显式指定）—— 只在**必要输入齐备且对象适用**时采用",
}

#: 不得被表达偏好或任务约束改写的**五项不变式**（要求 2 / 3）
FACT_INVARIANTS = ("facts", "key_numbers", "evidence_state", "verdict", "method_applicability")

#: 允许随偏好变化的**呈现维度**
PRESENTATION_ONLY = ("order", "wording", "tone", "accent", "emphasis", "language", "form")

#: 证据五态（与 `evidence.py` 同源，`selftest` 会交叉核对，避免两处漂移）
EVIDENCE_STATES = ("verified", "unverified", "conflict", "pending", "missing")


def split_preferences(profile: dict) -> dict:
    """把一份偏好配置切成**表达偏好**与**任务约束**，并列出未分类键。

    ⚠️ 「未分类」不等于「可忽略」—— 它们进入「执行参数」，同样**不得**改写事实。
    """
    expr, cons, unclassified = {}, {}, []
    for k, v in (profile or {}).items():
        if k in EXPRESSION_PREFS:
            expr[k] = v
        elif k in TASK_CONSTRAINTS:
            cons[k] = v
        else:
            unclassified.append(k)
    return {
        "expression": expr,
        "constraints": cons,
        "unclassified": sorted(unclassified),
        "rule": "表达偏好只影响「怎么说」；任务约束只影响「研究什么」；"
                "两者都**不得**改写事实 / 关键数字 / 证据状态 / 方法适用条件。",
    }


# ═══════════════════════════════════════════════════════════════════
# 1. 不变式守卫（要求 2 / 3 / 10）
# ═══════════════════════════════════════════════════════════════════

def guard_invariants(before: dict, after: dict) -> list[dict]:
    """比较「换偏好前后」的两份结果，返回**被改动的不变式**清单（空 = 合规）。

    只比较 `FACT_INVARIANTS` 五项；`order` / `wording` / `tone` 等呈现维度**允许**不同。
    """
    violations = []
    for key in FACT_INVARIANTS:
        b, a = before.get(key), after.get(key)
        if b != a:
            violations.append({"invariant": key, "before": b, "after": a})
    return violations


def assert_invariants(before: dict, after: dict, *, context: str = "") -> dict:
    v = guard_invariants(before, after)
    return {"ok": not v, "violations": v, "context": context,
            "rule": "风格 / 风险容忍度 / 结论语气**不得**改变事实、证据状态或方法适用条件。"}


# ═══════════════════════════════════════════════════════════════════
# 2. 表达变体（要求 2 / 3；用例 A）
# ═══════════════════════════════════════════════════════════════════

#: 三条可复用的**呈现顺序**（只是换个说法/顺序，不改内容）
BIAS_ORDER = {
    "conservative": ["counter", "direction", "condition"],
    "neutral":      ["direction", "counter", "condition"],
    "aggressive":   ["direction", "condition", "counter"],
}

#: 段首措辞（`{body}` 由条目正文填入）
BIAS_LEAD = {
    "conservative": "具备条件但需验证：",
    "neutral": "并列利弊：",
    "aggressive": "方向性判断：",
}

_BIAS_ALIAS = {"conservative": "conservative", "neutral": "neutral", "aggressive": "aggressive",
               "审慎": "conservative", "中性": "neutral", "积极": "aggressive", "进取": "aggressive"}


def _norm_bias(bias) -> str:
    return _BIAS_ALIAS.get(str(bias), "neutral")


def render_conclusion(spec: dict, bias: str = "neutral") -> dict:
    """按 `conclusion_bias` 渲染结论：**只改顺序与措辞**。

    `spec` 形如::

        {"items": [{"role": "direction"|"counter"|"condition", "text": "..."}],
         "facts": [...], "key_numbers": {...}, "evidence_state": "unverified",
         "verdict": "..."}

    `facts` / `key_numbers` / `evidence_state` / `verdict` **原样带出**（由
    `guard_invariants` 复核），只有 `order` / `wording` / `text` 随 bias 变化。
    """
    b = _norm_bias(bias)
    items = list(spec.get("items") or [])
    by_role = {}
    for it in items:
        by_role.setdefault(it.get("role", "direction"), []).append(it)
    order = BIAS_ORDER[b]
    ordered = []
    for role in order:
        ordered.extend(by_role.get(role, []))
    for it in items:                      # 未登记的 role 追加在后，不丢内容
        if it not in ordered:
            ordered.append(it)

    body = "；".join(str(x.get("text", "")) for x in ordered)
    return {
        "bias": b,
        "order": [x.get("role") for x in ordered],
        "items": ordered,
        "text": (BIAS_LEAD[b] + body) if body else BIAS_LEAD[b].rstrip("："),
        "lead": BIAS_LEAD[b],
        # ── 以下四项为不变式，逐字带出，不得改动 ──
        "facts": list(spec.get("facts") or []),
        "key_numbers": dict(spec.get("key_numbers") or {}),
        "evidence_state": spec.get("evidence_state"),
        "verdict": spec.get("verdict"),
        "presentation": {"order": [x.get("role") for x in ordered], "wording": BIAS_LEAD[b]},
        "rule": "审慎 / 中性 / 积极**只改呈现顺序与措辞**；事实、关键数字、证据状态、结论判定一律不动。",
    }


# ═══════════════════════════════════════════════════════════════════
# 3. 风险容忍度（要求 2；用例 B）
# ═══════════════════════════════════════════════════════════════════

#: 风险容忍度**只**改变这三样（关注角度），**不改**客观判断与情景概率
RISK_PRESENTATION = {
    "conservative": {"risk_section": "展开", "accent": "回撤与不确定性",
                     "extra_warning": "列举失效条件与需要人工复核的项"},
    "balanced":     {"risk_section": "标准", "accent": "风险与收益并重",
                     "extra_warning": "标准风险披露"},
    "aggressive":   {"risk_section": "聚焦关键项", "accent": "关键风险（不铺陈）",
                     "extra_warning": "列出触发条件与失效条件"},
}

#: 本轮**删除**的规则（不得再作为生效规则出现）
RETIRED_RULES = (
    "增加上行情景权重",
    "增加上行情景概率",
    "上行情景概率提高",
    "未写明则自动回退到 50/50",
    "未写明则自动回退 50/50",
    "否则按 baseline 执行",
)


def apply_risk_tolerance(level: str, judgement: dict, scenarios: dict) -> dict:
    """风险容忍度 → **只改关注角度**。

    `judgement`（客观判断）与 `scenarios`（情景及其概率）**原样返回**，
    并由 `objective_unchanged` / `probability_unchanged` 两个判定位显式声明。
    """
    lv = _norm_bias(level) if level in ("conservative", "aggressive") else \
        (level if level in ("conservative", "balanced", "aggressive") else "balanced")
    if level in ("保守",):
        lv = "conservative"
    if level in ("中性",):
        lv = "balanced"
    if level in ("进取", "积极"):
        lv = "aggressive"
    pres = RISK_PRESENTATION.get(lv, RISK_PRESENTATION["balanced"])
    return {
        "level": lv,
        "presentation": pres,
        # ── 以下两项**必须是原件**（不变式）──
        "judgement": dict(judgement or {}),
        "scenarios": {k: dict(v) if isinstance(v, dict) else v
                      for k, v in (scenarios or {}).items()},
        "objective_unchanged": True,
        "probability_unchanged": True,
        "rule": "风格与风险容忍度**不得**改变客观判断，也**不得**改变情景概率或权重；"
                "它只改变「风险段详略」与「强调哪一类风险」。",
    }


# ═══════════════════════════════════════════════════════════════════
# 4. 假设情景推演（要求 3）
# ═══════════════════════════════════════════════════════════════════

def scenario_variation(base_scenarios: dict, assumptions: dict, *, requester="user") -> dict:
    """用户**明确要求**不同假设情景时 → 可以推演，但每条必须标为**假设变化**。

    ⚠️ 假设变化（`hypothesis_variation`）**永不**写成事实变化（`fact_change`）。
    """
    out = {}
    for name, scen in (base_scenarios or {}).items():
        s = dict(scen) if isinstance(scen, dict) else {"value": scen}
        if name in (assumptions or {}):
            s = dict(s)
            s["changed_inputs"] = assumptions[name]
            s["kind"] = "hypothesis_variation"
            s["label"] = "[假设变化]"
            s["baseline_value"] = (scen.get("value") if isinstance(scen, dict) else scen)
            s["rule"] = "输入被替换的是**假设**，不是已披露事实；不得写成事实变化。"
        else:
            s = dict(s)
            s.setdefault("kind", "baseline")
            s.setdefault("label", "[基准情景]")
        out[name] = s
    return {
        "requester": requester,
        "scenarios": out,
        "kinds": sorted({v.get("kind") for v in out.values()}),
        "fact_changes_claimed": [],
        "rule": "可推演，但**必须显式标注为假设变化**；不得把假设变化写成事实变化。",
    }


# ═══════════════════════════════════════════════════════════════════
# 5. 数据权重的语义（要求 4）
# ═══════════════════════════════════════════════════════════════════

WEIGHT_SEMANTICS = "reading_attention_only"

WEIGHT_DISCLAIMER = ("数据权重只表示**阅读与关注权重**（先看哪一类、看得多细），"
                     "**不是**经校准的量化模型权重 —— 本包不做权重校准，"
                     "也不据此计算加权评分或目标价。")


def weights_disclaimer() -> dict:
    return {
        "semantics": WEIGHT_SEMANTICS,
        "disclaimer": WEIGHT_DISCLAIMER,
        "affects": ["关注角度（先看哪一类）", "取数勾选（必须成对出现）", "版式段落详略"],
        "not_affects": ["结论口径", "数字", "证据状态", "方法适用条件", "任何加权评分"],
        "rule": "权重若只是**阅读 / 关注权重**，必须**明确说明**，不得冒充已校准的量化模型。",
    }


def weight_effect(weights: dict) -> dict:
    """把权重翻译成**可执行动作**：只决定「勾选哪些能力」，不产生任何数值。"""
    w = dict(weights or {})
    need = []
    if w.get("capital_flow", 0) > 0:
        need.append("D3")
    if w.get("technical", 0) > 0:
        need.append("D9")
    return {
        "weights": w,
        "must_check": need,
        "produces_number": False,
        "note": "权重只决定**勾选哪些能力**（否则权重设置不会生效）；"
                "它**不产出任何数字**，也不改变结论口径。",
    }


# ═══════════════════════════════════════════════════════════════════
# 6. 交付形态（要求 5 / 6 / 7；用例 C / D / F）
# ═══════════════════════════════════════════════════════════════════

FORMS = ("short_answer", "comparison_table", "brief", "full_report")

FORM_SPEC = {
    "short_answer": {
        "zh": "短答",
        "required": ["关键结论", "关键依据", "必要限制"],
        "attach_full_text": False,
        "archive_entry": True,
        "six_section": False,
        "why": "简单任务简洁交付：给结论 + 一条关键依据 + 该结论的必要限制。",
    },
    "comparison_table": {
        "zh": "比较表",
        "required": ["比较对象", "指标口径", "所属期间", "单位", "排序或对比标准"],
        "attach_full_text": False,
        "archive_entry": True,
        "six_section": False,
        "why": "对比类任务用表更清楚；口径四要素必须先行一致。",
    },
    "brief": {
        "zh": "简析",
        "required": ["结论", "关键数据", "简析", "证据限制"],
        "attach_full_text": False,
        "archive_entry": True,
        "six_section": False,
        "why": "数据足够解释时的「数据 + 简析」，不强制另发报告。",
    },
    "full_report": {
        "zh": "完整报告",
        "required": ["结论", "证据", "假设", "反证", "局限"],
        "attach_full_text": True,
        "archive_entry": True,
        "six_section": True,
        "six_section_optional": True,
        "why": "复杂任务保留必要证据、假设、反证与局限；"
               "六段结构是**适配完整报告的一种形式**，不是所有任务的固定格式。",
    },
}


def choose_form(*, depth: str, question_kind: str = "fact", comparative: bool = False,
                passthrough: bool = False, requested: str | None = None) -> dict:
    """按**任务**选交付形态。**不看篇幅，也不看 50/50 配比。**

    - `passthrough`（仅查询 / 仅整理用户提供材料）→ 只出短答或比较表，**不升级**为完整报告；
    - `depth` → `fact` 短答 / `state` 简析 / `list` 比较表或短答 / `comparison` 比较表 /
      `deep_research` 完整报告。
    """
    refused = []
    if requested:
        if requested not in FORMS:
            refused.append({"requested": requested, "why": f"未知形态（可选 {list(FORMS)}）"})
        elif passthrough and requested == "full_report":
            refused.append({"requested": requested,
                            "why": "「仅查询 / 仅整理用户提供材料」**不得**自动扩展成新的深度研究"})
        else:
            return {"form": requested, "why": "按明确指定的形态交付", "refused": refused,
                    "passthrough": passthrough, "depth": depth}

    if passthrough:
        form = "comparison_table" if comparative else "short_answer"
        why = "仅查询 / 仅整理用户提供材料 → 短答或比较表，**不扩展成新研究**"
    elif depth in ("deep_research", "report"):
        form, why = "full_report", "深度研究 → 完整报告（六段只是其中一种适配形式）"
    elif depth == "comparison":
        form, why = "comparison_table", "比对 → 比较表"
    elif depth == "list":
        form, why = ("comparison_table" if comparative else "short_answer"), "名单 → 表或短答"
    elif depth == "state":
        form, why = "brief", "状态 → 数据 + 简析，不强制另发报告"
    else:
        form, why = "short_answer", "取数 → 短答"
    return {"form": form, "why": why, "refused": refused, "passthrough": passthrough, "depth": depth}


def form_spec(form: str) -> dict:
    spec = dict(FORM_SPEC.get(form) or {})
    spec.setdefault("form", form)
    spec["rule"] = ("**按任务选形态**，不按篇幅、不按 50/50 配比；"
                    "任何形态都**不得**以「结构简化」为由删掉关键内容。")
    spec["coverage_requirement"] = ("关键问题回答完整；重要结论有依据；证据不足**可见**"
                                    "（写明缺什么、影响什么）")
    return spec


# ═══════════════════════════════════════════════════════════════════
# 7. 原文归档（要求 7；沿用第 2 轮「完整归档」）
# ═══════════════════════════════════════════════════════════════════

def archive_policy(form: str, *, aid: str | None = None, tid=None,
                   created_at: str | None = None, source: str | None = None) -> dict:
    """归档策略：**原文一律完整归档**；正文是否附全文由形态决定。

    - 短答 / 比较表 / 简析 → 正文**不附全文**，按需要提供**归档入口**；
    - 完整报告 → 正文可含「分析主体」段（来自归档件）。
    """
    spec = form_spec(form)
    return {
        "form": form,
        "body_attaches_full_text": bool(spec.get("attach_full_text")),
        "archive_keeps_full_text": True,          # ← 第 2 轮要求，任何形态都不变
        "entry": {"aid": aid, "template_tid": tid, "generated_at": created_at, "source": source},
        "entry_rule": "归档入口必须能唯一定位到归档件：报告编号（aid）+ 生成时间 + 来源（模板 tid / 站点）。",
        "archive_rule": "原文**完整归档**（不摘要 / 不改写 / 不省略章节，含已确认错误的段落）；"
                        "**正文是否附全文**由交付形态决定 —— 摘要档不强制附全文，但不等于不归档。",
    }


# ═══════════════════════════════════════════════════════════════════
# 8. 复合任务（要求 8；用例 E）
# ═══════════════════════════════════════════════════════════════════

def composite_task(name: str, subtasks: list[dict]) -> dict:
    """一个总任务包含多个子任务：**共享经过验证的数据**、形成**统一结论**、避免重复取数。

    `subtasks` 每项形如::

        {"id": "s1", "goal": "...", "metric_family": "right_side_state",
         "data": {"600519.SH:close": 1680.0}, "evidence_state": "verified"}
    """
    shared: dict = {}
    fetch_requests: list[str] = []
    conflicts: list[dict] = []
    owners: dict = {}

    for st in subtasks or []:
        for key, val in (st.get("data") or {}).items():
            if key in shared and shared[key] != val:
                conflicts.append({"key": key, "values": [shared[key], val],
                                  "owners": [owners.get(key), st.get("id")]})
            shared.setdefault(key, val)
            owners.setdefault(key, st.get("id"))
            if key not in fetch_requests:          # ★ 去重：同一份数据只取一次
                fetch_requests.append(key)

    n_declared = sum(len(st.get("data") or {}) for st in (subtasks or []))
    states = sorted({st.get("evidence_state") for st in (subtasks or []) if st.get("evidence_state")})
    return {
        "task": name,
        "subtasks": [{"id": st.get("id"), "goal": st.get("goal"),
                      "metric_family": st.get("metric_family")} for st in (subtasks or [])],
        "n_subtasks": len(subtasks or []),
        "shared_verified_data": shared,
        "fetch_requests": fetch_requests,
        "duplicate_fetches": n_declared - len(fetch_requests),
        "conflicts": conflicts,
        "evidence_states": states,
        "unified_conclusion_required": True,
        "rule": "复杂任务允许「一个总任务包含多个子任务」：**共享经过验证的数据**、"
                "**形成统一结论**（单一结论段，不各说各话）、**避免重复取数**与零散交付。"
                "同一指标出现分歧时**并列原值与来源**，不得静默择一。",
    }


# ═══════════════════════════════════════════════════════════════════
# 9. 「不自动扩展」（要求 9；用例 F）
# ═══════════════════════════════════════════════════════════════════

PASSTHROUGH_WORDS = ("仅查询", "只查询", "仅查", "只查", "查一下", "看看是多少", "是多少",
                     "仅整理", "只整理", "帮我整理", "整理一下", "整理成", "转成表", "转换成表",
                     "归纳我提供", "我提供", "已给的", "按我给的", "这份材料", "这些材料",
                     "下列材料", "以下材料", "只做整理", "不要分析", "无需分析")

EXPANSION_FORBIDDEN = ("发深挖报告", "发布报告", "扩展研究", "自行补充外部资料",
                       "引入新标的", "追加情景推演")


def classify_task(text: str) -> dict:
    """识别「仅查询 / 仅整理用户提供材料」→ **不得**自动扩展成新的深度研究。"""
    t = text or ""
    hits = [w for w in PASSTHROUGH_WORDS if w in t]
    passthrough = bool(hits)
    if passthrough:
        kind = "organize" if any(w in t for w in ("整理", "转成", "归纳", "转换成")) else "query"
    else:
        kind = "research"
    return {
        "kind": kind,
        "passthrough": passthrough,
        "matched": hits,
        "expansion_allowed": not passthrough,
        "forbidden": list(EXPANSION_FORBIDDEN) if passthrough else [],
        "why": ("命中「仅查询 / 仅整理用户提供材料」语汇 → 只在**用户提供的材料范围内**交付"
                if passthrough else "未命中直通语汇 → 按研究对象与指标族正常处理"),
        "rule": "「仅查询」「仅整理用户提供材料」**不自动扩展**成新的深度研究；"
                "缺口如实说明，不擅自补外部研究。",
    }


# ═══════════════════════════════════════════════════════════════════
# 10. 输出格式转换（要求 10；用例 G）
# ═══════════════════════════════════════════════════════════════════

CONVERTIBLE_FORMATS = ("markdown", "table", "xlsx", "md")

#: PDF 的**来源标签** —— 官方原文 PDF 不得冒充专家包综合报告
PDF_LABELS = {
    "shenwa_official": "深挖官方原文 PDF（**原始报告**）",
    "composite": "**专家包综合报告**",
}


def label_for_pdf(kind: str) -> dict:
    label = PDF_LABELS.get(kind)
    return {
        "kind": kind,
        "label": label,
        "ok": label is not None,
        "rule": "深挖官方原文 PDF 必须标为**原始报告**，不得冒充专家包综合报告；两者标签不得互换。",
    }


def pdf_capability() -> dict:
    """本包**不含**综合版 PDF 生成链路 → 如实说明并交付支持的格式。"""
    return {
        "composite_pdf_supported": False,
        "why": "本包没有综合版 PDF 的生成链路（不自行排版、不重绘图表）；"
               "深挖官方 PDF 是该报告的**原始文件**，不是综合报告的成品。",
        "deliver": ["markdown", "table", "xlsx"],
        "rule": "当前环境无法生成综合版 PDF 时**说明并交付支持的格式**，不得用官方原文 PDF 顶替综合报告。",
    }


def convert_format(payload: dict, fmt: str) -> dict:
    """格式转换：结论 / 数字 / 验证状态**逐字不变**，只换序列化形式。"""
    if fmt not in CONVERTIBLE_FORMATS:
        return {"format": fmt, "ok": False, "why": f"不支持的格式（可选 {list(CONVERTIBLE_FORMATS)}）",
                "invariants_ok": False}
    concl = list(payload.get("conclusions") or [])
    nums = dict(payload.get("numbers") or {})
    state = payload.get("verification_state")
    if fmt in ("table", "xlsx"):
        text = "| 项 | 值 |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in nums.items())
    else:
        text = "\n".join([*[f"- {c}" for c in concl], *[f"- {k}: {v}" for k, v in nums.items()]])
    return {
        "format": fmt,
        "ok": True,
        "text": text,
        # ── 三项不变式：逐字带出 ──
        "conclusions": concl,
        "numbers": nums,
        "verification_state": state,
        "invariants_ok": (concl == list(payload.get("conclusions") or [])
                          and nums == dict(payload.get("numbers") or {})
                          and state == payload.get("verification_state")),
        "rule": "格式转换**不得**改变结论、数字与验证状态；只换序列化形式。",
    }


# ═══════════════════════════════════════════════════════════════════
# 11. 前几轮回归（要求 H）
# ═══════════════════════════════════════════════════════════════════

#: 前几轮关键风险 → 承载它们的模块与入口（H 项直接在**生产模块**上跑，不另写一套）
REGRESSION_TARGETS = (
    ("财务矛盾 / 指标混用", "data_qc", "selftest"),
    ("未核实证据 / 归档分层", "evidence", "selftest"),
    ("估值调整 / 方法与边界", "valuation", "selftest"),
    ("任务路由 / 能力边界", "capability", "selftest"),
    ("任务路由 / 六步顺序", "router", "selftest"),
)


def regression_suite() -> dict:
    """跑前几轮的关键回归（在**各自的真实实现**上），确认没有被本轮改动破坏。

    另加两项**功能性**回归（无 selftest 的模块）：
      · 右侧日期切片 —— `right_side.cut_cross_section()` 必须先切截面再分桶；
      · 发布超时 —— 由 `shenwa_api` 的分支覆盖（见 `cases/08` 与 `check_client()`）。
    """
    import importlib
    results = []
    for label, modname, entry in REGRESSION_TARGETS:
        try:
            m = importlib.import_module(modname)
            rc = getattr(m, entry)()
            results.append({"label": label, "module": modname, "rc": rc, "ok": rc == 0})
        except Exception as e:                                   # noqa: BLE001
            results.append({"label": label, "module": modname, "rc": None, "ok": False,
                            "error": f"{type(e).__name__}: {e}"})
    try:
        rs = importlib.import_module("right_side")
        rows = [{"date": "2026-09-30", "code": "A"}, {"date": "2026-09-30", "code": "B"},
                {"date": "2026-09-29", "code": "A"}, {"date": "2026-09-29", "code": "B"}]
        latest, cut = rs.cut_cross_section(rows)          # 返回 (目标日期, 该日截面)
        slice_ok = (len(cut) == 2 and latest == "2026-09-30")
        results.append({"label": "右侧日期切片（先切截面）", "module": "right_side",
                        "rc": 0 if slice_ok else 1, "ok": slice_ok,
                        "detail": f"截面 {latest} 取 {len(cut)} 条（应为 2）"})
    except Exception as e:                                       # noqa: BLE001
        results.append({"label": "右侧日期切片（先切截面）", "module": "right_side",
                        "rc": None, "ok": False, "error": f"{type(e).__name__}: {e}"})
    n_fail = sum(1 for r in results if not r["ok"])
    return {"pass": n_fail == 0, "n": len(results), "n_fail": n_fail, "results": results,
            "rule": "回归只覆盖前几轮关键风险及本轮直接影响，**不扩展成新的功能开发**。"}


# ═══════════════════════════════════════════════════════════════════
# 12. 规则文本（供文档复述与条款扫描）
# ═══════════════════════════════════════════════════════════════════

def rules_text() -> str:
    return """# 偏好机制与交付形态（v1.9.0）

## 一、偏好二分
表达偏好：语言 / 长度 / 图表 / 结构 / 关注重点 —— 只影响「怎么说」。
任务约束：研究期限 / 比较目标 / 风险限制 / 明确指定的方法 —— 只影响「研究什么」。
**两者都不得改写事实、关键数字、证据状态、方法适用条件。**

## 二、四条不变式
1. 风格、风险容忍度与结论语气**不改变**事实、证据状态或方法适用条件。
2. 风险容忍度**不改变**客观判断，也**不改变**上行情景概率或权重。
3. 用户明确要求不同假设情景时可以推演，但必须标为**假设变化**，不得写成事实变化。
4. 数据权重若只是**阅读 / 关注权重**，必须明确说明，不冒充已校准的量化模型。

## 三、交付形态（按任务选，不按篇幅选）
短答 / 比较表 / 简析 / 完整报告。
**六段结构**保留为**适配完整报告的一种形式**，不强制用于所有任务。
关键问题回答完整；重要结论有依据；**证据不足可见**。

## 四、原文归档
任何形态都**完整归档**原文（第 2 轮要求，含已确认错误的段落）。
短答 / 比较表 / 简析的**正文不附全文**，按需要提供**归档入口**（aid + 生成时间 + 来源）。

## 五、复合任务
复杂任务允许「一个总任务包含多个子任务」：共享经过验证的数据、形成统一结论、
避免重复取数与零散交付。

## 六、不自动扩展
「仅查询」「仅整理用户提供材料」**不**自动扩展成新的深度研究。

## 七、格式转换
转换**不得**改变结论、数字与验证状态。
深挖官方原文 PDF 标为**原始报告**，不得冒充专家包综合报告；
当前环境无法生成综合版 PDF 时**说明并交付支持的格式**。

## 八、已删除的规则（不得再作为生效规则）
- 「因为用户进取就增加上行情景概率或权重」
- 「偏离基准超 ±10% 未写明理由则自动回退 50/50」
- 「所有任务一律套六段结构」
"""


# ═══════════════════════════════════════════════════════════════════
# 13. 检查清单执行器（cases/09 与 verify.py 走同一条真实路径）
# ═══════════════════════════════════════════════════════════════════

#: 用例输入的口径与 selftest 共用同一份，避免两处漂移
FACT_SPEC_A = {
    "items": [
        {"role": "direction", "text": "公司在动力电池环节的份额稳固"},
        {"role": "counter", "text": "应收账款周转天数较上年拉长"},
        {"role": "condition", "text": "若毛利率下滑 3pct，安全边际将被侵蚀"},
    ],
    "facts": ["公司为动力电池环节头部供应商", "应收账款周转天数同比拉长"],
    "key_numbers": {"收盘价": 1680.0, "PE": 15.98, "股息率": "2.80%"},
    "evidence_state": "unverified",
    "verdict": "待验证（关键证据未独立核实）",
}

RISK_JUDGEMENT_B = {"客观判断": "估值处于历史中位", "方向": "中性偏谨慎", "关键风险": ["毛利率下滑"]}
RISK_SCENARIOS_B = {
    "乐观": {"value": "上行区间", "probability": 0.25},
    "中性": {"value": "中枢区间", "probability": 0.50},
    "悲观": {"value": "下行区间", "probability": 0.25},
}

CONVERT_PAYLOAD_G = {
    "conclusions": ["份额稳固但需验证", "估值处于历史中位"],
    "numbers": {"营收": "1,236 亿", "PE": 15.98},
    "verification_state": "部分校验",
}


def run_check_spec(spec: dict) -> dict:
    """执行 cases/09 的检查清单。

    支持的类型：`prefs` / `variant` / `risk` / `scenario` / `weights` / `form` /
    `archive` / `composite` / `classify` / `convert` / `label` / `pdf` / `invariant` /
    `regression` / `rules`；能力类（cap / cap_map / …）委派给 `capability.py`。
    """
    items = []

    def add(label, ok, detail):
        items.append({"label": label, "pass": bool(ok), "detail": detail})

    for ch in spec.get("checks", []):
        kind = ch.get("type")
        label = ch.get("label") or kind

        if kind in cap.LOCAL_CHECK_TYPES:
            items.extend(cap.run_check_spec({"checks": [ch]})["results"])
            continue

        try:
            if kind == "prefs":
                result = split_preferences(ch.get("input") or {})
            elif kind == "invariant":
                inp = ch.get("input") or {}
                result = assert_invariants(inp.get("before") or {}, inp.get("after") or {},
                                           context=inp.get("context", ""))
            elif kind == "variant":
                inp = ch.get("input") or {}
                result = render_conclusion(inp.get("spec") or FACT_SPEC_A, inp.get("bias", "neutral"))
            elif kind == "risk":
                inp = ch.get("input") or {}
                result = apply_risk_tolerance(inp.get("level", "balanced"),
                                              inp.get("judgement") or RISK_JUDGEMENT_B,
                                              inp.get("scenarios") or RISK_SCENARIOS_B)
            elif kind == "scenario":
                inp = ch.get("input") or {}
                result = scenario_variation(inp.get("base") or RISK_SCENARIOS_B,
                                            inp.get("assumptions") or {"乐观": {"毛利率": "持平"}},
                                            requester=inp.get("requester", "user"))
            elif kind == "weights":
                result = weights_disclaimer()
                if ch.get("with_effect"):
                    result = dict(result, **weight_effect(ch["with_effect"]))
            elif kind == "form":
                result = choose_form(**(ch.get("input") or {}))
            elif kind == "formspec":
                result = form_spec(ch.get("input", "short_answer"))
            elif kind == "archive":
                inp = ch.get("input") or {}
                result = archive_policy(inp.pop("form", "short_answer"), **inp)
            elif kind == "composite":
                inp = ch.get("input") or {}
                result = composite_task(inp.get("name", "复合任务"), inp.get("subtasks") or [])
            elif kind == "classify":
                result = classify_task(ch.get("input", ""))
            elif kind == "convert":
                inp = ch.get("input") or {}
                result = convert_format(inp.get("payload") or CONVERT_PAYLOAD_G,
                                        inp.get("format", "markdown"))
            elif kind == "label":
                result = label_for_pdf(ch.get("input", "shenwa_official"))
            elif kind == "pdf":
                result = pdf_capability()
            elif kind == "regression":
                result = regression_suite()
            elif kind == "rules":
                result = {"rules_text": rules_text()}
            else:
                add(label, False, f"preferences 不处理的检查类型 {kind!r}")
                continue
        except Exception as e:                                   # noqa: BLE001
            add(label, False, f"执行失败：{type(e).__name__}: {e}")
            continue

        for r in cap.run_asserts(result, ch.get("assert", [])):
            add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")

    n_fail = sum(1 for x in items if not x["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail, "results": items}


# ═══════════════════════════════════════════════════════════════════
# 14. 自检
# ═══════════════════════════════════════════════════════════════════

def selftest() -> int:
    fails = []
    n = 0

    def ck(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fails.append(msg)

    # ── 偏好二分（要求 1）──
    sp = split_preferences({"language": "zh", "length": "brief", "visual": "key",
                            "structure": "short_answer", "focus": "profit_quality",
                            "horizon": "medium", "comparison_target": "同业三家",
                            "risk_limit": "回撤 20%", "method": "DCF",
                            "data_weights": {}})
    ck(set(sp["expression"]) == {"language", "length", "visual", "structure", "focus"},
       f"表达偏好应为 5 项，实际 {sorted(sp['expression'])}")
    ck(set(sp["constraints"]) == {"horizon", "comparison_target", "risk_limit", "method"},
       f"任务约束应为 4 项，实际 {sorted(sp['constraints'])}")
    ck("data_weights" in sp["unclassified"], "data_weights 应登记为未分类（执行参数）")

    # ── A：审慎 vs 积极 → 事实/数字/证据状态一致，顺序/措辞不同 ──
    cons = render_conclusion(FACT_SPEC_A, "conservative")
    aggr = render_conclusion(FACT_SPEC_A, "aggressive")
    ck(not guard_invariants(cons, aggr), f"A 不变式被改动：{guard_invariants(cons, aggr)}")
    ck(cons["facts"] == aggr["facts"] == FACT_SPEC_A["facts"], "A 事实应逐字一致")
    ck(cons["key_numbers"] == aggr["key_numbers"] == FACT_SPEC_A["key_numbers"], "A 关键数字应一致")
    ck(cons["evidence_state"] == aggr["evidence_state"] == "unverified", "A 证据状态应一致")
    ck(cons["verdict"] == aggr["verdict"], "A 结论判定应一致")
    ck(cons["order"] != aggr["order"], f"A 呈现顺序应不同：{cons['order']} vs {aggr['order']}")
    ck(cons["lead"] != aggr["lead"], "A 措辞应不同")
    ck(cons["order"] == ["counter", "direction", "condition"], "审慎应先列反面证据")
    ck(aggr["order"] == ["direction", "condition", "counter"], "积极应先给方向")

    # ── B：风险容忍度 → 客观判断与情景概率不变 ──
    rc_ = apply_risk_tolerance("conservative", RISK_JUDGEMENT_B, RISK_SCENARIOS_B)
    ra = apply_risk_tolerance("aggressive", RISK_JUDGEMENT_B, RISK_SCENARIOS_B)
    ck(rc_["judgement"] == ra["judgement"] == RISK_JUDGEMENT_B, "B 客观判断不得随风险容忍度变化")
    ck(rc_["scenarios"] == ra["scenarios"] == RISK_SCENARIOS_B, "B 情景概率不得变化")
    ck(rc_["scenarios"]["乐观"]["probability"] == 0.25, "B 上行情景概率不得被调高")
    ck(rc_["presentation"] != ra["presentation"], "B 关注角度应当不同")
    ck(rc_["objective_unchanged"] and ra["probability_unchanged"], "B 两个判定位应为真")
    ck("增加上行情景权重" in RETIRED_RULES, "B 「增加上行情景权重」必须登记为已删除规则")

    # ── C：短答 → 简洁、不附全文、关键依据与限制仍可见 ──
    c = choose_form(depth="fact")
    ck(c["form"] == "short_answer", f"C 应为短答，实际 {c['form']}")
    cs = form_spec("short_answer")
    ck(cs["attach_full_text"] is False, "C 短答不得附全文")
    ck(set(cs["required"]) == {"关键结论", "关键依据", "必要限制"}, "C 短答三要素")
    ck(cs["archive_entry"] is True, "C 短答仍须给归档入口")

    # ── D：完整报告 → 证据/假设/反证/局限齐备；六段只是一种形式 ──
    d = choose_form(depth="deep_research")
    ck(d["form"] == "full_report", f"D 应为完整报告，实际 {d['form']}")
    ds = form_spec("full_report")
    ck(set(ds["required"]) == {"结论", "证据", "假设", "反证", "局限"}, "D 五要素齐备")
    ck(ds["six_section"] is True and ds["six_section_optional"] is True,
       "D 六段应「适用但可选」，不得强制")
    ck("不得" in ds["rule"] and "结构简化" in ds["rule"], "D 须禁止以简化结构为由删关键内容")

    # ── E：复合任务 → 统一组织、共享数据、不重复取数 ──
    e = composite_task("扫描+比较+解释", [
        {"id": "s1", "goal": "扫描右侧升级名单", "metric_family": "right_side_state",
         "data": {"932666.CSI:state": "S3", "600519.SH:close": 1680.0}, "evidence_state": "verified"},
        {"id": "s2", "goal": "比较候选", "metric_family": "financial_compare",
         "data": {"600519.SH:close": 1680.0, "000858.SZ:close": 130.0}, "evidence_state": "verified"},
        {"id": "s3", "goal": "解释成因", "metric_family": "deep_narrative",
         "data": {"932666.CSI:state": "S3"}, "evidence_state": "unverified"},
    ])
    ck(e["n_subtasks"] == 3, "E 应为 3 个子任务")
    ck(e["duplicate_fetches"] == 2, f"E 去重应省 2 次取数，实际 {e['duplicate_fetches']}")
    ck(len(e["fetch_requests"]) == 3, f"E 共享数据应只剩 3 个键，实际 {e['fetch_requests']}")
    ck(not e["conflicts"], "E 一致数据不应报冲突")
    ck(e["unified_conclusion_required"] is True, "E 必须形成统一结论")
    e2 = composite_task("冲突样例", [
        {"id": "s1", "data": {"PE": 15.98}, "evidence_state": "verified"},
        {"id": "s2", "data": {"PE": 18.0}, "evidence_state": "verified"},
    ])
    ck(bool(e2["conflicts"]), "E 同一指标分歧必须报冲突（不得静默择一）")

    # ── F：仅整理用户提供材料 → 不扩展 ──
    f = classify_task("帮我把这份材料整理成表")
    ck(f["passthrough"] is True, "F 应识别为直通任务")
    ck(f["expansion_allowed"] is False, "F 不得扩展")
    ck(f["kind"] == "organize", f"F 应判为整理，实际 {f['kind']}")
    ff = choose_form(depth="deep_research", passthrough=True)
    ck(ff["form"] == "short_answer", f"F 即使深度标为 deep_research 也不得升级，实际 {ff['form']}")
    ff2 = choose_form(depth="fact", passthrough=True, requested="full_report")
    ck(ff2["form"] == "short_answer" and bool(ff2["refused"]),
       "F 明确要求完整报告也应被拒并降级")
    ck(classify_task("分析一下这家公司的竞争格局")["expansion_allowed"] is True,
       "F 正常研究任务不应被误判为直通")

    # ── G：格式转换 → 结论/数字/验证状态一致 ──
    g1 = convert_format(CONVERT_PAYLOAD_G, "markdown")
    g2 = convert_format(CONVERT_PAYLOAD_G, "table")
    g3 = convert_format(CONVERT_PAYLOAD_G, "xlsx")
    ck(all(x["ok"] for x in (g1, g2, g3)), "G 三种格式都应成功")
    ck(g1["conclusions"] == g2["conclusions"] == g3["conclusions"], "G 结论应一致")
    ck(g1["numbers"] == g2["numbers"] == g3["numbers"], "G 数字应一致")
    ck(g1["verification_state"] == g2["verification_state"] ==
       g3["verification_state"] == "部分校验", "G 验证状态应一致")
    ck(g1["text"] != g2["text"], "G 序列化形式应不同")
    ck(g1["invariants_ok"] and g2["invariants_ok"] and g3["invariants_ok"], "G 不变式判定应为真")
    ck(convert_format(CONVERT_PAYLOAD_G, "docx")["ok"] is False, "G 不支持的格式应明确报错")
    ck("原始报告" in label_for_pdf("shenwa_official")["label"], "G 官方 PDF 须标为原始报告")
    ck("综合报告" in label_for_pdf("composite")["label"], "G 综合报告标签不得与原始报告混用")
    ck(label_for_pdf("nope")["ok"] is False, "G 未知 PDF 来源应报错")
    ck(pdf_capability()["composite_pdf_supported"] is False, "G 本包不支持综合版 PDF")
    ck("markdown" in pdf_capability()["deliver"], "G 不支持时应交付支持的格式")

    # ── 权重语义（要求 4）──
    w = weights_disclaimer()
    ck(w["semantics"] == WEIGHT_SEMANTICS, "权重语义应为 reading_attention_only")
    ck("阅读" in w["disclaimer"] and "校准" in w["disclaimer"], "权重声明须含阅读权重与校准")
    ck("任何加权评分" in w["not_affects"], "权重不得产出加权评分")
    ck(weight_effect({"capital_flow": 0.15, "technical": 0.10})["must_check"] == ["D3", "D9"],
       "权重须翻译为勾选动作 D3/D9")
    ck(weight_effect({"capital_flow": 0.15})["produces_number"] is False, "权重不得产出数字")

    # ── 情景假设（要求 3）──
    sv = scenario_variation(RISK_SCENARIOS_B, {"乐观": {"毛利率": "下滑 3pct"}})
    ck(sv["scenarios"]["乐观"]["kind"] == "hypothesis_variation", "假设变化须标注")
    ck(sv["scenarios"]["乐观"]["label"] == "[假设变化]", "假设变化须带标签")
    ck(sv["scenarios"]["中性"]["kind"] == "baseline", "未替换的情景保持基准")
    ck(not sv["fact_changes_claimed"], "不得声称事实变化")
    ck(sv["scenarios"]["乐观"]["baseline_value"] == "上行区间", "须保留基准值")

    # ── 归档策略（要求 7）──
    ap = archive_policy("short_answer", aid="ManualNews@@x@@1", tid=9,
                        created_at="2026-10-02 01:00", source="www.shenwa.top")
    ck(ap["body_attaches_full_text"] is False, "摘要档正文不附全文")
    ck(ap["archive_keeps_full_text"] is True, "摘要档仍须完整归档")
    ck(ap["entry"]["aid"] == "ManualNews@@x@@1" and ap["entry"]["template_tid"] == 9,
       "归档入口须含 aid 与 tid")
    ck(archive_policy("full_report")["body_attaches_full_text"] is True, "完整报告可含主体段")

    # ── 不变式守卫自身 ──
    bad = dict(cons, key_numbers={"收盘价": 1.0})
    ck(bool(guard_invariants(cons, bad)), "数字被改动时守卫必须报违规")
    ck(guard_invariants(cons, dict(cons, order=["x"])) == [], "仅顺序变化不算违规")

    # ── 证据五态与 evidence.py 同源 ──
    try:
        import evidence as ev
        ck(ev.evidence_state(ev.make_evidence("e1", "s", independently_verified=True)) == "verified",
           "证据五态应与 evidence.py 一致")
        ck(set(EVIDENCE_STATES) == {"verified", "unverified", "conflict", "pending", "missing"},
           "证据五态枚举不得改动")
    except Exception as ex:                                      # noqa: BLE001
        ck(False, f"evidence 交叉核对失败：{ex}")

    # ── 执行器自跑（与 cases/09 / verify.py 同一条路径）──
    demo = run_check_spec({"checks": [
        {"label": "demo-form", "type": "form", "input": {"depth": "fact"},
         "assert": [{"path": "form", "op": "eq", "value": "short_answer"}]},
        {"label": "demo-variant", "type": "variant", "input": {"bias": "conservative"},
         "assert": [{"path": "order", "op": "eq", "value": ["counter", "direction", "condition"]},
                    {"path": "evidence_state", "op": "eq", "value": "unverified"}]},
        {"label": "demo-rule", "type": "classify", "input": "仅查询昨日收盘价",
         "assert": [{"path": "passthrough", "op": "eq", "value": True},
                    {"path": "expansion_allowed", "op": "eq", "value": False}]},
    ]})
    ck(demo["pass"] and demo["n_checks"] == 5,
       f"run_check_spec 自跑未通过：{demo['n_fail']} 失败 / {demo['n_checks']} 项")
    ck(run_check_spec({"checks": [{"label": "bad", "type": "no_such"}]})["n_fail"] == 1,
       "未知检查类型应判失败，不得静默跳过")
    ck(run_check_spec({"checks": [{"label": "bad2", "type": "form", "input": {"depth": "fact"},
                                   "assert": [{"path": "form", "op": "eq",
                                               "value": "full_report"}]}]})["n_fail"] == 1,
       "反例（短答期望成完整报告）应被判失败")

    # ── 六段不是所有任务的格式 ──
    for f_ in ("short_answer", "comparison_table", "brief"):
        ck(form_spec(f_)["six_section"] is False, f"{f_} 不得强制六段")
    ck("不按篇幅" in form_spec("short_answer")["rule"], "形态选择须声明「不按篇幅」")
    ck("关键问题回答完整" in form_spec("brief")["coverage_requirement"], "覆盖率要求须在场")

    print(f"selftest: {'全部通过' if not fails else '失败 %d 项' % len(fails)}（{n} 项断言）")
    for x in fails:
        print("  ✗ " + x)
    return 1 if fails else 0


# ═══════════════════════════════════════════════════════════════════
# 15. CLI
# ═══════════════════════════════════════════════════════════════════

def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "selftest":
        return selftest()
    if cmd == "rules":
        print(rules_text())
        return 0
    if cmd == "forms":
        print(json.dumps({k: form_spec(k) for k in FORMS}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "split":
        print(json.dumps(split_preferences({
            "language": "zh", "length": "standard", "visual": "key", "structure": "full_report",
            "focus": "profit_quality", "horizon": "medium", "comparison_target": "同业",
            "risk_limit": "—", "method": "auto", "data_weights": {}}),
            ensure_ascii=False, indent=2))
        return 0
    print(f"未知子命令 {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

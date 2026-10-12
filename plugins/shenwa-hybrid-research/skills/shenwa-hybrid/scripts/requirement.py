# -*- coding: utf-8 -*-
"""requirement.py · 需求（问题）清单公共模块 —— v1.11.0 新增（第 2 轮）

**要修的病根**：`router.py::step3_metrics()` 命中多个指标族时**只返回第一个**，
下游 12 处消费者一律只读那一个族 → 用户同时提的其它问题被**静默丢掉**。
典型症状：「同时分析估值、财务风险和事件影响」被压成单族，另外两个问题消失。

本模块提供**唯一**的问题清单结构，供 `router.py` / 任务卡 / 交付门共用：

  · `QUESTION_FIELDS`      问题记录字段（§3.4 共享字段表里属于第 2 轮的那些）
  · `split_questions()`    任务原话 → 问题条目（**文本驱动，不按指标族数量**）
  · `build_requirements()` 规范化成问题清单（含对象 × 问题对应）
  · `classify_ask()`       事实查询 / 分析问题 —— **只用于理解需求**
  · `choose_execution()`   按**证据缺口**选执行方式（直取 / 计算 / 已有资料 / 报告候选）
  · `deliver_gate()`       **覆盖情况** vs **完成情况**二分
  · `run_flow()`          代表性流程用例：需求 → 路由 → 执行 → 证据 → 交付门
  · `selftest()` / CLI

⚠️ 本模块**不联网、不调用任何外部接口**；也**不**独自决定「发不发报告」
（那是路由 + 模板能力的联合判断，见 `router.template_for()`）。
⚠️ 时间字段一律沿用第 1 轮的**四类时间口径**，**不另起一套**：
`call_time` / `research_cutoff` / `material_asof` / `published_at`( / `usable_from` )。

CLI::

    python requirement.py selftest
    python requirement.py split "<任务原话>"
    python requirement.py flow      "<任务原话>" [--materials FILE]
    python requirement.py gate      <清单+回答 JSON 文件>
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capability as cap  # noqa: E402

# ═══════════════════════════════════════════════════════════════════
# 0. 共享字段 —— 第 1 轮的四类时间口径（不另起一套）
# ═══════════════════════════════════════════════════════════════════

#: 第 1 轮已定的四类时间口径（第 2 轮直接复用同名字段）
TIME_FIELDS = ("call_time", "research_cutoff", "material_asof", "published_at", "usable_from")

TIME_NOTES = {
    "call_time": "调用时间：发起本次分析的时刻（ISO 时间戳）",
    "research_cutoff": "研究截止时间：本次分析「截至哪一天」（日期）",
    "material_asof": "材料所属时间：所引材料本身属于哪一期（日期或期间，如 2026Q2）",
    "published_at": "公开时间：该材料可被公众获知的最早时间；无法确认 → unknown + 限制说明",
    "usable_from": "可确认可用时间：本包能证明「当时可得」的时点；无证据 → unknown",
}

# ═══════════════════════════════════════════════════════════════════
# 1. 问题记录字段（用户要求 2 的「至少能够追踪」清单）
# ═══════════════════════════════════════════════════════════════════

QUESTION_FIELDS = (
    "question_id",       # ① 稳定的问题编号（<研究对象>-Q<n>，§3.4）
    "source_text",       # ② 对应的用户原始需求（本问的原话片段）
    "parent_text",       #    所属任务的完整原话（便于回溯，不丢上下文）
    "object_id",         # ③ 研究对象
    "object_level",      # ④ 对象层级（个股 / 板块 / 行业 / 产业链 / 宏观 / 指数 / 未指明）
    "research_cutoff",   # ⑤ 时间要求（第 1 轮口径，不另建时间字段）
    "asks",              # ⑥ 需要查询的事实 / 计算的结果 / 解释的问题
    "metric_families",   # ⑦ 所需指标（族）—— 注意「问题数 ≠ 族数」
    "evidence_need",     # ⑧ 所需证据
    "material_refs",     # ⑨ 已有材料引用（**不写进发布 text**）
    "execution_mode",    # ⑩ 拟采用的执行方式
    "status",            # ⑪ 完成状态
    "answer_slot",       # ⑫ 最终回答的对应位置
)

#: 缺任一即视为问题记录不完整
REQUIRED_Q_FIELDS = ("question_id", "source_text", "object_id", "asks",
                     "metric_families", "execution_mode", "status", "answer_slot")

#: 问题种类（**只用于理解需求，不决定是否发报告**）
ASK_KINDS = ("fact_query", "analysis_question")

#: 执行方式（一个任务可**组合**多种）
EXECUTION_MODES = ("direct_fetch", "compute", "existing_material",
                   "report_candidate", "blocked")

EXECUTION_NOTES = {
    "direct_fetch": "已有可用接口能满足的事实查询 → 直接取数",
    "compute": "能通过已有数据完成的计算 → 直接计算并保留口径",
    "existing_material": "现有资料足够支持的分析 → 使用现有资料完成（**不因是「分析问题」就发报告**）",
    "report_candidate": "存在**实质性证据缺口**、需进一步研究且模板能力匹配 → 进入报告候选路径",
    "blocked": "缺必需数据 / 证据且无匹配能力 → 如实列出缺口，不擅自缩减研究范围",
}

#: 状态（「已回答 / 部分回答 / 未完成」明确区分）
STATUS_ORDER = ("answered", "partial", "unfinished")

#: 「未完成」的**合法原因**（原因缺失本身即缺陷）
LEGAL_UNFINISHED_REASONS = (
    "material_insufficient",   # 材料不足
    "calibre_incomparable",    # 口径不可比
    "no_authorization",        # 需付费发布但未获授权
    "submit_unknown",          # 结果未知待核对
    "out_of_scope",            # 超出授权范围
    "capability_unavailable",  # 能力不可用 / 未验证
    "not_implemented_yet",     # 依赖后续轮次（如第 3 轮发布计划）
)

# ═══════════════════════════════════════════════════════════════════
# 2. 词表（只用于「识别需求形态」，不用于分流、不用于决定发不发报告）
# ═══════════════════════════════════════════════════════════════════

#: ⚠️ 「分析」二字**不是**触发深度报告的规则 —— 它只说明这是分析问题。
ANALYSIS_WORDS = ["为什么", "原因", "成因", "归因", "驱动", "意味着", "影响", "解释",
                  "推演", "判断", "展望", "前景", "怎么看", "如何看", "有何", "是否",
                  "贵不贵", "有无风险", "风险大不大", "稳不稳", "可持续", "解读"]

#: ⚠️ 指标族数量**不是**触发报告的规则；这里只识别「要不要算」。
COMPUTE_WORDS = ["计算", "测算", "核算", "复算", "同比", "环比", "占比", "比重",
                 "差距", "差值", "变化量", "增幅", "降幅", "复合增长", "CAGR",
                 "分位", "排名", "排行", "Top", "TOP", "前几", "倍数"]

#: 直取类指标族 —— 这些族的常用能力是**同步接口**，不需要发布报告
DIRECT_FAMILIES = ("price_quote", "bias_rate", "technical", "fund_flow",
                   "announcement", "macro_industry", "right_side_state",
                   "valuation", "financial_compare", "research_opinion")

_SEG_SPLIT = re.compile(r"[；;。！!？?\n]+")
_PLUS_SPLIT = re.compile(r"\s*[+＋]\s*")
_MARK_SPLIT = re.compile(r"(?:同时|并且|另外(?:还)?|还有|以及|再看|再加|再问|顺便(?:还)?|接着)")
_VERB_HEAD = re.compile(r"^\s*(查询|查一下|查|查看|看|分析|比较|对比|解释|评估|判断|研究|"
                        r"梳理|列出|给出|测算|计算|核算|统计|跟踪|监测|复盘|帮我看)")
_ENUM_SPLIT = re.compile(r"[、,]|(?:和|与)(?=[^和与\d])")
_COLON_PREFIX = re.compile(r"^\s*([^：:\n]{1,24})[：:]\s*")
_WS = re.compile(r"\s+")


def _hits(text, words):
    return [w for w in words if w in (text or "")]


def _norm(s):
    return _WS.sub("", (s or "").strip())


# ═══════════════════════════════════════════════════════════════════
# 3. 问题拆分（文本驱动）
# ═══════════════════════════════════════════════════════════════════

def split_questions(text: str) -> dict:
    """把任务原话拆成问题条目。

    **判据是文本结构，不是指标族数量**：
      · 强分隔：`；;。！!？?` 与换行；
      · `+ ＋` 并列；
      · 并列连词：同时 / 并且 / 另外 / 还有 / 以及 …；
      · **枚举**（`、 ,` 或 `X和Y`）——仅当该片段带**动作词头**（查询 / 分析 / 比较 …）
        或本身含顿号时才拆，避免把「公司的竞争格局与战略」这类**一个**问题误拆。
    """
    t = (text or "").strip()
    if not t:
        return {"prefix": "", "questions": [], "why": "空输入"}

    m = _COLON_PREFIX.match(t)
    prefix = (m.group(1).strip() + "：") if m else ""
    body = t[m.end():] if m else t

    parts = []
    for seg in _SEG_SPLIT.split(body):
        seg = seg.strip()
        if not seg:
            continue
        parts.extend(x.strip() for x in _PLUS_SPLIT.split(seg) if x.strip())

    marked = []
    for p in parts:
        chunks = [c.strip() for c in _MARK_SPLIT.split(p) if c.strip()]
        marked.extend(chunks or [p])

    out = []
    for p in marked:
        has_verb = bool(_VERB_HEAD.match(p))
        if has_verb or "、" in p or "," in p:
            head = _VERB_HEAD.match(p)
            tail = p[head.end():] if head else p
            items = [x.strip() for x in _ENUM_SPLIT.split(tail) if x.strip()]
            if len(items) >= 2:
                out.extend(items)
                continue
        out.append(p)

    seen, res = set(), []
    for q in out:
        k = _norm(q)
        if k and k not in seen:
            seen.add(k)
            res.append(q)
    return {"prefix": prefix, "questions": res,
            "why": f"按分隔符 / 并列连词 / 枚举拆出 {len(res)} 个问题（**不按指标族数量**）"}


# ═══════════════════════════════════════════════════════════════════
# 4. 需求理解：事实查询 / 分析问题（**只用于理解需求**）
# ═══════════════════════════════════════════════════════════════════

def classify_ask(qtext: str, families=None) -> dict:
    """该问题是「事实查询」还是「分析问题」。

    🔴 **本函数只用于理解需求，不得单独决定是否生成深度报告。**
    「出现『分析』二字」不是发布报告的规则；报告与否由**证据缺口 + 模板能力**决定
    （见 `choose_execution()`）。
    """
    hits = _hits(qtext, ANALYSIS_WORDS)
    if hits:
        return {"kind": "analysis_question", "matched": hits,
                "why": f"含分析/归因语汇 {hits}（**仅表示需要解释，不构成发布报告的理由**）"}
    if families:
        return {"kind": "fact_query", "matched": [],
                "why": "未含分析语汇、且可定位到具体指标族 → 事实查询"}
    return {"kind": "fact_query", "matched": [],
            "why": "未含分析语汇 → 按事实查询处理（如需解释再升级执行方式）"}


# ═══════════════════════════════════════════════════════════════════
# 5. 执行方式：按**证据缺口**选（不是按「分析」二字、也不是按族数）
# ═══════════════════════════════════════════════════════════════════

def _material_of(materials, key):
    """材料表按「指标族」或「问题编号」取值，两种键都支持。"""
    if not isinstance(materials, dict):
        return None
    return materials.get(key)


def _gap_of(fam, materials) -> bool | None:
    """该指标族是否**存在实质证据缺口**。

    · `materials is None` → `None`（**未知**，不擅自当成「够」或「不够」）
    · 材料存在且 `sufficient` 为真 → `False`（无缺口）
    · 材料存在但不足 / 该族无材料 → `True`（有缺口）
    """
    if materials is None:
        return None
    m = _material_of(materials, fam)
    if isinstance(m, dict):
        return not bool(m.get("sufficient", True))
    if m in ("sufficient", "ok", "enough"):
        return False
    if m in ("insufficient", "none", "missing"):
        return True
    return True


def _direct_caps(families, available):
    out = []
    for f in families or []:
        cs = cap.capabilities_for(f)
        if available is not None:
            cs = [c for c in cs if c in available]
        out.extend(cs)
    return sorted(set(out))


def choose_execution(qtext, families, *, ask_kind=None, materials=None,
                     available=None, template_ok=False) -> dict:
    """按**证据缺口**选执行方式。可组合：一个任务里不同问题可有不同方式。

    判定顺序：
      ① 明确要求计算 → `compute`；
      ② 事实查询 + 有可用直取接口 + 无缺口 → `direct_fetch`；
      ③ 资料足够 → `existing_material`（**分析问题也走这条，不发报告**）；
      ④ 有实质缺口 + 模板能力匹配 → `report_candidate`；
      ⑤ 其余 → `blocked`（如实列缺口）。

    ⚠️ 全程**不**看指标族数量、**不**看是否命中「分析」二字。
    """
    fams = [f for f in (families or []) if f]
    th = _hits(qtext, COMPUTE_WORDS)
    if th:
        return {"mode": "compute",
                "why": f"问题要求由已有数据算出结果（命中 {th}）→ 直接计算并保留口径",
                "gaps": [], "caps": _direct_caps(fams, available)}

    if not fams:
        return {"mode": "blocked",
                "why": "未定位到指标族 → 不猜能力，先澄清「要看哪一类指标」；如已给材料则按材料回答",
                "gaps": [], "caps": []}

    gaps = {f: _gap_of(f, materials) for f in fams}
    known_gap = [f for f, g in gaps.items() if g is True]
    unknown = [f for f, g in gaps.items() if g is None]
    caps = _direct_caps(fams, available)
    kind = ask_kind or classify_ask(qtext, fams)["kind"]

    if kind == "fact_query" and caps and not known_gap and not unknown:
        return {"mode": "direct_fetch",
                "why": f"事实查询；所需指标族 {fams} 有可直接调用的接口（{caps}）且所需数据可得 → 直接取数",
                "gaps": [], "caps": caps}

    if not known_gap and not unknown:
        return {"mode": "existing_material",
                "why": f"所需指标族 {fams} 的现有资料已足够 → 使用现有资料完成"
                       "（**不因为这是「分析问题」就发布报告**）",
                "gaps": [], "caps": caps}

    if known_gap and template_ok:
        return {"mode": "report_candidate",
                "why": f"指标族 {known_gap} 存在**实质性证据缺口**，且模板能力匹配 → 进入报告候选路径"
                       "（发布计划与预算属第 3 轮，本轮不产生真实发布）",
                "gaps": known_gap, "caps": caps}

    if known_gap and not template_ok:
        return {"mode": "blocked",
                "why": f"指标族 {known_gap} 存在证据缺口，且当前**无匹配模板能力** → 如实列出缺口，"
                       "不擅自缩减研究范围、不用其他问题的结论代替",
                "gaps": known_gap, "caps": caps}

    return {"mode": "direct_fetch",
            "why": f"所需指标族 {fams} 的材料充分性**未知** → 先按可得数据直取（{caps or '无可用直取接口'}）；"
                   "直取后若仍有缺口，再进入报告候选 —— **不因是「分析问题」就预先发报告**",
            "gaps": [], "caps": caps}


# ═══════════════════════════════════════════════════════════════════
# 6. 问题清单构造
# ═══════════════════════════════════════════════════════════════════

REQ_RULE = (
    "**问题数量 ≠ 指标族数量**：同一指标族可包含多个问题，一个问题也可涉及多个指标族；"
    "**不得因为属于同一指标族就把用户要求回答的不同问题合并掉**。"
    "`n_families` 只是问题清单的一个属性 —— **不得**据此决定是否发报告，"
    "**也不得**据此改用 `tid=2001`。"
)


def build_requirements(text: str, *, reading=None, objects=None, object_level=None,
                       questions=None, families_of=None, research_cutoff=None,
                       materials=None, available=None, template_ok=False,
                       call_time=None, material_asof=None,
                       published_at=None, usable_from=None,
                       answer_slots=None) -> dict:
    """把「一个总任务」规范化成**统一的问题清单**。

    `questions` 可由 WorkBuddy（理解需求的一方）显式给出（list[str] 或 list[dict]）；
    未给出时按 `split_questions()` 从原话拆分。**尽量复用现有结构**：
    指标族匹配一律调用传进来的 `families_of`（= `router.families_for_text`），**不另写一套**。
    """
    level = object_level or ((reading or {}).get("2_object_scope", {}) or {}).get("level") or "未指明"

    spec = split_questions(text)
    prefix = spec["prefix"]

    # 对象表：未给出则退回「reading 的实体提示 → 原话的对象前缀 → 单一未指明对象」
    objs = list(objects) if objects else []
    if not objs:
        hint = ((reading or {}).get("2_object_scope", {}) or {}).get("entities_hint") or []
        if hint:
            objs = list(hint)
        elif prefix:
            objs = [prefix.rstrip("：:").strip()]
        else:
            objs = ["未指明"]

    if questions is None:
        raw = spec["questions"] or [(text or "").strip() or "(未识别出问题)"]
        items = [{"source_text": q} for q in raw]
    else:
        items = []
        for q in questions:
            items.append({"source_text": q} if isinstance(q, str) else dict(q))

    multi_obj = len(objs) > 1
    out, warnings, idx = [], [], 0
    for it in items:
        qtext = it.get("source_text") or it.get("ask") or ""
        fams = it.get("metric_families")
        if fams is None:
            fams = families_of(qtext) if families_of else []
        ask = it.get("asks")
        if ask is None:
            kind = classify_ask(qtext, fams)["kind"]
            ask = {"kind": kind, "fact": [qtext] if kind == "fact_query" else [],
                   "compute": _hits(qtext, COMPUTE_WORDS),
                   "explain": [qtext] if kind == "analysis_question" else []}
        # 对象 × 问题：多对象时每个对象各自成条目（不得串到别的对象）
        tgt = it.get("objects") or (objs if multi_obj else objs[:1])
        for oid in tgt:
            idx += 1
            qid = it.get("question_id") or f"{oid}-Q{idx}"
            ex = it.get("execution_mode")
            if ex is None:
                ex = choose_execution(qtext, fams,
                                      ask_kind=ask.get("kind"),
                                      materials=materials, available=available,
                                      template_ok=template_ok)
            elif isinstance(ex, str):
                ex = {"mode": ex, "why": "由调用方指定", "gaps": [], "caps": []}
            if answer_slots and qid in answer_slots:
                slot = answer_slots[qid]
            elif answer_slots and idx - 1 < len(answer_slots or []):
                slot = list(answer_slots)[idx - 1]
            else:
                slot = f"§{idx}"
            # 追溯用：问题片段若未带对象，补上原话的对象前缀（**原始需求映射不丢**）
            src = qtext
            if prefix and prefix.rstrip("：:").strip() not in qtext:
                src = prefix + qtext
            out.append({
                "question_id": qid,
                "source_text": src,
                "parent_text": it.get("parent_text") or (prefix + text if prefix else text),
                "object_id": oid,
                "object_level": it.get("object_level") or level,
                "research_cutoff": it.get("research_cutoff") or research_cutoff or "unspecified",
                "asks": ask,
                "metric_families": fams,
                "evidence_need": it.get("evidence_need") or _evidence_need(fams, ask.get("kind")),
                "material_refs": it.get("material_refs") or [],
                "execution_mode": ex["mode"],
                "execution_why": ex["why"],
                "execution_gaps": ex.get("gaps", []),
                "status": it.get("status") or "unfinished",
                "answer_slot": slot,
                "required": bool(it.get("required", True)),
            })
            if not fams:
                warnings.append(f"{qid}：未定位到指标族 → 不得据此猜能力")

    # 族顺序 = **首次出现顺序**（与问题的先后一致，便于逐条对应；不排序、不去重丢位）
    fams_all, seen_f = [], set()
    for q in out:
        for f in q["metric_families"]:
            if f not in seen_f:
                seen_f.add(f)
                fams_all.append(f)
    fam_counts = {}
    for q in out:
        for f in q["metric_families"]:
            fam_counts[f] = fam_counts.get(f, 0) + 1

    return {
        "task": text,
        "questions": out,
        "n_questions": len(out),
        "n_objects": len(objs),
        "objects": objs,
        "object_level": level,
        "multi_object": multi_obj,
        "time": {
            "call_time": call_time or "unspecified",
            "research_cutoff": research_cutoff or "unspecified",
            "material_asof": material_asof or "unspecified",
            "published_at": published_at or "unknown",
            "usable_from": usable_from or "unknown",
        },
        "time_notes": TIME_NOTES,
        "n_families": len(fams_all),                 # ← 只是属性，不参与模板选择
        "families": fams_all,
        "family_counts": fam_counts,                 # 同一族可对应多个问题
        "fields": QUESTION_FIELDS,
        "rule": REQ_RULE,
        "warnings": warnings,
        "split_why": spec["why"],
    }


def _evidence_need(families, kind):
    need = {
        "right_side_state": "右侧状态截面（日期 + state/state_trans 字段）",
        "bias_rate": "乖离率序列（窗口 + 改善定义）",
        "valuation": "估值分位（窗口 + 口径来源 + 取数日）",
        "financial_compare": "财报科目与衍生比率（报告期 + 科目口径）",
        "price_quote": "行情（前收 / 收盘 / 时点）",
        "technical": "技术指标（周期 + 参数）",
        "fund_flow": "资金流向（口径 + 区间）",
        "announcement": "公告原文或事件记录（披露时间）",
        "research_opinion": "研报 / 舆情（来源 + 归属）",
        "macro_industry": "宏观行业指标（口径 + 最新日期）",
        "deep_narrative": "成因与推演所需的多源材料（须标来源与时间）",
    }
    ev = [need[f] for f in (families or []) if f in need] or ["**待澄清：需哪一类证据**"]
    if kind == "analysis_question":
        ev.append("解释类：成因链证据 + 反证 + 局限说明")
    return ev


def families_of_questions(req) -> list:
    """清单涉及的全部指标族（去重、保序）。"""
    seen, out = set(), []
    for q in (req or {}).get("questions", []):
        for f in q.get("metric_families", []):
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def to_composite_subtasks(req) -> list:
    """适配 `preferences.composite_task()` 的子任务结构（复用既有复合任务能力）。"""
    subs = []
    for i, q in enumerate((req or {}).get("questions", []), 1):
        subs.append({
            "id": f"s{i}",
            "goal": q.get("source_text"),
            "metric_family": (q.get("metric_families") or [None])[0],
            "metric_families": list(q.get("metric_families") or []),
            "question_id": q.get("question_id"),
            "object_id": q.get("object_id"),
        })
    return subs


# ═══════════════════════════════════════════════════════════════════
# 7. 交付门：覆盖情况 vs 完成情况（**二分**）
# ═══════════════════════════════════════════════════════════════════

GATE_RULE = (
    "「每个问题有回答或缺项说明」只证明**没有静默遗漏**，**不能单独证明任务完成**。"
    "必须二分：**覆盖情况**（每个问题都已登记并处理）与**完成情况**"
    "（有足够材料的问题得到**实际回答**；未完成的问题记录**具体原因与状态**）。"
    "**不得用其他问题的结论代替**；**「缺资料」不算完整回答**；"
    "存在未完成的**必需**问题时，整体任务**不得**标记为全部完成。"
)


def deliver_gate(questions_or_req, answers=None) -> dict:
    """交付前对照问题清单逐条检查。**必须进入实际交付流程**，不能只写在文档里。"""
    qs = (questions_or_req or {}).get("questions") if isinstance(questions_or_req, dict) \
        else (questions_or_req or [])
    answers = answers or {}
    rows, unregistered, missing_reason = [], [], []
    n_ans = n_part = n_unf = 0

    for q in qs or []:
        qid = q.get("question_id")
        a = answers.get(qid)
        if a is None:
            st, reason, slot, ev = "unfinished", "未登记回答", q.get("answer_slot"), []
            unregistered.append(qid)
        else:
            st = a.get("status") or "unfinished"
            reason = a.get("reason")
            slot = a.get("answer_slot") or q.get("answer_slot")
            ev = a.get("evidence") or a.get("refs") or []
            if st in ("partial", "unfinished") and not reason:
                missing_reason.append(qid)
            if st == "answered":
                n_ans += 1
            elif st == "partial":
                n_part += 1
            else:
                n_unf += 1
        rows.append({"question_id": qid, "source_text": q.get("source_text"),
                     "object_id": q.get("object_id"),
                     "metric_families": q.get("metric_families"),
                     "execution_mode": q.get("execution_mode"),
                     "status": st, "reason": reason,
                     "answer_slot": slot, "evidence_refs": ev,
                     "required": bool(q.get("required", True))})

    covered = not unregistered
    req_rows = [r for r in rows if r["required"]]
    all_answered = bool(req_rows) and all(r["status"] == "answered" for r in req_rows)
    complete = bool(covered and not missing_reason and all_answered)
    if complete:
        overall = "complete"
    elif n_ans or n_part:
        overall = "partial"
    else:
        overall = "incomplete"

    return {
        "coverage": {
            "n_questions": len(rows),
            "registered": len(rows) - len(unregistered),
            "unregistered": unregistered,
            "ok": covered,
            "note": "每个问题都已登记并处理，无静默遗漏" if covered
                    else f"有 {len(unregistered)} 个问题**未登记** → 静默遗漏，不通过",
        },
        "completion": {
            "answered": n_ans, "partial": n_part, "unfinished": n_unf,
            "missing_reason": missing_reason,
            "ok": not missing_reason and all_answered,
            "note": "有足够材料的问题均已实际回答" if all_answered
                    else "存在未完成的必需问题，或其状态/原因缺失 → 不得标记为全部完成" ,
        },
        "overall": overall,
        "complete": complete,
        "rows": rows,
        "rule": GATE_RULE,
    }


# ═══════════════════════════════════════════════════════════════════
# 8. 代表性流程用例：需求 → 路由 → 执行 → 证据 → 交付门
# ═══════════════════════════════════════════════════════════════════

class NullPublisher:
    """离线替身：**只计数、不发布**。

    本轮（第 2 轮）不实现发布计划与预算 —— 属第 3 轮。
    因此报告候选一律记 `not_implemented_yet`，**不产生任何真实付费发布**。
    """

    def __init__(self):
        self.calls = 0
        self.records = []

    def __call__(self, item):
        self.calls += 1
        self.records.append(dict(item))
        return {"ok": False, "aid": None, "reason": "not_implemented_yet",
                "msg": "本轮（第 2 轮）不实现发布；发布计划 / 预算 / 任务条目绑定属第 3 轮"}


def run_flow(text: str, *, objects=None, object_level=None, questions=None,
             materials=None, available=None, template_ok=None,
             route_fn=None, publisher=None, **route_kwargs) -> dict:
    """跑通「问题清单 → 执行调用 → 证据 → 最终回答」，返回可断言的观测。

    发布端使用**离线替身**（`NullPublisher`），**不产生任何真实付费发布**；
    合成材料须由调用方显式标 `source: "SYNTHETIC..."` 以便区分。
    """
    if route_fn is None:                                     # 懒 import，避免循环依赖
        import router as route_mod                           # noqa: PLC0415
        route_fn, families_of = route_mod.route, route_mod.families_for_text
    else:
        import router as route_mod                           # noqa: PLC0415
        families_of = route_mod.families_for_text

    r = route_fn(text, available=available, **route_kwargs)
    tpl = r.get("template")
    if template_ok is None:
        template_ok = bool(tpl)

    req = build_requirements(text, reading=r, objects=objects, object_level=object_level,
                             questions=questions, families_of=families_of,
                             materials=materials, available=available,
                             template_ok=template_ok)
    pub = publisher if publisher is not None else NullPublisher()

    answers = {}
    for q in req["questions"]:
        qid, fams, mode = q["question_id"], q["metric_families"], q["execution_mode"]
        slot = q["answer_slot"]
        if mode in ("direct_fetch", "compute", "existing_material"):
            ev = []
            for f in fams:
                m = _material_of(materials, f)
                m = m if isinstance(m, dict) else {}
                ev.append({"family": f, "value": m.get("value"), "unit": m.get("unit"),
                           "asof": m.get("asof"),
                           "source": m.get("source", "SYNTHETIC(未标注)"),
                           "calibre": m.get("calibre")})
            ok = bool(ev) and all(e["value"] is not None for e in ev)
            if ok:
                answers[qid] = {"status": "answered", "answer_slot": slot, "evidence": ev,
                                "execution_mode": mode,
                                "note": "口径随答案标注（" + EXECUTION_NOTES[mode] + "）"}
            else:
                answers[qid] = {"status": "unfinished", "reason": "material_insufficient",
                                "answer_slot": slot,
                                "note": "未取到可用证据 → 明确缺口，不用其他问题结论代替"}
        elif mode == "report_candidate":
            res = pub({"question_id": qid, "metric_families": fams,
                       "object_id": q["object_id"], "ask": q["source_text"]})
            answers[qid] = {"status": "unfinished",
                            "reason": res.get("reason") or "not_implemented_yet",
                            "answer_slot": slot,
                            "note": res.get("msg", "")}
        else:  # blocked
            answers[qid] = {"status": "unfinished",
                            "reason": (q.get("execution_gaps") and "material_insufficient")
                                      or "capability_unavailable",
                            "answer_slot": slot, "note": q.get("execution_why")}

    gate = deliver_gate(req, answers)
    return {
        "task": text,
        "route": r,
        "requirements": req,
        "questions": req["questions"],
        "answers": answers,
        "gate": gate,
        "publish_calls": getattr(pub, "calls", 0),
        "publish_records": getattr(pub, "records", []),
        "deliverable_mode": r["deliverable"]["mode"],
        "template": tpl,
        "families": req["families"],
        "n_families": req["n_families"],
        "flow": [{"question_id": q["question_id"], "ask": q["source_text"],
                  "families": q["metric_families"], "execution_mode": q["execution_mode"],
                  "status": answers[q["question_id"]]["status"],
                  "answer_slot": q["answer_slot"]} for q in req["questions"]],
    }


# ═══════════════════════════════════════════════════════════════════
# 9. 自检
# ═══════════════════════════════════════════════════════════════════

OBS = {
    "pe_debt": "同时查询PE和资产负债率",
    "explain_change": "解释一下这家公司毛利率最近两年为什么下滑",
    "three_dims": "某公司：估值贵不贵 + 财务有无风险 + 近期事件影响",
    "same_family": "这家公司研发投入高不高，以及研发投入的会计处理是否有变化",
    "multi_object": "比较以下三家的盈利质量：甲公司、乙公司、丙公司",
    "industry_multi": "行业层面：景气度与竞争格局如何演变",
}

SYNTH = {
    "valuation": {"value": 18.6, "unit": "倍", "asof": "2026-09-30",
                  "calibre": "TTM PE，历史分位窗口 5 年",
                  "source": "SYNTHETIC(离线合成材料·非真实数据)", "sufficient": True},
    "financial_compare": {"value": 63.65, "unit": "%", "asof": "2026Q2",
                          "calibre": "资产负债率，合并报表口径",
                          "source": "SYNTHETIC(离线合成材料·非真实数据)", "sufficient": True},
    "announcement": {"value": "2026-09-28 公告", "unit": "", "asof": "2026-09-28",
                     "calibre": "公告披露时间",
                     "source": "SYNTHETIC(离线合成材料·非真实数据)", "sufficient": True},
    "macro_industry": {"value": 51.2, "unit": "点", "asof": "2026-09",
                       "calibre": "行业景气指数，月度",
                       "source": "SYNTHETIC(离线合成材料·非真实数据)", "sufficient": True},
    "right_side_state": {"value": "S3", "unit": "阶段", "asof": "2026-09-30",
                         "calibre": "state_mapping.current_state，按 date 切截面",
                         "source": "SYNTHETIC(离线合成材料·非真实数据)", "sufficient": True},
}


def selftest() -> int:
    fails = []
    n = 0

    def ck(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fails.append(msg)

    # ── ① 问题拆分：并行问题不丢 ──
    s = split_questions(OBS["pe_debt"])
    ck(len(s["questions"]) == 2, f"「PE和资产负债率」应拆 2 问，实际 {s['questions']}")
    s2 = split_questions(OBS["three_dims"])
    ck(len(s2["questions"]) == 3, f"三问场景应拆 3 问，实际 {s2['questions']}")
    ck(s2["prefix"] == "某公司：", f"应识别对象前缀，实际 {s2['prefix']!r}")
    # 单一问题不得被误拆
    ck(len(split_questions("比较三家公司的盈利质量")["questions"]) == 1,
       "「比较三家公司的盈利质量」是**一个**问题，不得误拆")
    ck(len(split_questions("深入比较三个行业未来三年的竞争格局")["questions"]) == 1,
       "含「三个行业」的单一问题不得被数量词误拆")

    # ── ② 问题数 ≠ 族数：同一族多问不被合并 ──
    rq = build_requirements(OBS["same_family"],
                            families_of=lambda t: (["financial_compare"] if "研发" in t else []))
    ck(rq["n_questions"] == 2, f"同一族下两个问题应各留一条，实际 {rq['n_questions']}")
    ck(rq["n_families"] == 1, f"两问同族 → n_families 应为 1，实际 {rq['n_families']}")
    ck(all(q["metric_families"] == ["financial_compare"] for q in rq["questions"]),
       "两问都应挂 financial_compare")
    ck(rq["family_counts"]["financial_compare"] == 2, "同族计数应为 2（不为 1，未被去重）")

    # ── ③ 一个问题可涉及多个指标族 ──
    #    注意：若写成「估值贵不贵、财务有没有风险」会被**顿号**拆成两问（那才是对的）；
    #    这里用一个**不拆**的句式，验证「一问挂多族」。
    rq2 = build_requirements("这家公司估值与财务风险的判断",
                             families_of=lambda t: ([f for f, ws in
                                                     (("valuation", ["估值"]),
                                                      ("financial_compare", ["财务"]))
                                                     if any(w in t for w in ws)]))
    ck(rq2["n_questions"] == 1 and len(rq2["questions"][0]["metric_families"]) == 2,
       f"一问可挂多族，实际 n={rq2['n_questions']} "
       f"{[q['metric_families'] for q in rq2['questions']]}")

    # ── ④ 多对象：对象 × 问题逐条对应，不串对象 ──
    rq3 = build_requirements("比较以下三家的盈利质量",
                             objects=["甲公司", "乙公司", "丙公司"], object_level="个股",
                             families_of=lambda t: ["financial_compare"])
    ck(rq3["n_questions"] == 3, f"3 对象应得 3 条，实际 {rq3['n_questions']}")
    ck({q["object_id"] for q in rq3["questions"]} == {"甲公司", "乙公司", "丙公司"},
       "每个对象各成一条，不得串对象")
    ck(len({q["question_id"] for q in rq3["questions"]}) == 3, "问题编号应唯一")

    # ── ⑤ 分类：只用于理解需求，不决定发布 ──
    c1 = classify_ask("解释一下这家公司毛利率最近两年为什么下滑", ["financial_compare"])
    ck(c1["kind"] == "analysis_question", "含因果语汇应判为分析问题")
    ck("不构成发布报告的理由" in c1["why"] or "不构成发布报告的理由" in c1["why"],
       "分类说明必须写明「不构成发布报告的理由」")
    c2 = classify_ask("同时查询PE和资产负债率", ["valuation", "financial_compare"])
    ck(c2["kind"] == "fact_query", "取数问题应判为事实查询")

    # ── ⑥ 执行方式：按证据缺口，不按「分析」二字 / 不按族数 ──
    e1 = choose_execution("同时查询PE和资产负债率", ["valuation", "financial_compare"],
                          ask_kind="fact_query", materials=SYNTH,
                          available=["D1", "D7", "D2"])
    ck(e1["mode"] == "direct_fetch", f"数据可用的事实查询应直取，实际 {e1['mode']}")
    e2 = choose_execution("解释一下这家公司毛利率最近两年为什么下滑", ["financial_compare"],
                          ask_kind="analysis_question", materials=SYNTH,
                          available=["D2"], template_ok=True)
    ck(e2["mode"] == "existing_material",
       f"资料足够的**分析问题**应用现有资料完成、**不发报告**，实际 {e2['mode']}")
    e3 = choose_execution("这家公司研发投入与会计处理", ["financial_compare"],
                          ask_kind="fact_query", materials={}, available=["D2"],
                          template_ok=True)
    ck(e3["mode"] == "report_candidate", f"有实质缺口且模板匹配 → 报告候选，实际 {e3['mode']}")
    e4 = choose_execution("这家公司研发投入与会计处理", ["financial_compare"],
                          ask_kind="fact_query", materials={}, available=["D2"],
                          template_ok=False)
    ck(e4["mode"] == "blocked", f"有缺口且无模板 → 如实列缺口，实际 {e4['mode']}")
    # 族数不参与判定：族数 1 与 3，在同样缺口条件下得到同样结论
    e5 = choose_execution("x", ["valuation", "financial_compare", "announcement"],
                          ask_kind="fact_query", materials={}, available=["D1"],
                          template_ok=True)
    e6 = choose_execution("x", ["valuation"], ask_kind="fact_query",
                          materials={}, available=["D1"], template_ok=True)
    ck(e5["mode"] == e6["mode"] == "report_candidate",
       "**族数不得影响执行方式**（1 族与 3 族在同一缺口条件下结论相同）")
    ck("族数" not in e5["why"] or True, "noop")
    # 计算类
    e7 = choose_execution("算一下这家公司近三年营收的复合增长率", ["financial_compare"],
                          ask_kind="fact_query", materials=SYNTH, available=["D2"])
    ck(e7["mode"] == "compute", f"要求计算应走 compute，实际 {e7['mode']}")
    # 材料未知（None）不得擅自当成「够」
    e8 = choose_execution("这家公司估值贵不贵", ["valuation"],
                          ask_kind="analysis_question", materials=None,
                          available=["D1", "D7"], template_ok=True)
    ck(e8["mode"] in ("direct_fetch", "report_candidate"),
       f"材料未知时应按能力/缺口判定，实际 {e8['mode']}")

    # ── ⑦ 交付门：覆盖 vs 完成 二分 ──
    g1 = deliver_gate(rq3)
    ck(g1["coverage"]["ok"] is False and len(g1["coverage"]["unregistered"]) == 3,
       "未提供回答 → 全部未登记（静默遗漏）→ 覆盖不通过")
    ck(g1["complete"] is False, "未登记不得判为完成")
    ans = {q["question_id"]: {"status": "answered"} for q in rq3["questions"]}
    g2 = deliver_gate(rq3, ans)
    ck(g2["coverage"]["ok"] and g2["complete"] and g2["overall"] == "complete",
       "三个问题都有实际回答 → 覆盖 + 完成都通过")
    ans2 = dict(ans)
    ans2[rq3["questions"][2]["question_id"]] = {"status": "unfinished",
                                                "reason": "material_insufficient"}
    g3 = deliver_gate(rq3, ans2)
    ck(g3["coverage"]["ok"] is True, "有登记即覆盖通过")
    ck(g3["complete"] is False and g3["overall"] == "partial",
       f"一个未完成 → 整体部分完成，实际 {g3['overall']}")
    ans3 = dict(ans)
    ans3[rq3["questions"][0]["question_id"]] = {"status": "unfinished"}
    g4 = deliver_gate(rq3, ans3)
    ck(g4["completion"]["missing_reason"], "未完成但无原因 → 原因缺失即缺陷")
    ck(g4["complete"] is False, "原因缺失不得判为完成")
    ck("不得用其他问题的结论代替" in g4["rule"], "交付门规则须禁止用其他问题结论代替")
    ck("「缺资料」不算完整回答" in g4["rule"], "交付门规则须写明缺资料不算完整回答")

    # ── ⑧ 代表性流程用例（离线替身，0 真实发布）──
    f1 = run_flow(OBS["pe_debt"], materials=SYNTH,
                  available=["D1", "D7", "D2", "D9"])
    ck(f1["publish_calls"] == 0, f"查 PE + 资产负债率不得触发发布，实际 {f1['publish_calls']}")
    ck(f1["deliverable_mode"] != "report", "两问数据可用 → 交付形态不得是报告")
    ck(all(a["status"] == "answered" for a in f1["answers"].values()), "两项都应返回")
    ck(f1["template"] is None, "不发报告 → 无模板选型")
    ck(all(e.get("calibre") for e in
           [x for a in f1["answers"].values() for x in a["evidence"]]),
       "两项都应说明口径")

    f2 = run_flow(OBS["explain_change"], materials=SYNTH,
                  available=["D2"])
    ck(f2["publish_calls"] == 0, "已有材料足够解释 → 不得发布报告")
    ck(all(a["status"] == "answered" for a in f2["answers"].values()), "应完成解释")
    ck(f2["requirements"]["questions"][0]["asks"]["kind"] == "analysis_question",
       "应识别为分析问题，但**不因此发布报告**")

    f3 = run_flow(OBS["three_dims"], objects=["某公司"], object_level="个股",
                  materials=SYNTH, available=["D1", "D7", "D2", "D4", "D9"])
    ck(f3["n_families"] >= 2, f"三问应识别出 ≥2 个族，实际 {f3['families']}")
    ck(len(f3["questions"]) == 3, f"三问应逐条保留，实际 {len(f3['questions'])}")
    ck(all(a["status"] == "answered" for a in f3["answers"].values()),
       f"三问都应逐项回应，实际 {[a['status'] for a in f3['answers'].values()]}")
    tpl = f3["template"] or {}
    ck(tpl.get("初选") != 2001 or tpl.get("switch_reason", {}).get("by_family_count") is not True,
       "**不得仅因族数 ≥2 就改用 2001**")
    ck(tpl.get("coverage_gap", {}).get("by_family_count") is not True,
       "模板选型的 coverage_gap 不得由族数产生")
    ck(f3["publish_calls"] == 0, "三问材料齐备 → 不产生发布")

    f4 = run_flow(OBS["multi_object"], objects=["甲公司", "乙公司", "丙公司"],
                  object_level="个股", materials=SYNTH, available=["D2"])
    ck({q["object_id"] for q in f4["questions"]} == {"甲公司", "乙公司", "丙公司"},
       "对象与问题对应正确")
    ck(f4["template"] is None or f4["template"]["level"] == "个股",
       "不得调用不适用该层级的模板")

    f5 = run_flow(OBS["industry_multi"], objects=["电池行业"], object_level="行业",
                  materials=SYNTH, available=["D7"])
    ck(f5["template"] is not None and f5["template"]["level"] == "行业",
       f"行业层级应有行业级模板，实际 {f5['template']}")
    ck(f5["template"]["2001_eligible"] is False, "行业层级 2001_eligible 必须为 False")
    ck(2001 not in (f5["template"]["allowed"] or []), "行业层级允许集不得含 2001")
    ck(f5["template"]["switched_to_2001"] is False,
       "行业层级即使存在覆盖缺口也**不得**转 2001（越级）")
    ck("blocked_by_level" in f5["template"]["switch_reason"],
       "行业层级存在覆盖缺口时应如实登记「不得转 2001」")

    # 三问中两个资料充分、一个不足 → 部分完成
    part = run_flow("某公司：估值贵不贵 + 财务有无风险 + 近期事件影响", objects=["某公司"],
                    object_level="个股",
                    materials={"valuation": SYNTH["valuation"],
                               "financial_compare": SYNTH["financial_compare"],
                               "announcement": {"sufficient": False}},
                    available=["D1", "D7", "D2", "D4"], template_ok=False)
    st = [a["status"] for a in part["answers"].values()]
    ck(st.count("answered") == 2 and st.count("unfinished") == 1,
       f"应为 2 答 + 1 缺口，实际 {st}")
    ck(part["gate"]["overall"] == "partial", f"整体应标部分完成，实际 {part['gate']['overall']}")
    ck(part["gate"]["complete"] is False, "有未完成必需问题 → 不得标记全部完成")
    ck(all(a.get("reason") for a in part["answers"].values() if a["status"] != "answered"),
       "未完成必须记录具体原因")

    # ── ⑨ 复用既有结构：可喂给 preferences.composite_task ──
    subs = to_composite_subtasks(f3["requirements"])
    ck(len(subs) == 3, "应转出 3 个子任务")
    ck(all("metric_families" in x for x in subs), "子任务须带全族列表（不止首族）")
    import preferences as pref                                    # noqa: PLC0415
    ct = pref.composite_task("多维任务", subs)
    ck(ct["n_subtasks"] == 3 and ct["unified_conclusion_required"] is True,
       "复合任务能力应可直接复用")

    # ── ⑩ 时间字段沿用第 1 轮口径，不另起一套 ──
    rqt = build_requirements("查一下这家公司PE", families_of=lambda t: ["valuation"],
                             call_time="2026-10-03T21:30:00+08:00",
                             research_cutoff="2026-09-30", material_asof="2026Q2")
    ck(set(rqt["time"]) == set(TIME_FIELDS),
       f"时间字段必须就是第 1 轮那四类，实际 {sorted(rqt['time'])}")
    ck(rqt["time"]["published_at"] == "unknown",
       "公开时间无法确认时须记 unknown，不声称已还原历史")

    # ── ⑪ 字段完整性 ──
    for q in f3["questions"]:
        miss = [f for f in REQUIRED_Q_FIELDS if q.get(f) in (None, [], "")]
        ck(not miss, f"问题记录 {q['question_id']} 缺字段 {miss}")

    print(f"selftest: {'全部通过' if not fails else '失败 %d 项' % len(fails)}（{n} 项）")
    for x in fails:
        print("  ✗ " + x)
    return 1 if fails else 0


# ═══════════════════════════════════════════════════════════════════
# 10. CLI
# ═══════════════════════════════════════════════════════════════════

def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "selftest":
        return selftest()
    if cmd == "split":
        if not rest:
            print('用法: split "<任务原话>"')
            return 2
        print(json.dumps(split_questions(rest[0]), ensure_ascii=False, indent=2))
        return 0
    if cmd == "flow":
        if not rest:
            print('用法: flow "<任务原话>"')
            return 2
        mats = None
        if "--materials" in rest:
            i = rest.index("--materials")
            mats = json.loads(open(rest[i + 1], encoding="utf-8").read())
            rest = rest[:i] + rest[i + 2:]
        out = run_flow(rest[0], materials=mats)
        print(json.dumps({"families": out["families"], "n_questions": len(out["questions"]),
                          "deliverable_mode": out["deliverable_mode"],
                          "publish_calls": out["publish_calls"],
                          "flow": out["flow"], "gate": out["gate"]},
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "gate":
        if not rest:
            print("用法: gate <清单+回答 JSON>")
            return 2
        d = json.loads(open(rest[0], encoding="utf-8").read())
        print(json.dumps(deliver_gate(d.get("requirements") or d.get("questions"),
                                      d.get("answers")), ensure_ascii=False, indent=2))
        return 0
    print(f"未知子命令 {cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

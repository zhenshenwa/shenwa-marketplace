#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据采信 · 错误原文处理 · 资料不足交付 —— 本包**唯一**的证据分级实现。

解决的问题
----------
1. **规则冲突**：「深挖原文必须全文保留」与「已确认错误必须剔除」在字面上互斥。
   本模块把它拆成**两层**：**原始资料层**（完整归档，一字不删）与**研究层**（正文只呈现本轮认可的内容）。
   两者作用对象不同，因此不构成冲突。
2. **有来源 ≠ 已独立核实**：接口/报告给了数字，只说明「取到了」，不说明「核实过」。
3. **结论确定程度必须与证据状态一致**：关键证据失效时，结论同步**撤回 / 改写 / 降级**。
4. **资料不足要收缩交付范围**，而不是用有限样本冒充实证。

设计约束（来自维护共同约定，改本文件时必须保持）
--------------------------------------------------
1. **归档完整性 > 正文可用性**：确认错误的原文**照常完整归档**（保留报告编号 / 生成时间 / 来源），
   只是**不得再作为正文判断的依据**。
2. **不得用假设或估计冒充已披露事实**；用户提出的观点先登记为**待检验命题**。
3. **比较之前先确认对象 / 时间窗口 / 基准 / 指标一致**。
4. **不把有限检索样本写成市场或卖方整体共识**；统计必须可核对（样本 / 分母 / 去重 / 时间口径 / 归属）。
5. **「已检查覆盖率」≠「已独立验证覆盖率」**，两者分别报告。
6. 本模块只做**本地证据分级与一致性检查**，**不改写深挖的阶段 / 状态 / 算法**。

五态证据（贯穿全部检查）
------------------------
| 状态 | 含义 |
|---|---|
| `verified` | 已取得、口径明确，且**已独立核实**（另一来源/数据源确认） |
| `unverified` | 已取得、口径明确，但**未独立核实** —— 「有来源」仅止于此 |
| `conflict` | 已取得，但**存在冲突**（保留原值与来源，不擅自选一个当真值） |
| `pending` | **待验证**（尚未取得结论性核查） |
| `missing` | **缺失**（未取得） |

另有一类**窗口归属**（`window_ref`）：资料早于本任务时间窗口 → `role="history"`，
**不计入窗口内新增证据**，只作历史背景。

用法
----
    python3 evidence.py selftest
    python3 evidence.py state --text "站上均线比例88.68%" --source "深挖报告"          # 证据分级
    python3 evidence.py archive <report.json>                                        # 归档 vs 正文
    python3 evidence.py support-level --bias <series.json> [--price-low <series.json>]
    python3 evidence.py rel-abs --relative outperform --absolute decline
    python3 evidence.py delivery <request.json>                                      # 交付范围收缩
    python3 evidence.py coverage --total 20 --checked 20 --verified 6
    python3 evidence.py checks <清单.json>                                            # 数据驱动检查清单
    python3 evidence.py independence --graph <关系图.json> --a A --b B                # 独立性三态
    python3 evidence.py echo --returned <证据.json> --sent <背景.json>                # 背景复述检测

第 4 轮新增（**来源关系是补充信息，不替代五态证据校验**）
--------------------------------------------------------
| 能力 | 实现 |
|---|---|
| 背景材料 / 来源统一登记（9 类追踪项，未知显式标记） | `MATERIAL_FIELDS` / `make_material()` |
| 材料性质分类（原始事实 / 计算 / 外部判断 / 用户观点） | `MATERIAL_KINDS` / `material_statement_kind()` |
| 来源关系（引用 / 转述 / 底层同源 / 派生 / 独立 / 未知） | `SOURCE_RELATIONS` / `SourceGraph` |
| 独立性三态（**未登记即 unknown，不默认独立**） | `SourceGraph.independence()` |
| 按具体证据判定（**不整份报告一刀切**） | `SourceGraph.per_evidence_independence()` |
| 深挖复述背景数字 ≠ 新增独立证据 | `echo_check()` / `ECHO_RULE` |
| 「两路独立验证一致」准入 | `can_claim_two_way_independent()` |
| 交付自检 | `check_independence_claims()` + `check_delivery_consistency()` 第 ⑧ 项 |
"""
from __future__ import annotations

import argparse
import json
import sys

# ══════════════════════════════════════════════════════════════════
# 0 · 规则优先关系（「全文保留」与「错误剔除」的冲突解法）
# ══════════════════════════════════════════════════════════════════

#: 「归档」与「正文」的适用范围与优先关系 —— 这是本轮对两条冲突规则的判定
LAYER_RULE = {
    "archive": {
        "name": "原始资料层（归档件）",
        "rule": "深挖报告原文一律**完整归档**：不摘要、不改写、不省略章节，**含已确认错误的段落**",
        "must_keep": ["正文全文", "报告编号(aid)", "生成时间", "来源(模板 tid / 站点)"],
    },
    "body": {
        "name": "研究层（正文）",
        "rule": "正文只呈现本轮**认可的事实、推断、假设与局限**；"
                "**已确认错误的原文不得继续支持正文结论**",
    },
    "conflict_resolution": (
        "两条规则**作用对象不同**，不构成冲突：即「归档保留全文 + 正文禁用」。"
        "字面冲突时一律按此判定 —— **先看它落在哪一层**，而不是选一条规则执行。"
    ),
    "error_contrast": (
        "确认错误的原文如需在正文中展示，必须标 `role=error_contrast`（错误对照），"
        "且**不得**与正文结论共用同一证据链、**不得**出现在结论摘要中。"
    ),
}

#: 全局规则优先顺序（前者覆盖后者）
PRECEDENCE = [
    ("接入合规", "只走 API / 不索要邀请码 / 自动切站 —— 最高优先级，不受其他规则影响"),
    ("归档完整性", "原始材料完整归档（含确认错误段落），保留编号 / 时间 / 来源"),
    ("正文采信", "正文只使用「已取得且口径明确」的内容；确认错误者不得作为正文依据"),
    ("证据状态标注", "逐条标注 verified / unverified / conflict / pending / missing"),
    ("结论确定程度", "结论的确定程度**不得超过**其关键证据的状态"),
    ("交付范围", "范围按实际取得的样本描述；样本不完整时**收缩**范围"),
]


def precedence_note() -> dict:
    return {"layers": {k: v for k, v in LAYER_RULE.items() if k in ("archive", "body")},
            "conflict_resolution": LAYER_RULE["conflict_resolution"],
            "precedence": [p[0] for p in PRECEDENCE]}


# ══════════════════════════════════════════════════════════════════
# 1 · 证据状态（五态 + 窗口归属）
# ══════════════════════════════════════════════════════════════════

EVIDENCE_STATES = {
    "verified": "已取得且口径明确，并已**独立核实**",
    "unverified": "已取得且口径明确，但**未独立核实**（「有来源」仅止于此）",
    "conflict": "已取得，但**存在冲突**（保留原值与来源，未擅自选一个当准）",
    "pending": "**待验证**（尚未取得结论性核查）",
    "missing": "**缺失**（未取得）",
}

#: 可直接作为「已确认事实」写入结论摘要的状态集合 —— 只有 verified
FACT_GRADE_STATES = ("verified",)


def make_evidence(eid: str, statement: str, *, source=None, independently_verified=False,
                  conflicting=False, pending=False, missing=False, observed_at=None,
                  value=None, unit=None, period=None, note=None, critical=False) -> dict:
    """登记一条证据。

    ⚠️ `source` 非空**不等于**已核实 —— 必须由 `independently_verified=True` 显式声明。
    """
    return {
        "id": eid, "statement": statement, "source": source, "value": value, "unit": unit,
        "period": period, "observed_at": observed_at,
        "independently_verified": bool(independently_verified),
        "conflicting": bool(conflicting), "pending": bool(pending), "missing": bool(missing),
        "critical": bool(critical), "note": note,
    }


def evidence_state(ev) -> str:
    """判证据状态。优先级：missing > conflict > pending > verified > unverified。"""
    if not isinstance(ev, dict):
        return "missing"
    if ev.get("missing"):
        return "missing"
    if ev.get("conflicting"):
        return "conflict"
    if ev.get("pending"):
        return "pending"
    if ev.get("independently_verified"):
        return "verified"
    return "unverified"


def is_fact_grade(ev) -> bool:
    """能否当作「已确认事实」用于摘要与结论。"""
    return evidence_state(ev) in FACT_GRADE_STATES


def evidence_table(items, window=None) -> dict:
    """批量分级，并给出**按状态的计数**。

    传入 `window` 时，同时标注每条证据的**窗口归属**（`window_role`）：
    早于窗口起点 → `history`，**不计入窗口内新增证据**。
    """
    rows = []
    for e in items:
        r = {"id": e.get("id"), "statement": e.get("statement"), "state": evidence_state(e),
             "source": e.get("source"), "critical": bool(e.get("critical"))}
        if window is not None:
            w = window_ref(e, window)
            r["window_role"] = w["role"]
            r["counts_as_new_evidence"] = w["counts_as_new_evidence"]
        rows.append(r)
    counts = {s: 0 for s in EVIDENCE_STATES}
    for r in rows:
        counts[r["state"]] += 1
    res = {"rows": rows, "counts": counts, "n": len(rows)}
    if window is not None:
        res["window"] = list(window)
        res["history_ids"] = [r["id"] for r in rows if r.get("window_role") == "history"]
    return res


# ══════════════════════════════════════════════════════════════════
# 2 · 窗口归属、陈述分类
# ══════════════════════════════════════════════════════════════════

def window_ref(ev, window) -> dict:
    """判断一条证据相对本任务时间窗口的位置。

    `window = (start, end)`，ISO 日期字符串。早于 `start` → `role="history"`，
    **不计入窗口内新增证据**（只作历史背景）。
    """
    start, end = (window or (None, None))
    at = (ev or {}).get("observed_at")
    res = {"id": (ev or {}).get("id"), "observed_at": at, "window": [start, end],
           "role": "in_window", "counts_as_new_evidence": True, "note": ""}
    if at is None:
        res["role"] = "unknown_time"
        res["counts_as_new_evidence"] = False
        res["note"] = "该证据无时间标注 → 不得计入窗口内新增证据，须先补时间口径"
        return res
    if start and at < start:
        res["role"] = "history"
        res["counts_as_new_evidence"] = False
        res["note"] = f"资料日期 {at} 早于窗口起点 {start} → 只作**历史背景**，不计入窗口内新增证据"
        return res
    if end and at > end:
        res["role"] = "after_window"
        res["counts_as_new_evidence"] = False
        res["note"] = f"资料日期 {at} 晚于窗口终点 {end} → 不得计入本窗口"
        return res
    return res


STATEMENT_KINDS = {
    "disclosed_fact": "已披露事实（有可核对的公开来源）",
    "scenario_assumption": "情景假设（**须显式标注**，不得冒充已披露事实）",
    "user_hypothesis": "用户提出的观点 → 先作为**待检验命题**，不自动写成背景事实",
    "estimate": "估计 / 推算值（须标注方法与区间，不得当作披露数）",
    "inference": "基于证据的推断（须列出所依据的证据 id）",
}


def classify_statement(text, *, kind=None, evidence_ids=None) -> dict:
    """给一条陈述归类，并给出**写入正文的准入判定**。

    规则：`disclosed_fact` 必须有可核对来源；`scenario_assumption` / `estimate` 必须显式标注；
    `user_hypothesis` 只能是命题，**不得**出现在「事实底座」段。
    """
    k = kind or "disclosed_fact"
    res = {"text": text, "kind": k, "label": STATEMENT_KINDS.get(k, "未知"),
           "usable_as_fact": False, "must_label": False, "allowed_sections": [], "note": ""}
    if k == "disclosed_fact":
        res["usable_as_fact"] = True
        res["allowed_sections"] = ["结论摘要", "事实底座", "分析主体", "校验表", "风险与情景"]
        res["note"] = "仍须标注来源与截取时点；**「有来源」不等于「已独立核实」**"
    elif k == "scenario_assumption":
        res["must_label"] = True
        res["allowed_sections"] = ["风险与情景"]
        res["note"] = "必须显式写「情景假设」，**不得**出现在事实底座或结论摘要的事实句中"
    elif k == "estimate":
        res["must_label"] = True
        res["allowed_sections"] = ["分析主体", "风险与情景"]
        res["note"] = "必须标注估算方法与区间；**不得**当作披露事实"
    elif k == "user_hypothesis":
        res["allowed_sections"] = ["待检验命题"];
        res["note"] = "用户观点先作为**待检验命题**登记，**不得**自动写成背景事实"
    elif k == "inference":
        res["allowed_sections"] = ["分析主体", "风险与情景"]
        res["note"] = "推断必须列出所依据的证据 id，且**不得**升格为披露事实"
    if evidence_ids is not None:
        res["evidence_ids"] = list(evidence_ids)
    return res


def check_statement_misuse(statement: dict, target_section: str) -> dict:
    """检查一条陈述是否被放到了不该放的位置（req 4 的守门）。"""
    kind = statement.get("kind")
    allowed = STATEMENT_KINDS.get(kind) and classify_statement(statement.get("text", ""), kind=kind)["allowed_sections"]
    bad = target_section not in (allowed or [])
    return {"text": statement.get("text"), "kind": kind, "section": target_section,
            "misuse": bad,
            "note": (f"⚠️「{STATEMENT_KINDS.get(kind, kind)}」不得出现在「{target_section}」"
                     if bad else "位置合规")}


# ══════════════════════════════════════════════════════════════════
# 3 · 摘要准入与「转述 vs 断言」
# ══════════════════════════════════════════════════════════════════

USE_AS = ("confirmed_fact", "attributed_citation", "hypothesis", "assumption")


def summary_admissibility(ev, used_as="confirmed_fact") -> dict:
    """某条证据能否以指定方式进入结论摘要。

    - `confirmed_fact` → **只有 `verified` 可以**；
    - `attributed_citation` → 可以（须**准确转述来源与限制**）；
    - `hypothesis` / `assumption` → 可以，但必须显式标注为命题 / 假设。
    """
    st = evidence_state(ev)
    res = {"id": (ev or {}).get("id"), "state": st, "used_as": used_as,
           "allowed": False, "require_label": False, "note": ""}
    if used_as == "confirmed_fact":
        res["allowed"] = st == "verified"
        if not res["allowed"]:
            res["note"] = (f"证据状态为 `{st}` → **不得**作为「已确认事实」用于摘要与广度/健康判断；"
                           f"可改为按来源转述（`attributed_citation`）并写明限制")
    elif used_as == "attributed_citation":
        res["allowed"] = st in ("verified", "unverified", "conflict", "pending")
        res["note"] = "须**准确转述来源、时间与分母口径**，并写明「尚未独立核实」等限制"
    elif used_as in ("hypothesis", "assumption"):
        res["allowed"] = True
        res["require_label"] = True
        res["note"] = "必须显式标注为「待检验命题」或「情景假设」"
    else:
        res["note"] = f"未知用法 {used_as}"
    return res


def citation_note(ev) -> str:
    """转述一条**未独立核实**证据时的标准措辞。"""
    st = evidence_state(ev)
    src = (ev or {}).get("source") or "来源未标注"
    if st == "verified":
        return f"来源：{src}（已独立核实）"
    if st == "unverified":
        return f"来源：{src}；**尚未独立核实**，本文仅按原文转述，不据此判断"
    if st == "conflict":
        return f"来源：{src}；**存在冲突**，原值与来源均已保留，不擅自取值"
    if st == "pending":
        return f"来源：{src}；**待验证**"
    return f"来源：{src}；**缺失**"


# ══════════════════════════════════════════════════════════════════
# 4 · 归档 vs 正文（错误原文处理 —— 本轮冲突的正面解法）
# ══════════════════════════════════════════════════════════════════

def archive_report(report: dict) -> dict:
    """把一份深挖报告**完整**归档。一节不删，**包含已确认错误的段落**。

    `report` = {report_id, generated_at, source, sections:[{id,title,text,confirmed_error,error_note}]}
    """
    secs = [dict(s) for s in (report.get("sections") or [])]
    return {
        "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at"),
        "source": report.get("source"),
        "complete": True,
        "n_sections": len(secs),
        "sections": secs,
        "note": "归档件**完整保留全文**（不摘要 / 不改写 / 不省略章节），含已确认错误段落；"
                "归档 ≠ 可引用 —— 能否进正文由 `build_body()` 决定",
    }


def build_body(archive: dict) -> dict:
    """从归档件派生**研究正文可用材料**：剔除已确认错误的段落。

    被剔除的段落仍留在归档里，只是**不得再支持正文结论**；如需展示则标 `role=error_contrast`。
    """
    keep, excluded = [], []
    for s in (archive.get("sections") or []):
        if s.get("confirmed_error"):
            excluded.append({
                "id": s.get("id"), "title": s.get("title"), "role": "error_contrast",
                "error_note": s.get("error_note"),
                "text": s.get("text"),
                "usage": "仅作**错误对照 / 原始材料**展示，禁止作为正文判断依据",
                "may_support_conclusion": False,
            })
        else:
            keep.append({"id": s.get("id"), "title": s.get("title"), "text": s.get("text"),
                         "may_support_conclusion": True})
    return {
        "from_report": archive.get("report_id"),
        "sections": keep,
        "excluded": excluded,
        "n_kept": len(keep), "n_excluded": len(excluded),
        "note": "正文只呈现本轮认可的材料；确认错误的原文**不进正文证据链**（归档里仍在）",
    }


def check_error_not_used(delivery: dict) -> dict:
    """交付自检：确认错误的原文有没有被正文继续用来支持结论。"""
    body = delivery.get("body") or {}
    summary_text = delivery.get("summary") or ""
    err_ids = {e["id"] for e in (body.get("excluded") or [])}
    err_titles = {e.get("title") for e in (body.get("excluded") or []) if e.get("title")}
    in_conclusion = [s.get("id") for s in (body.get("sections") or [])
                     if s.get("may_support_conclusion") and s.get("id") in err_ids]
    mentioned = [t for t in err_titles if t and t in summary_text]
    ok = not in_conclusion and not mentioned
    return {
        "ok": ok,
        "confirmed_error_in_body": in_conclusion,
        "confirmed_error_in_summary": mentioned,
        "note": ("确认错误未被正文继续使用" if ok else
                 "⚠️ 已确认错误的原文仍在支持正文结论 / 出现在摘要中 —— 必须剔除或降为错误对照"),
    }


# ══════════════════════════════════════════════════════════════════
# 5 · 结论 ↔ 证据对应（失效即撤回 / 改写 / 降级）
# ══════════════════════════════════════════════════════════════════

CLAIM_ACTIONS = {
    "keep": "关键证据均为 `verified` → **保留明确结论**（不要无故模糊化）",
    "downgrade": "关键证据仅 `unverified` / `pending` → **降低确定程度**（加「尚未独立核实」等限定）",
    "rewrite": "关键证据存在 `conflict` → **改写**为并列呈现原值与来源，不给单一判断",
    "retract": "关键证据 `missing` → **撤回**该结论（或明确标注为不可交付）",
    "background": "关键证据**全部落在本任务时间窗口之外** → **标为历史背景**，"
                  "不作为本窗口新增证据，不得据此下本窗口结论",
}

CERTAINTY = {"keep": "确定", "downgrade": "较低（须加限定）",
             "rewrite": "不成立（改并列）", "retract": "撤回",
             "background": "不适用（历史背景）"}


def bind_claims(claims, evidence_index: dict) -> dict:
    """为每条结论建立**证据对应关系**，并按关键证据状态给出应做的动作。

    `claims` = [{id, text, evidence:[eid...], critical_evidence:[eid...], history_evidence:[eid...]}]
    """
    out = []
    for c in claims:
        crit = c.get("critical_evidence") or c.get("evidence") or []
        hist = list(c.get("history_evidence") or [])
        states = {eid: evidence_state(evidence_index.get(eid)) for eid in crit}
        if not crit:
            act = "background" if hist else "retract"
        elif any(s == "missing" for s in states.values()):
            act = "retract"
        elif any(s == "conflict" for s in states.values()):
            act = "rewrite"
        elif any(s in ("unverified", "pending") for s in states.values()):
            act = "downgrade"
        elif all(s == "verified" for s in states.values()):
            act = "keep"
        else:
            act = "retract"
        out.append({"id": c.get("id"), "text": c.get("text"), "evidence": crit,
                    "history_evidence": hist,
                    "evidence_states": states, "action": act, "action_note": CLAIM_ACTIONS[act],
                    "certainty": CERTAINTY[act]})
    n_bad = sum(1 for r in out if r["action"] != "keep")
    return {"claims": out, "n": len(out), "n_need_action": n_bad,
            "note": "结论的确定程度**不得超过**其关键证据的状态；证据失效时同步撤回 / 改写 / 降级"}



def hedge_policy(claims_result: dict, *, style="neutral") -> dict:
    """**反过度模糊**：只有关键证据确有问题的结论才降级，其余保留明确结论（req H）。"""
    cs = claims_result.get("claims", [])
    keep = [c["id"] for c in cs if c["action"] == "keep"]
    hedge = [c["id"] for c in cs if c["action"] in ("downgrade", "rewrite", "retract")]
    bg = [c["id"] for c in cs if c["action"] == "background"]
    return {"keep_decisive": keep, "hedge": hedge, "background": bg, "style": style,
            "blanket_hedging": False,
            "note": "**不得**把全部结论一律改成模糊表述 —— 证据充分且无冲突的结论**保留明确措辞**；"
                    "窗口外资料支撑的结论**移入历史背景**，不与本窗口结论混排"}


# ══════════════════════════════════════════════════════════════════
# 6 · 比较前置：对象 / 窗口 / 基准 / 指标（req 5）
# ══════════════════════════════════════════════════════════════════

PAIR_KEYS = ("object", "window", "baseline", "metric")


def check_pair_comparability(a: dict, b: dict, keys=PAIR_KEYS) -> dict:
    """比较两处结论前，先确认对象 / 时间窗口 / 基准 / 指标一致。"""
    diffs = {}
    for k in keys:
        va, vb = (a or {}).get(k), (b or {}).get(k)
        if va != vb:
            diffs[k] = {"a": va, "b": vb}
    comparable = not diffs
    return {"comparable": comparable, "diffs": diffs,
            "note": ("四项一致，可直接比较" if comparable else
                     "**口径不一致**：" + "；".join(
                         f"{k}（{v['a']} vs {v['b']}）" for k, v in diffs.items())
                     + " → 不得直接比较，须先统一或并列说明")}


def check_support_level_claim(*, bias_series=None, price_low_data=None,
                              claimed_support=None) -> dict:
    """**乖离率未创新低 ≠ 价格前低未破**（req 5 的专门陷阱）。

    乖离率是**价格相对均线的偏离**，它不包含「前低」这一价格信息；
    用乖离率序列去断言某个**价格支撑位未破**，属于跨口径外推。
    """
    res = {"claimed_support": claimed_support, "has_bias": bool(bias_series),
           "has_price_low": bool(price_low_data), "conclusion_allowed": False, "reason": ""}
    if not price_low_data:
        res["conclusion_allowed"] = False
        res["reason"] = ("只有**乖离率序列**、没有**价格前低数据** → 乖离率反映的是价格相对均线的偏离，"
                         "**不包含前低这一价格事实**，因此**不得宣布任何价格支撑位未破**；"
                         "只能表述为「乖离率未见新低」并注明该指标不覆盖前低信息")
        res["bias_only_statement"] = "乖离率未见新低（**该指标不含前低价信息，不能推出前低未破**）"
    else:
        res["conclusion_allowed"] = True
        res["reason"] = "已取得价格前低数据 → 可基于**价格**判断前低是否被跌破（乖离率仅作辅助）"
    return res


RELATIVE_ABS = {"outperform": 1, "inline": 0, "underperform": -1}
ABSOLUTE_ABS = {"rise": 1, "flat": 0, "decline": -1}


def check_relative_absolute(*, relative, absolute) -> dict:
    """**相对强势与绝对下跌可以同时成立**，不得互相否定。

    例：近 5 日跑赢基准（相对强势）＋ 近 20 日绝对下跌 —— 两者量纲不同（相对 vs 绝对），
    **不矛盾**：跑赢一个下跌的基准，自身仍可以下跌。
    """
    r, a = RELATIVE_ABS.get(relative), ABSOLUTE_ABS.get(absolute)
    ok_input = r is not None and a is not None
    contradictory = False
    note = ""
    if not ok_input:
        note = f"未知取值（relative={relative}, absolute={absolute}）"
    elif (r <= 0 and a > 0):
        contradictory = True
        note = ("⚠️「相对跑输 / 持平」与「绝对上涨」在多数情形下需核对基准是否下跌，"
                "须确认基准涨跌后再下判断")
    else:
        note = ("相对表现与绝对表现**量纲不同，可同时成立** —— "
                "「跑赢基准」不等于「自身上涨」，「绝对下跌」也不否定「相对强势」")
    return {"relative": relative, "absolute": absolute, "compatible": ok_input and not contradictory,
            "contradictory": contradictory, "note": note}


# ══════════════════════════════════════════════════════════════════
# 7 · 覆盖率二分（req 8）
# ══════════════════════════════════════════════════════════════════

def coverage_split(*, total, checked, independently_verified) -> dict:
    """**「已检查覆盖率」≠「已独立验证覆盖率」**，两者必须分别报告。

    列入校验表只等于「已检查」（取到了数据源值并做了比对），
    不等于「已独立核实」（另一独立来源确认）。
    """
    t = max(int(total), 1)
    checked = int(checked)
    verified = int(independently_verified)
    return {
        "total": int(total), "checked": checked, "independently_verified": verified,
        "checked_rate": round(checked / t * 100, 2),
        "verified_rate": round(verified / t * 100, 2),
        "note": (f"已检查覆盖率 {checked}/{total}；**已独立验证覆盖率 {verified}/{total}** —— "
                 f"两者**不得混用**，也不得把校验表里的全部条目都算成「已验证」"),
    }


# ══════════════════════════════════════════════════════════════════
# 8 · 统计口径（req 7）
# ══════════════════════════════════════════════════════════════════

STAT_FIELDS = ("sample_size", "denominator", "dedup", "time_window")


def check_statistic(stat: dict) -> dict:
    """「多少比例的机构持某观点」这类统计必须可核对。

    必填：样本量 / 分母 / 去重规则 / 时间口径；**引用他人报告须保留归属**（不得改写成本包自行统计）。
    """
    missing = [f for f in STAT_FIELDS if not stat.get(f)]
    attribution = stat.get("attribution")
    res = {"text": stat.get("text"), "sample_size": stat.get("sample_size"),
           "denominator": stat.get("denominator"), "dedup": stat.get("dedup"),
           "time_window": stat.get("time_window"), "attribution": attribution,
           "missing": missing, "reportable": not missing,
           "own_statistic": not bool(attribution), "pct": stat.get("pct")}
    if missing:
        res["note"] = ("不可报告：缺少 " + " / ".join(missing) +
                       " → 不得输出该比例，或改为定性描述")
    elif not attribution:
        res["note"] = ("可报告，但本包**自行统计**——须说明样本口径与计算方法，"
                       "且**不得**把它描述为「市场共识」")
    else:
        res["note"] = (f"可报告；**归属：{attribution}** —— 引用时必须保留归属，"
                       f"**不得改写成本包自行统计**")
    # 一致性：分子不得大于分母
    n, d = stat.get("numerator"), stat.get("denominator")
    if isinstance(n, (int, float)) and isinstance(d, (int, float)) and d:
        res["recomputed_pct"] = round(n / d * 100, 2)
        if stat.get("pct") is not None and abs(res["recomputed_pct"] - stat["pct"]) > 0.05:
            res["pct_conflict"] = {"reported": stat["pct"], "recomputed": res["recomputed_pct"]}
    return res


# ══════════════════════════════════════════════════════════════════
# 9 · 交付范围与资料不足（req 6）
# ══════════════════════════════════════════════════════════════════

def delivery_scope(*, requested, obtained=None, failed_sources=None, sample_complete=None,
                   notes=None) -> dict:
    """资料不足时的交付判定：**交付能完成的部分 + 说明覆盖范围、缺项及其影响**。

    规则：
    - 检索失败或样本不完整 → **收缩交付范围**；
    - 样本不完整时，**不得**输出「没有样本依据的总体百分比」；
    - 不把**有限检索样本**写成「市场 / 卖方整体共识」。
    """
    requested = list(requested or [])
    obtained = list(obtained or [])
    failed = list(failed_sources or [])
    missing = [x for x in requested if x not in obtained]
    shrink = bool(failed) or bool(missing) or (sample_complete is False)
    return {
        "requested": requested, "obtained": obtained, "missing": missing,
        "failed_sources": failed,
        "coverage": f"{len(obtained)}/{len(requested)}",
        "shrink": shrink,
        "sample_complete": (sample_complete is not False) and not missing and not failed,
        "forbid_population_pct": shrink,
        "forbid_consensus_wording": shrink,
        "notes": list(notes or []),
        "note": ("交付范围完整" if not shrink else
                 f"**资料不足 → 收缩交付范围**：缺项 {missing or '—'}"
                 f"{'；失败源 ' + str(failed) if failed else ''}。"
                 "须在交付物中写明**覆盖范围与缺项影响**；"
                 "**不得**输出没有样本依据的总体百分比，"
                 "**不得**把有限检索样本写成市场 / 卖方整体共识"),
    }


# ══════════════════════════════════════════════════════════════════
# 10 · 交付状态阶梯（req G：生成成功 ≠ 研究已验证完成）
# ══════════════════════════════════════════════════════════════════

STATUS_LADDER = ["生成中", "已生成未校验", "部分校验", "已校验", "研究已验证完成"]


def resolve_status(*, generated, checked, all_critical_verified=False,
                   has_conflict=False, coverage_complete=True) -> dict:
    """按实际完成度定交付状态，**不得越级**。

    ⚠️ 深挖报告**生成成功**只说明取到材料，**不等于**研究已完成校验。
    """
    if not generated:
        status = "生成中"
    elif not checked:
        status = "已生成未校验"
    elif has_conflict or not all_critical_verified or not coverage_complete:
        status = "部分校验"
    elif all_critical_verified and coverage_complete:
        status = "已校验"
    else:
        status = "部分校验"
    can_full = (status == "已校验")
    return {
        "status": status,
        "generated": bool(generated), "checked": bool(checked),
        "all_critical_verified": bool(all_critical_verified),
        "has_conflict": bool(has_conflict), "coverage_complete": bool(coverage_complete),
        "can_claim_verified": can_full,
        "note": ("状态按实际完成度判定，**不得越级**；"
                 "报告生成成功 ≠ 研究已验证完成"
                 if not can_full else "全部关键证据已独立核实且覆盖完整"),
    }


# ══════════════════════════════════════════════════════════════════
# 10.2 · 背景材料登记与来源关系（第 4 轮新增；**不替代证据校验**）
# ══════════════════════════════════════════════════════════════════
#
# 本轮要修的病根：① 与任务有关的事实 / 数字 / 观点没有统一的来源记录，
# 说不清「哪条材料支持哪个对象、哪个问题、能不能核对」；
# ② 深挖**复述**我们传进去的背景数字，被当成**新增独立证据**；
# ③ 两个接口其实读的是同一份底层材料，却被写成「两路独立验证一致」。
#
# 处理原则：**来源关系是补充信息** —— 它回答「这算不算独立证据」，
# 但**不能替代**第 3 轮已定的五态证据（verified / unverified / conflict / pending / missing）。
# 一条材料**既是** `unverified`、**又**与另一条同源，两件事都要分别记录。

#: 材料 / 来源记录必须能够追踪的项（用户要求 1 的「至少能够追踪」清单）
MATERIAL_FIELDS = (
    "material_id",      # ① 材料或证据编号
    "object_id",        # ② 对应对象
    "question_ids",     # ③ 对应问题编号（可多个；一份材料可支持多个问题）
    "source_name",      # ④ 来源名称
    "origin_ref",       # ⑤ 原始出处 / 可核对的位置
    "material_asof",    # ⑥ 材料所属时间（哪一期）
    "published_at",     # ⑦ 公开 / 可用时间
    "usable_from",      #   可确认可用时间（第 1 轮口径）
    "fetched_at",       # ⑧ 获取时间
    "metric_meaning",   # ⑨ 指标含义
    "unit",             # ⑩ 单位
    "calibre",          # ⑪ 统计口径
    "evidence_state",   # ⑫ 核实状态（**沿用五态，不新增第六态**）
    "claim_kind",       # ⑬ 原始事实 / 计算结果 / 外部判断 / 用户待检验观点
    "relations",        # ⑭ 与其他材料的引用 / 转述 / 同源关系
)

#: 时间类字段：未知时必须**显式标 `unknown`**（不得留空冒充已知时间）
MATERIAL_TIME_FIELDS = ("material_asof", "published_at", "usable_from", "fetched_at")

#: 材料性质 → 复用既有 `STATEMENT_KINDS` 的写入规则（**不另起一套分类**）
MATERIAL_KINDS = {
    "primary_fact": ("原始事实（有可核对的公开来源）", "disclosed_fact"),
    "computed": ("计算结果（本包按公式算出，须留方法与输入）", "estimate"),
    "external_judgement": ("外部判断（他人 / 机构的观点，**不等于事实**）", "inference"),
    "user_hypothesis": ("用户待检验观点（先作为命题登记）", "user_hypothesis"),
}

#: 来源关系（**补充信息**，不替代五态证据校验）
SOURCE_RELATIONS = {
    "quote": "引用（逐字 / 近逐字取自同一来源）",
    "paraphrase": "转述（同一来源的改写表达）",
    "same_underlying": "底层同源（不同接口 / 报告指向同一原始材料）",
    "derived_from": "派生（在其基础上计算 / 推断而来）",
    "independent": "独立（来源不同，可互为验证）",
    "unknown": "关系未知（**不得默认独立，也不得武断认定同源**）",
}

#: 计入「同一来源」的关系（关系图里按并查集合并）
SAME_SOURCE_RELATIONS = ("quote", "paraphrase", "same_underlying", "derived_from")

#: 独立性三态（用户要求 3）
INDEPENDENCE_STATES = (
    "independent",   # 已确认来源不同 → 可互为验证
    "same_source",   # 已确认同源（引用 / 转述 / 底层同源 / 派生）→ **不得**算独立验证
    "unknown",       # 关系未知 → **不得默认独立，也不得武断认定同源**
)

INDEPENDENCE_NOTES = {
    "independent": "已确认来源不同 → 可互为验证",
    "same_source": "已确认同源 → **同一份信息不得重复计算为独立证据**",
    "unknown": "来源关系未知 → **不得默认独立**，**也不得**武断认定同源；"
               "只能如实标注未知",
}

#: 深挖复述背景 → **不算新增独立证据**（用户要求 3）
ECHO_RULE = (
    "**深挖复述已传入的背景数字，不算新增独立证据** —— 同一来源、同一口径的重复出现，"
    "只证明「传进去了并且被复述」，不构成第二路验证。"
)


def make_material(mid, statement, *, object_id=None, question_ids=None,
                  source_name=None, origin_ref=None, material_asof=None,
                  published_at=None, usable_from=None, fetched_at=None,
                  metric_meaning=None, unit=None, calibre=None, value=None,
                  period=None, claim_kind="primary_fact", relations=None,
                  evidence_attrs=None, note=None, unknown_fields=None) -> dict:
    """登记一条**背景材料 / 来源记录**（追踪字段见 `MATERIAL_FIELDS`）。

    · 未提供的时间 / 口径等字段**不是留空冒充已知** —— 会进入 `unknown_fields`
      并标 `unknown`（用户要求 1：「未知项明确标记」）；
    · `evidence_attrs` 直通 `make_evidence()` —— 本记录**内嵌**证据视图，
      **不另起一套核实状态**：`evidence_state` 恒由 `evidence_state()` 现算。
    """
    ev = make_evidence(mid, statement, value=value, unit=unit, period=period,
                       **(evidence_attrs or {}))
    if claim_kind not in MATERIAL_KINDS:
        raise ValueError(f"未知 material claim_kind：{claim_kind}（限 {tuple(MATERIAL_KINDS)}）")
    rec = {
        "material_id": mid,
        "object_id": object_id,
        "question_ids": list(question_ids or []),
        "source_name": source_name,
        "origin_ref": origin_ref,
        "material_asof": material_asof,
        "published_at": published_at,
        "usable_from": usable_from,
        "fetched_at": fetched_at,
        "metric_meaning": metric_meaning,
        "unit": unit,
        "calibre": calibre,
        "claim_kind": claim_kind,
        "relations": list(relations or []),
        "statement": statement,
        "value": value,
        "note": note,
        "evidence": ev,
        "evidence_state": evidence_state(ev),
        "statement_kind": MATERIAL_KINDS[claim_kind][1],
        "claim_label": MATERIAL_KINDS[claim_kind][0],
    }
    # 时间类字段：未给 → 显式 unknown（**不冒充已知**）
    for f in MATERIAL_TIME_FIELDS:
        if rec.get(f) is None:
            rec[f] = "unknown"
    rec["unknown_fields"] = sorted(set(list(unknown_fields or []) + material_unknown_fields(rec)))
    return rec


def material_unknown_fields(mat) -> list:
    """材料里**仍未知**的追踪项（时间类 `unknown` 与其余 `None` 都算）。"""
    out = []
    for f in MATERIAL_FIELDS:
        if f in ("relations", "evidence_state"):
            continue
        v = (mat or {}).get(f)
        if v is None or v == "unknown" or v == [] or v == "":
            out.append(f)
    return sorted(out)


def material_statement_kind(mat) -> str:
    """材料性质 → 既有 `STATEMENT_KINDS` 的键（供 `classify_statement()` 复用）。"""
    return MATERIAL_KINDS.get((mat or {}).get("claim_kind"), (None, "disclosed_fact"))[1]


class SourceGraph:
    """材料 + 来源关系图。**回答「这算不算独立证据」，不替代五态证据校验。**

    · 关系入图：`relate(a, b, relation)`；
    · `independence(a, b)`：按并查集求**同源组**，只有**显式** `independent` 才判独立；
      其余一律 `unknown` —— **不得默认独立**。
    """

    def __init__(self, materials=None, relations=None):
        self.materials = {}
        for m in (materials or []):
            if isinstance(m, dict) and m.get("material_id") is not None:
                self.materials[m["material_id"]] = m
        self.relations = []
        for r in (relations or []):
            if isinstance(r, dict):
                self.relations.append(dict(r))
            elif isinstance(r, (list, tuple)) and len(r) >= 3:
                self.relations.append({"a": r[0], "b": r[1], "relation": r[2]})
        self._independent = set()
        self._rebuild()

    # ——— 关系登记 ———
    def relate(self, a, b, relation="unknown", note=None):
        if relation not in SOURCE_RELATIONS:
            raise ValueError(f"未知来源关系：{relation}（限 {tuple(SOURCE_RELATIONS)}）")
        self.relations.append({"a": a, "b": b, "relation": relation, "note": note})
        self._rebuild()
        return {"a": a, "b": b, "relation": relation,
                "label": SOURCE_RELATIONS[relation], "note": note}

    # ——— 并查集（**整体重建**，`_independent` 也从 relations 现算，避免漏登记） ———
    def _rebuild(self):
        self._parent = {mid: mid for mid in self.materials}
        self._independent = set()
        for r in self.relations:
            for x in (r.get("a"), r.get("b")):
                if x is not None:
                    self._parent.setdefault(x, x)
            if r.get("relation") == "independent":
                self._independent.add(frozenset((r.get("a"), r.get("b"))))
            elif r.get("relation") in SAME_SOURCE_RELATIONS:
                self._union(r.get("a"), r.get("b"))

    def _find(self, x):
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def _union(self, a, b):
        if a is None or b is None:
            return
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[rb] = ra

    # ——— 独立性 ———
    def independence(self, a, b) -> dict:
        """两处证据的独立性**三态**。未登记关系 → `unknown`（**不得默认独立**）。"""
        if a is None or b is None:
            return {"state": "unknown", "a": a, "b": b,
                    "why": "缺材料编号 → 无法判定来源关系，标未知",
                    "counts_as_independent": False}
        if a == b or self._find(a) == self._find(b):
            rels = [r for r in self.relations
                    if {r.get("a"), r.get("b")} & {a, b}]
            return {"state": "same_source", "a": a, "b": b,
                    "why": INDEPENDENCE_NOTES["same_source"],
                    "relations": rels, "counts_as_independent": False}
        if frozenset((a, b)) in self._independent:
            return {"state": "independent", "a": a, "b": b,
                    "why": INDEPENDENCE_NOTES["independent"],
                    "counts_as_independent": True}
        return {"state": "unknown", "a": a, "b": b,
                "why": INDEPENDENCE_NOTES["unknown"], "counts_as_independent": False}

    def per_evidence_independence(self, eid) -> dict:
        """**按具体证据**给出它与其余材料的关系（**不是整份报告一刀切**）。"""
        rels = [r for r in self.relations if eid in (r.get("a"), r.get("b"))]
        others = sorted({x for r in rels for x in (r.get("a"), r.get("b")) if x != eid})
        return {
            "material_id": eid,
            "relations": rels,
            "related_materials": others,
            "same_source_with": sorted(
                o for o in others if self.independence(eid, o)["state"] == "same_source"),
            "independent_with": sorted(
                o for o in others if self.independence(eid, o)["state"] == "independent"),
            "note": "**按具体证据**判定 —— **不得**因为一处同源就把整份报告的其他证据全部归为同源",
        }

    def summary(self) -> dict:
        groups = {}
        for mid in self._parent:
            groups.setdefault(self._find(mid), []).append(mid)
        multi = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
        return {
            "n_materials": len(self.materials),
            "n_relations": len(self.relations),
            "same_source_groups": multi,
            "independent_pairs": sorted([sorted(p) for p in self._independent]),
            "relations": self.relations,
            "rule": "来源关系是**补充信息**，**不替代**五态证据校验",
        }


def echo_check(returned, sent_background) -> dict:
    """判断一条**返回证据**是不是「已传入背景的复述」。

    命中判据（任一）：`material_id` 相同；`value` + `unit` 相同；`value` + `period` + `calibre`
    相同；或在值相同的前提下陈述文字互相包含。
    **复述 ≠ 新增独立证据**。
    """
    r = returned or {}
    rv, ru = r.get("value"), r.get("unit")
    matched = []
    for b in (sent_background or []):
        b = b or {}
        why = None
        if r.get("material_id") is not None and r.get("material_id") == b.get("material_id"):
            why = "material_id 相同 → 直接是同一份材料"
        elif rv is not None and rv == b.get("value") and ru == b.get("unit"):
            why = f"数值与单位相同（{rv}{ru or ''}）→ 与已传入背景同一数字"
        elif (rv is not None and rv == b.get("value") and r.get("period") == b.get("period")
              and r.get("calibre") == b.get("calibre") and r.get("period") is not None):
            why = f"数值 + 期间 + 口径相同（{rv} / {r.get('period')}）→ 同一数字"
        elif (rv is not None and rv == b.get("value")
              and (r.get("statement") or "") and (b.get("statement") or "")
              and ((r.get("statement") in b.get("statement"))
                   or (b.get("statement") in r.get("statement")))):
            why = "陈述文字互相包含且数值相同 → 疑似同一数字的复述"
        if why:
            matched.append({"material_id": b.get("material_id"), "why": why})
    is_echo = bool(matched)
    return {
        "is_echo": is_echo,
        "matched": matched,
        "counts_as_new_evidence": not is_echo,
        "why": (f"报告复述了已传入的背景数字（{matched[0]['why']}）→ "
                f"**不算新增独立证据**" if is_echo else "未匹配到已传入背景 → 可作新增材料处理"),
        "rule": ECHO_RULE,
    }


def can_claim_two_way_independent(a, b, *, graph=None, a_is_echo=False, b_is_echo=False) -> dict:
    """能否写成「**两路独立验证一致**」。

    仅当 ① 独立性确认为 `independent`，**且** ② 两者都不是已传入背景的复述时**才允许**。
    """
    ind = (graph or SourceGraph()).independence(a, b)
    blocked = []
    if ind["state"] != "independent":
        blocked.append(f"来源关系为 `{ind['state']}`（{INDEPENDENCE_NOTES[ind['state']]}）")
    if a_is_echo or b_is_echo:
        blocked.append("其中至少一项是**已传入背景的复述**（同一来源，不构成第二路）")
    allowed = not blocked
    return {
        "allowed": allowed,
        "a": a, "b": b, "independence": ind["state"],
        "a_is_echo": bool(a_is_echo), "b_is_echo": bool(b_is_echo),
        "blocked_by": blocked,
        "note": ("独立性已确认且均非背景复述 → 可写「两路独立验证一致」" if allowed else
                 "**不得**写成「两路独立验证一致」 —— " + "；".join(blocked)),
    }


def check_independence_claims(claims, *, graph=None, echoes=None) -> dict:
    """交付前的**独立性自检**：逐条结论检查它引用的证据之间是否被当成独立验证。

    · 任一被引用证据是背景复述 → 该结论**不得**声称新增独立验证；
    · 被判同源的两条证据**不得**同时计入「多路验证」。
    """
    g = graph or SourceGraph()
    echoes = echoes or {}
    issues, rows = [], []
    for c in (claims or []):
        eids = list(c.get("evidence") or c.get("critical_evidence") or [])
        echo_ids = [e for e in eids if (echoes.get(e) or {}).get("is_echo")]
        same_pairs = []
        for i, x in enumerate(eids):
            for y in eids[i + 1:]:
                st = g.independence(x, y)["state"]
                if st == "same_source":
                    same_pairs.append([x, y])
        claimed_two_way = bool(c.get("claims_two_way_independent"))
        if claimed_two_way and (echo_ids or same_pairs):
            parts = []
            if echo_ids:
                parts.append("含背景复述 " + str(echo_ids))
            if same_pairs:
                parts.append("同源对 " + str(same_pairs))
            issues.append(f"结论 {c.get('id')} 声称「两路独立验证」，但 " + "；".join(parts))
        rows.append({"claim": c.get("id"), "evidence": eids,
                     "echo_evidence": echo_ids, "same_source_pairs": same_pairs,
                     "claimed_two_way": claimed_two_way})
    return {
        "ok": not issues, "n_issues": len(issues), "issues": issues, "rows": rows,
        "n_echo": sum(1 for v in echoes.values() if (v or {}).get("is_echo")),
        "note": ("未发现把同源 / 复述当成独立验证的表述" if not issues
                 else "存在把同源或背景复述当独立验证的表述，须修正"),
        "rule": "**不得仅因为两个结果一致，就写成「两路独立验证一致」**",
    }


# ══════════════════════════════════════════════════════════════════
# 10.5 · 端到端离线装配：输入材料 → 交付物 → 一致性自检
# ══════════════════════════════════════════════════════════════════

def assemble_delivery(case: dict) -> dict:
    """从**输入材料**装配一份交付物（纯离线，不联网、不调接口）。

    产出的四个部分必须互相一致：**摘要 / 正文 / 表格 / 附件说明**。
    """
    task = case.get("task") or {}
    archive = archive_report(case.get("report") or {})
    body = build_body(archive)

    idx = {}
    for e in case.get("evidence", []):
        idx[e["id"]] = make_evidence(e["id"], e.get("text", ""), **e.get("attrs", {}))
    win = tuple(task.get("window") or (None, None))
    tbl = evidence_table(list(idx.values()), window=win)

    claims_in = []
    for c in case.get("claims", []):
        c = dict(c)
        # 窗口归属：把窗口外资料从「关键证据」里摘出来，只作历史背景
        crit = list(c.get("critical_evidence") or [])
        hist = [eid for eid in crit
                if window_ref(idx.get(eid), tuple(task.get("window") or (None, None)))["role"] != "in_window"]
        c["critical_evidence"] = [eid for eid in crit if eid not in hist]
        c["history_evidence"] = hist
        claims_in.append(c)
    cr = bind_claims(claims_in, idx)
    policy = hedge_policy(cr)

    # 结论摘要：只允许 verified 作为「已确认事实」；其余按实际状态转述或降级
    summary = []
    for c in cr["claims"]:
        hist = c.get("history_evidence") or []
        item = {"claim": c["id"], "text": c["text"], "basis": c["evidence"],
                "history_basis": hist,
                "history_note": ("窗口外资料仅作**历史背景**，不计入本窗口新增证据" if hist else "")}
        if c["action"] == "keep":
            item.update({"mode": "已确认",
                         "note": "关键证据已独立核实，保留明确结论"})
        elif c["action"] == "downgrade":
            item.update({"mode": "限定表述",
                         "note": "关键证据尚未独立核实 → 加限定，**不得**写成已确认事实"})
        elif c["action"] == "rewrite":
            item.update({"mode": "并列呈现",
                         "note": "证据存在冲突 → 并列原值与来源，不给单一判断"})
        elif c["action"] == "background":
            item.update({"mode": "历史背景",
                         "note": "关键证据全部落在本窗口之外 → **只作历史背景**，"
                                 "不计入本窗口新增证据，不得据此下本窗口结论"})
        else:
            item.update({"mode": "撤回",
                         "note": "关键证据缺失 → 撤回该结论 / 明确标为不可交付"})
        summary.append(item)

    src = case.get("sources") or {}
    scope = delivery_scope(requested=src.get("requested", []), obtained=src.get("obtained", []),
                           failed_sources=src.get("failed", []),
                           sample_complete=src.get("sample_complete"))
    cov = case.get("coverage")
    coverage = (coverage_split(total=cov["total"], checked=cov["checked"],
                               independently_verified=cov.get("independently_verified",
                                                              cov.get("verified")))
                if cov else None)

    stats = [check_statistic(s) for s in case.get("stats", [])]

    status = resolve_status(generated=bool(archive.get("report_id")),
                            checked=case.get("checked", False),
                            all_critical_verified=case.get("all_critical_verified", False),
                            has_conflict=any(e.get("conflicting") for e in idx.values()),
                            coverage_complete=scope["sample_complete"])

    # —— 第 4 轮：来源关系 / 背景复述 / 独立性（**不替代**上面的五态证据校验） ——
    mats = case.get("materials") or []
    graph = SourceGraph(materials=mats, relations=case.get("relations"))
    # 材料 → 证据 → 问题的对应关系（用户要求 3：三个环节可解释）
    mat_by_id = {m.get("material_id"): m for m in mats if isinstance(m, dict)}
    sent_background = list(case.get("sent_background") or [])
    echoes = {}
    for e in case.get("evidence", []):
        r = {"material_id": e.get("id"), "value": e.get("value"),
             "unit": e.get("unit"), "period": e.get("period"),
             "calibre": (e.get("attrs") or {}).get("calibre") or e.get("calibre"),
             "statement": e.get("text") or e.get("statement")}
        echoes[e.get("id")] = echo_check(r, sent_background)
    question_of = {m.get("material_id"): list(m.get("question_ids") or []) for m in mats
                   if isinstance(m, dict)}
    link = []
    for e in case.get("evidence", []):
        eid = e.get("id")
        m = mat_by_id.get(eid)
        link.append({
            "evidence_id": eid,
            "material_id": eid if m else None,
            "question_ids": question_of.get(eid, []),
            "sent_as_background": any(x.get("material_id") == eid for x in sent_background),
            "is_background_echo": bool((echoes.get(eid) or {}).get("is_echo")),
            "counts_as_new_evidence": bool((echoes.get(eid) or {}).get("counts_as_new_evidence")),
        })
    indep = check_independence_claims(case.get("claims") or [], graph=graph, echoes=echoes)

    delivery = {
        "task": {"id": task.get("id"), "object": task.get("object"),
                 "window": task.get("window")},
        "status": status,
        "summary": summary,
        "body": body,
        "table": tbl,
        "scope": scope,
        "coverage": coverage,
        "stats": stats,
        "appendix": {
            "report_id": archive.get("report_id"),
            "generated_at": archive.get("generated_at"),
            "source": archive.get("source"),
            "archived_sections": archive.get("n_sections"),
            "body_sections": body["n_kept"],
            "excluded_sections": body["n_excluded"],
            "excluded_scope": "完整归档；仅作错误对照展示，不参与正文结论",
            "evidence_counts": tbl["counts"],
            "window": task.get("window"),
        },
        "hedge_policy": policy,
        "source": {
            "n_materials": len(mat_by_id),
            "graph": graph.summary(),
            "material_evidence_question": link,
            "echoes": echoes,
            "n_echo": sum(1 for v in echoes.values() if v["is_echo"]),
            "independence": indep,
            "note": "保留**原始材料 / 实际发送背景 / 返回证据**三者的对应关系，"
                    "能够解释最终判断依据了哪些信息",
        },
    }
    delivery["consistency"] = check_delivery_consistency(delivery)
    return delivery


def check_delivery_consistency(d: dict) -> dict:
    """一致性自检：**摘要 / 正文 / 表格 / 附件说明** 四者必须互相对得上。"""
    issues = []

    # ① 摘要的每条结论，其证据状态必须与表格一致
    rows_by_id = {r["id"]: r for r in (d.get("table") or {}).get("rows", [])}
    tbl_state = {k: v["state"] for k, v in rows_by_id.items()}
    for s in d.get("summary", []):
        for eid in s.get("basis", []):
            if eid not in tbl_state:
                issues.append(f"摘要结论 {s['claim']} 引用了表格中不存在的证据 {eid}")
        states = {tbl_state.get(eid) for eid in s.get("basis", [])}
        if s["mode"] == "已确认" and not states <= {"verified"}:
            issues.append(f"摘要结论 {s['claim']} 标为「已确认」，但证据状态为 {sorted(states)}")
        if s["mode"] == "限定表述" and s["basis"] and "unverified" not in states \
                and "pending" not in states:
            issues.append(f"摘要结论 {s['claim']} 标为「限定表述」，但无未核实/待验证证据")
        # 窗口外资料不得充作本窗口证据链
        bad_win = [eid for eid in s.get("basis", [])
                   if rows_by_id.get(eid, {}).get("counts_as_new_evidence") is False]
        if bad_win:
            issues.append(f"摘要结论 {s['claim']} 把窗口外/无时间的资料 {bad_win} 计入了本窗口证据")
        # 历史背景必须显式标注
        for eid in s.get("history_basis", []):
            if rows_by_id.get(eid, {}).get("window_role") != "history":
                issues.append(f"摘要结论 {s['claim']} 把 {eid} 标为历史背景，但其窗口归属不是 history")

    # ② 正文不得使用已确认错误段落
    err_ids = {e["id"] for e in (d.get("body") or {}).get("excluded", [])}
    for sec in (d.get("body") or {}).get("sections", []):
        if sec.get("id") in err_ids:
            issues.append(f"正文含已确认错误段落 {sec['id']}")

    # ③ 表格条数 == 证据条数
    if (d.get("table") or {}).get("n") != len((d.get("table") or {}).get("rows", [])):
        issues.append("证据表的计数与行数不一致")

    # ④ 附件说明与归档件一致
    ap = d.get("appendix") or {}
    if ap.get("archived_sections") != (ap.get("body_sections", 0) + ap.get("excluded_sections", 0)):
        issues.append("附件说明：归档节数 ≠ 正文节数 + 剔除节数")
    if not ap.get("report_id") or not ap.get("generated_at") or not ap.get("source"):
        issues.append("附件说明：缺少报告编号 / 生成时间 / 来源")

    # ⑤ 状态阶梯与实际完成度一致
    st = d.get("status") or {}
    if st.get("status") == "已校验" and not st.get("all_critical_verified"):
        issues.append("状态标为「已校验」但关键证据未全部独立核实")
    if st.get("can_claim_verified") and d.get("scope", {}).get("shrink"):
        issues.append("状态声称已验证，但交付范围已收缩（覆盖不完整）")

    # ⑥ 样本不完整时：不得输出**本包自行统计**的总体百分比，也不得写成「市场共识」
    #   （引自他人报告、带归属且有完整口径的统计，允许保留——它有自己的样本依据）
    if (d.get("scope") or {}).get("forbid_population_pct"):
        for s in d.get("stats", []):
            if s.get("pct") is not None and s.get("own_statistic"):
                issues.append(f"样本不完整却输出了本包自行统计的总体百分比：{s.get('text')}")
    if (d.get("scope") or {}).get("forbid_consensus_wording"):
        for s in d.get("stats", []):
            if "共识" in (s.get("text") or ""):
                issues.append(f"有限样本不得写成「市场/卖方共识」：{s.get('text')}")

    # ⑦ 覆盖率二分必须同时给出
    cov = d.get("coverage")
    if cov and ("checked_rate" not in cov or "verified_rate" not in cov):
        issues.append("覆盖率未区分「已检查」与「已独立验证」")

    # ⑧ 来源关系（第 4 轮）：不得把同源 / 背景复述当成独立验证
    src = d.get("source") or {}
    ind = src.get("independence") or {}
    if ind and not ind.get("ok", True):
        for it in ind.get("issues", []):
            issues.append(f"独立性自检：{it}")
    # 被标为历史参考的旧报告不得同时充当本窗口新增证据
    for lk in (src.get("material_evidence_question") or []):
        if lk.get("is_background_echo") and lk.get("counts_as_new_evidence"):
            issues.append(f"证据 {lk.get('evidence_id')} 标为背景复述却同时计为新增证据")

    return {"ok": not issues, "n_issues": len(issues), "issues": issues,
            "checks": ["摘要-表格证据状态一致", "正文未用已确认错误段落", "表格计数自洽",
                       "附件说明与归档一致", "状态阶梯未越级", "样本不足不发总体百分比",
                       "覆盖率二分齐备", "来源关系：同源 / 复述不充作独立验证"],
            "note": "摘要 / 正文 / 表格 / 附件说明四者一致" if not issues else "存在不一致，须修正"}


# ══════════════════════════════════════════════════════════════════
# 11 · 检查清单驱动：让用例夹具直接调用**生产函数**
# ══════════════════════════════════════════════════════════════════

_DISPATCH = ("evidence_state", "summary", "archive", "error_use", "claims", "hedge",
             "window", "statement", "pair", "support_level", "rel_abs",
             "coverage", "statistic", "delivery", "status", "delivery_case",
             # 第 4 轮：来源关系 / 背景复述 / 独立性
             "material", "independence", "echo", "two_way", "source_case")


def _run_one(chk: dict, base_dir=None) -> dict:
    t = chk["type"]
    if t == "evidence_state":
        ev = make_evidence(chk.get("id", "E"), chk.get("text", ""), **chk.get("attrs", {}))
        return {"evidence": ev, "state": evidence_state(ev),
                "is_fact_grade": is_fact_grade(ev)}
    if t == "summary":
        ev = make_evidence(chk.get("id", "E"), chk.get("text", ""), **chk.get("attrs", {}))
        return summary_admissibility(ev, used_as=chk.get("used_as", "confirmed_fact"))
    if t == "archive":
        arch = archive_report(chk["report"])
        body = build_body(arch)
        return {"archive": arch, "body": body}
    if t == "error_use":
        arch = archive_report(chk["report"])
        body = build_body(arch)
        return check_error_not_used({"body": body, "summary": chk.get("summary", "")})
    if t == "claims":
        idx = {}
        for e in chk.get("evidence", []):
            idx[e["id"]] = make_evidence(e["id"], e.get("text", ""), **e.get("attrs", {}))
        return bind_claims(chk.get("claims", []), idx)
    if t == "hedge":
        idx = {}
        for e in chk.get("evidence", []):
            idx[e["id"]] = make_evidence(e["id"], e.get("text", ""), **e.get("attrs", {}))
        cr = bind_claims(chk.get("claims", []), idx)
        return {"claims_result": cr, "policy": hedge_policy(cr)}
    if t == "window":
        ev = make_evidence(chk.get("id", "E"), chk.get("text", ""), **chk.get("attrs", {}))
        return window_ref(ev, tuple(chk.get("window", (None, None))))
    if t == "statement":
        st = classify_statement(chk.get("text", ""), kind=chk.get("kind"))
        if chk.get("section"):
            st["misuse_check"] = check_statement_misuse(
                {"text": chk.get("text", ""), "kind": chk.get("kind")}, chk["section"])
        return st
    if t == "pair":
        return check_pair_comparability(chk["a"], chk["b"])
    if t == "support_level":
        return check_support_level_claim(bias_series=chk.get("bias_series"),
                                         price_low_data=chk.get("price_low_data"),
                                         claimed_support=chk.get("claimed_support"))
    if t == "rel_abs":
        return check_relative_absolute(relative=chk["relative"], absolute=chk["absolute"])
    if t == "coverage":
        return coverage_split(total=chk["total"], checked=chk["checked"],
                              independently_verified=chk["verified"])
    if t == "statistic":
        return check_statistic(chk["stat"])
    if t == "delivery":
        return delivery_scope(**chk["request"])
    if t == "status":
        return resolve_status(**chk["request"])
    if t == "delivery_case":
        if "case_file" in chk:
            import os
            p = os.path.join(base_dir or ".", chk["case_file"])
            case = json.loads(open(p, encoding="utf-8").read())
        else:
            case = chk["case"]
        d = assemble_delivery(case)
        return {"delivery": d, "consistency": d["consistency"],
                "status": d["status"]["status"],
                "can_claim_verified": d["status"]["can_claim_verified"],
                "summary_modes": [s["mode"] for s in d["summary"]],
                "body_kept": d["body"]["n_kept"], "body_excluded": d["body"]["n_excluded"],
                "appendix": d["appendix"], "scope_shrink": d["scope"]["shrink"],
                "forbid_population_pct": d["scope"]["forbid_population_pct"],
                "coverage": d["coverage"], "claims": d["hedge_policy"],
                "table_counts": d["table"]["counts"],
                # 第 4 轮：来源关系视图
                "source": d["source"], "n_echo": d["source"]["n_echo"],
                "consistency_ok": d["consistency"]["ok"],
                "consistency_issues": d["consistency"]["issues"],
                "graph": d["source"]["graph"], "independence": d["source"]["independence"]}
    # —— 第 4 轮：来源关系 / 背景复述 / 独立性 ——
    if t == "material":
        kw = dict(chk.get("kwargs") or {})
        mat = make_material(chk.get("id", "M"), chk.get("text", ""), **kw)
        return {"material": mat, "evidence_state": mat["evidence_state"],
                "unknown_fields": mat["unknown_fields"],
                "n_unknown": len(mat["unknown_fields"]),
                "claim_kind": mat["claim_kind"], "statement_kind": mat["statement_kind"],
                "claim_label": mat["claim_label"],
                "as_fact_allowed": summary_admissibility(mat["evidence"], "confirmed_fact")["allowed"]}
    if t == "independence":
        g = SourceGraph(materials=chk.get("materials"),
                        relations=chk.get("relations"))
        res = g.independence(chk.get("a"), chk.get("b"))
        res["summary"] = g.summary()
        # 给了 `focus` 才附「**按具体证据**」视图（用户要求 3：
        # 不因一处同源，就把整份报告的其他证据全部归为同源）
        if chk.get("focus") is not None:
            res["per_evidence"] = g.per_evidence_independence(chk["focus"])
        return res
    if t == "echo":
        return echo_check(chk.get("returned"), chk.get("background"))
    if t == "two_way":
        g = SourceGraph(materials=chk.get("materials"), relations=chk.get("relations"))
        return can_claim_two_way_independent(chk.get("a"), chk.get("b"), graph=g,
                                             a_is_echo=bool(chk.get("a_is_echo")),
                                             b_is_echo=bool(chk.get("b_is_echo")))
    if t == "source_case":
        if "case_file" in chk:
            import os
            p = os.path.join(base_dir or ".", chk["case_file"])
            case = json.loads(open(p, encoding="utf-8").read())
        else:
            case = chk["case"]
        d = assemble_delivery(case)
        ind = d["source"]["independence"]
        return {"consistency": d["consistency"], "source": d["source"],
                "n_echo": d["source"]["n_echo"], "ind_ok": ind["ok"],
                "ind_issues": ind["issues"], "ind_rows": ind["rows"],
                "graph": d["source"]["graph"],
                "materials_linked": d["source"]["material_evidence_question"],
                "summary_modes": [s["mode"] for s in d["summary"]]}
    raise ValueError(f"未知检查类型：{t}")


def _eq(cur, want) -> bool:
    if isinstance(want, bool) or isinstance(cur, bool):
        return cur is want
    if isinstance(cur, list) and isinstance(want, int):
        return len(cur) == want
    if isinstance(cur, dict) and isinstance(want, int):
        return len(cur) == want
    if isinstance(cur, (int, float)) and isinstance(want, (int, float)):
        if want == 0:
            return abs(cur) <= 1e-9
        return abs(cur - want) <= max(0.02, abs(want) * 1e-3)
    return cur == want


def _assert_expect(got: dict, expect: dict) -> list:
    bad = []
    for path, want in (expect or {}).items():
        segs = path.split(".")
        negate = segs[-1].startswith("not_")
        if negate:
            segs[-1] = segs[-1][4:]
        cur = got
        for seg in segs:
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                cur = None
                break
        hit = _eq(cur, want)
        if negate:
            if hit:
                bad.append(f"{'.'.join(segs)} 不应等于 {want!r}（实际 {cur!r}）")
        elif not hit:
            bad.append(f"{path} 期望 {want!r}，实际 {cur!r}")
    return bad


def run_check_spec(doc: dict, base_dir=None) -> dict:
    """执行一份检查清单。每项都调 `_DISPATCH` 里的**生产函数**，再按 `expect` 断言。"""
    results, n_fail = [], 0
    for chk in doc.get("checks", []):
        if chk["type"] not in _DISPATCH:
            results.append({"label": chk.get("label"), "type": chk["type"],
                            "pass": False, "detail": "未知检查类型"})
            n_fail += 1
            continue
        got = _run_one(chk, base_dir)
        bad = _assert_expect(got, chk.get("expect", {}))
        ok = not bad
        n_fail += 0 if ok else 1
        results.append({"label": chk.get("label"), "type": chk["type"], "pass": ok,
                        "detail": "符合预期" if ok else "；".join(bad), "got": got})
    return {"case": doc.get("case"), "n_checks": len(results), "n_fail": n_fail,
            "pass": n_fail == 0, "results": results}


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def _p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def selftest() -> int:
    """自检：用**内置真实场景**核对本模块的核心判定（不依赖外部文件）。"""
    ok = True

    # ── A · 已确认错误的原文：可完整归档，正文不得据此判断 ──
    rep = {"report_id": "RPT-2026-0926-0001", "generated_at": "2026-09-26T14:05:00+08:00",
           "source": "shenwa:tid=9",
           "sections": [
               {"id": "s1", "title": "估值推演", "text": "PE 18x、PB 3.2x、股息率 1.2% …",
                "confirmed_error": True, "error_note": "三个输入锚点全失真（实际 15.98 / 3.64 / 2.80%）"},
               {"id": "s2", "title": "行业格局", "text": "……"},
               {"id": "s3", "title": "竞争位次", "text": "……"}]}
    arch = archive_report(rep)
    body = build_body(arch)
    assert arch["complete"] is True and arch["n_sections"] == 3, arch
    assert body["n_kept"] == 2 and body["n_excluded"] == 1, body
    assert body["excluded"][0]["may_support_conclusion"] is False, body["excluded"]
    assert body["excluded"][0]["role"] == "error_contrast"
    print(f"[ok] A 归档完整 {arch['n_sections']} 节（含 1 节确认错误）；正文保留 {body['n_kept']} 节、"
          f"剔除 {body['n_excluded']} 节并标 error_contrast")

    # ── B · 未独立核实的比例：不得作为已确认事实 ──
    ev_b = make_evidence("E-B1", "站上均线比例 88.68%", source="深挖报告 RPT-…", observed_at="2026-09-30")
    assert evidence_state(ev_b) == "unverified"
    assert summary_admissibility(ev_b, "confirmed_fact")["allowed"] is False
    assert summary_admissibility(ev_b, "attributed_citation")["allowed"] is True
    assert "尚未独立核实" in citation_note(ev_b)
    print(f"[ok] B 88.68% → `unverified`：**不得**作已确认事实（allowed=False），"
          f"可按来源转述并注明限制")

    # ── C · 只有乖离率、无价格前低 → 不得宣布支撑位未破 ──
    c1 = check_support_level_claim(bias_series=[-0.44, -0.31], price_low_data=None,
                                   claimed_support="前低 88.0 未破")
    assert c1["conclusion_allowed"] is False and "不得宣布" in c1["reason"]
    c2 = check_support_level_claim(bias_series=[-0.44], price_low_data=[88.0, 86.2])
    assert c2["conclusion_allowed"] is True
    print("[ok] C 只有乖离率 → conclusion_allowed=False；补上价格前低数据 → True")

    # ── D · 相对跑赢 + 绝对下跌 可同时成立 ──
    d = check_relative_absolute(relative="outperform", absolute="decline")
    assert d["compatible"] is True and d["contradictory"] is False
    print("[ok] D 近5日相对跑赢 + 近20日绝对下跌 → compatible=True（量纲不同，不互相否定）")

    # ── E · 窗口外资料只作历史背景 ──
    ev_e = make_evidence("E-E1", "9 月初的行业政策", source="旧报告", observed_at="2026-09-10")
    w = window_ref(ev_e, ("2026-09-24", "2026-09-30"))
    assert w["role"] == "history" and w["counts_as_new_evidence"] is False
    print("[ok] E 09-10 资料 vs 窗口 09-24~09-30 → role=history，不计入窗口内新增证据")

    # ── F · 资料不足 → 收缩范围，禁输出总体百分比 ──
    f = delivery_scope(requested=["行情", "财务", "资金", "研报舆情"], obtained=["行情", "财务"],
                       failed_sources=["D5 研报与舆情"], sample_complete=False)
    assert f["shrink"] is True and f["forbid_population_pct"] is True
    print("[ok] F 缺 2 项 + 1 源失败 → shrink=True、forbid_population_pct=True，须说明缺项影响")

    # ── G · 报告生成成功但校验未完成 → 不得标「研究已验证完成」 ──
    g = resolve_status(generated=True, checked=False)
    assert g["status"] == "已生成未校验" and g["can_claim_verified"] is False
    g2 = resolve_status(generated=True, checked=True, all_critical_verified=False)
    assert g2["status"] == "部分校验" and g2["can_claim_verified"] is False
    g3 = resolve_status(generated=True, checked=True, all_critical_verified=True)
    assert g3["status"] == "已校验" and g3["can_claim_verified"] is True
    print("[ok] G 生成成功+未校验 → `已生成未校验`（can_claim_verified=False）；全核实 → `已校验`")

    # ── H · 证据充分 → 保留明确结论（反过度模糊） ──
    idx = {"E1": make_evidence("E1", "营收同比", independently_verified=True),
           "E2": make_evidence("E2", "毛利率", independently_verified=True),
           "E3": make_evidence("E3", "机构预期", source="深挖")}
    cr = bind_claims([{"id": "C1", "text": "基本面稳健", "critical_evidence": ["E1", "E2"]},
                      {"id": "C2", "text": "市场预期乐观", "critical_evidence": ["E3"]}], idx)
    assert cr["claims"][0]["action"] == "keep" and cr["claims"][1]["action"] == "downgrade"
    pol = hedge_policy(cr)
    assert pol["keep_decisive"] == ["C1"] and pol["blanket_hedging"] is False
    print("[ok] H 关键证据 verified → action=keep 保留明确结论；仅 unverified 那条降级（不一律模糊化）")

    # ── 附加：覆盖率二分 / 统计口径 / 陈述分类 / 可比性 ──
    cov = coverage_split(total=20, checked=20, independently_verified=6)
    assert cov["checked_rate"] == 100.0 and cov["verified_rate"] == 30.0
    print(f"[ok] 覆盖率二分：已检查 {cov['checked_rate']}% ≠ 已独立验证 {cov['verified_rate']}%（不得混用）")

    st = check_statistic({"text": "62% 机构看多", "sample_size": None, "denominator": None,
                          "dedup": None, "time_window": None})
    assert st["reportable"] is False and len(st["missing"]) == 4
    st2 = check_statistic({"text": "62% 机构看多", "sample_size": 12, "denominator": 20,
                           "numerator": 12, "pct": 60.0, "dedup": "按机构去重",
                           "time_window": "2026-09-01~2026-09-30", "attribution": "某券商 2026-09-28"})
    assert st2["reportable"] is True and st2["own_statistic"] is False
    assert "pct_conflict" not in st2 and st2["recomputed_pct"] == 60.0
    st3 = check_statistic({"text": "62% 机构看多", "sample_size": 12, "denominator": 20,
                           "numerator": 12, "pct": 62.0, "dedup": "按机构去重",
                           "time_window": "2026-09-01~2026-09-30"})
    assert st3["pct_conflict"] == {"reported": 62.0, "recomputed": 60.0}, st3
    print("[ok] 统计口径：缺四项不可报告；有归属必须保留归属（不得改写成本包自行统计）；"
          f"分子分母与所报比例不符 → 标冲突（62.0 vs 复算 60.0）")

    use = classify_statement("若毛利率下滑 3pct 则…", kind="scenario_assumption")
    assert use["must_label"] is True and "事实底座" not in use["allowed_sections"]
    mys = check_statement_misuse({"text": "若毛利率下滑 3pct", "kind": "scenario_assumption"}, "事实底座")
    assert mys["misuse"] is True
    uh = classify_statement("我认为明年会翻倍", kind="user_hypothesis")
    assert "待检验命题" in uh["note"] and uh["usable_as_fact"] is False
    print("[ok] 陈述分类：情景假设写进「事实底座」→ misuse=True；用户观点 → 待检验命题")

    pc = check_pair_comparability({"object": "宁德时代", "window": "近5日", "baseline": "沪深300", "metric": "涨幅"},
                                  {"object": "宁德时代", "window": "近20日", "baseline": "沪深300", "metric": "涨幅"})
    assert pc["comparable"] is False and "window" in pc["diffs"]
    print("[ok] 比较前置：时间窗口不同 → comparable=False，须先统一或并列说明")

    assert "归档保留全文 + 正文禁用" in precedence_note()["conflict_resolution"]
    print("[ok] 规则优先关系：归档层与正文层**作用对象不同** → 冲突判为「归档保留全文 + 正文禁用」")

    # ── I · 背景材料登记：追踪字段齐备、未知项显式标记、性质沿用既有分类（第 4 轮） ──
    m1 = make_material(
        "M1", "2026Q2 营收同比 +18.4%", object_id="某公司", question_ids=["某公司-Q1"],
        source_name="合成材料·离线", origin_ref="SYNTHETIC#income-q2",
        material_asof="2026Q2", published_at="2026-08-28", usable_from="2026-08-28",
        fetched_at="2026-10-03", metric_meaning="营业收入同比增速", unit="%",
        calibre="合并报表·累计同比", value=18.4, claim_kind="primary_fact",
        evidence_attrs={"independently_verified": True})
    assert m1["evidence_state"] == "verified", m1
    assert all(f in m1 for f in MATERIAL_FIELDS), MATERIAL_FIELDS
    assert m1["unknown_fields"] == [], m1["unknown_fields"]
    m2 = make_material("M2", "机构认为明年翻倍", object_id="某公司",
                       claim_kind="external_judgement")
    assert "source_name" in m2["unknown_fields"] and m2["material_asof"] == "unknown"
    assert material_statement_kind(m2) == "inference", m2
    assert summary_admissibility(m2["evidence"], "confirmed_fact")["allowed"] is False
    m3 = make_material("M3", "我认为该股会翻倍", object_id="某公司",
                       claim_kind="user_hypothesis", evidence_attrs={"pending": True})
    assert material_statement_kind(m3) == "user_hypothesis"
    print(f"[ok] I 背景材料登记：9 类追踪字段齐备；时间未知**显式标 `unknown`**"
          f"（M2 未知项 {len(m2['unknown_fields'])} 个）；"
          f"外部判断 → inference、用户观点 → user_hypothesis（**沿用既有分类**）")

    # ── J · 来源关系三态：同源不算独立；未登记关系 → unknown（**不默认独立**） ──
    g = SourceGraph(
        materials=[{"material_id": "A"}, {"material_id": "B"},
                   {"material_id": "C"}, {"material_id": "D"}],
        relations=[{"a": "A", "b": "B", "relation": "same_underlying"},
                   {"a": "C", "b": "D", "relation": "independent"}])
    assert g.independence("A", "B")["state"] == "same_source"
    assert g.independence("C", "D")["state"] == "independent"
    assert g.independence("A", "C")["state"] == "unknown"
    assert g.independence("A", "C")["counts_as_independent"] is False
    g.relate("B", "E", "derived_from")
    assert g.independence("A", "E")["state"] == "same_source"
    pe = g.per_evidence_independence("A")
    assert "B" in pe["same_source_with"] and "C" not in pe["same_source_with"], pe
    print("[ok] J 来源关系三态：底层同源 / 派生链 → same_source（**不算独立**）；"
          "显式 independent → 可互为验证；未登记 → unknown（**不默认独立、不武断同源**）；"
          "判定**按具体证据**，不整份报告一刀切")

    # ── K · 背景复述 ≠ 新增独立证据；「两路独立验证一致」的准入 ──
    sent = [{"material_id": "M1", "value": 18.4, "unit": "%", "statement": "营收同比 +18.4%"}]
    e1 = echo_check({"material_id": "RPT-E1", "value": 18.4, "unit": "%",
                     "statement": "公司营收同比增长 18.4%"}, sent)
    assert e1["is_echo"] and e1["counts_as_new_evidence"] is False, e1
    e2 = echo_check({"material_id": "RPT-E2", "value": 7.2, "unit": "%",
                     "statement": "毛利率 7.2%"}, sent)
    assert e2["is_echo"] is False and e2["counts_as_new_evidence"] is True, e2
    assert can_claim_two_way_independent("M1", "RPT-E1", a_is_echo=False,
                                         b_is_echo=True)["allowed"] is False
    assert can_claim_two_way_independent("C", "D", graph=g)["allowed"] is True
    assert can_claim_two_way_independent("A", "C", graph=g)["allowed"] is False
    print("[ok] K 背景复述：复述已传入数字 → **不算新增独立证据**；"
          "「两路独立验证一致」仅在**独立性已确认且均非复述**时允许")

    # ── L · 交付自检：把同源 / 复述当独立验证 → 拦下 ──
    ck_bad = check_independence_claims(
        [{"id": "C1", "evidence": ["A", "B"], "claims_two_way_independent": True}],
        graph=SourceGraph(materials=[{"material_id": "A"}, {"material_id": "B"}],
                          relations=[{"a": "A", "b": "B", "relation": "quote"}]),
        echoes={})
    assert ck_bad["ok"] is False and ck_bad["n_issues"] == 1, ck_bad
    ck_ok = check_independence_claims([{"id": "C2", "evidence": ["X", "Y"]}],
                                      graph=SourceGraph(), echoes={})
    assert ck_ok["ok"] is True and ck_ok["rows"][0]["same_source_pairs"] == []
    print("[ok] L 交付自检：声称「两路独立验证」但证据同源 → 报错拦下（n_issues=1）")

    print(f"\nselftest: {'全部通过' if ok else '有失败'}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="证据采信 · 错误原文处理 · 资料不足交付")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="内置真实场景自检")
    sub.add_parser("rules", help="打印规则优先关系")

    s = sub.add_parser("state", help="给一条证据分级")
    s.add_argument("--text", required=True)
    s.add_argument("--source")
    s.add_argument("--verified", action="store_true")
    s.add_argument("--conflict", action="store_true")
    s.add_argument("--pending", action="store_true")
    s.add_argument("--missing", action="store_true")

    a = sub.add_parser("archive", help="归档 vs 正文（错误原文处理）")
    a.add_argument("report", help="报告 JSON 文件")

    sl = sub.add_parser("support-level", help="乖离率 vs 价格前低")
    sl.add_argument("--bias", help="乖离率序列 JSON")
    sl.add_argument("--price-low", dest="price_low", help="价格前低序列 JSON")
    sl.add_argument("--claimed-support")

    ra = sub.add_parser("rel-abs", help="相对表现 vs 绝对表现")
    ra.add_argument("--relative", required=True, choices=list(RELATIVE_ABS))
    ra.add_argument("--absolute", required=True, choices=list(ABSOLUTE_ABS))

    co = sub.add_parser("coverage", help="覆盖率二分（已检查 vs 已独立验证）")
    co.add_argument("--total", type=int, required=True)
    co.add_argument("--checked", type=int, required=True)
    co.add_argument("--verified", type=int, required=True)

    de = sub.add_parser("delivery", help="交付范围收缩判定")
    de.add_argument("request", help="请求 JSON 文件")

    ck = sub.add_parser("checks", help="执行检查清单（每项都调生产函数）")
    ck.add_argument("spec", help="检查清单 JSON 文件")

    asm = sub.add_parser("assemble", help="端到端：输入材料 → 交付物 + 一致性自检")
    asm.add_argument("case", help="案例输入材料 JSON 文件")

    idp = sub.add_parser("independence", help="两处证据的独立性三态（来源关系）")
    idp.add_argument("--graph", help="关系图 JSON（materials / relations）")
    idp.add_argument("--a", required=True)
    idp.add_argument("--b", required=True)

    ec = sub.add_parser("echo", help="背景复述检测（复述 ≠ 新增独立证据）")
    ec.add_argument("--returned", required=True, help="返回证据 JSON")
    ec.add_argument("--sent", required=True, help="已发送背景 JSON（数组）")

    ns = ap.parse_args(argv)
    if ns.cmd == "selftest":
        return selftest()
    if ns.cmd == "rules":
        _p(precedence_note())
        return 0
    if ns.cmd == "state":
        ev = make_evidence("E", ns.text, source=ns.source,
                           independently_verified=ns.verified, conflicting=ns.conflict,
                           pending=ns.pending, missing=ns.missing)
        _p({"state": evidence_state(ev), "is_fact_grade": is_fact_grade(ev),
            "citation": citation_note(ev),
            "as_confirmed_fact": summary_admissibility(ev, "confirmed_fact")})
        return 0
    if ns.cmd == "archive":
        rep = json.loads(open(ns.report, encoding="utf-8").read())
        _p(build_body(archive_report(rep)))
        return 0
    if ns.cmd == "support-level":
        b = json.loads(open(ns.bias, encoding="utf-8").read()) if ns.bias else None
        p = json.loads(open(ns.price_low, encoding="utf-8").read()) if ns.price_low else None
        _p(check_support_level_claim(bias_series=b, price_low_data=p,
                                     claimed_support=ns.claimed_support))
        return 0
    if ns.cmd == "rel-abs":
        _p(check_relative_absolute(relative=ns.relative, absolute=ns.absolute))
        return 0
    if ns.cmd == "coverage":
        _p(coverage_split(total=ns.total, checked=ns.checked, independently_verified=ns.verified))
        return 0
    if ns.cmd == "delivery":
        _p(delivery_scope(**json.loads(open(ns.request, encoding="utf-8").read())))
        return 0
    if ns.cmd == "checks":
        import os
        spec = json.loads(open(ns.spec, encoding="utf-8").read())
        res = run_check_spec(spec, base_dir=os.path.dirname(os.path.abspath(ns.spec)))
        for r in res["results"]:
            print(f"[{'ok' if r['pass'] else 'FAIL'}] {r.get('label') or r['type']}：{r['detail']}")
        print(f"\n{res['n_checks']} 项，失败 {res['n_fail']}")
        return 0 if res["pass"] else 1
    if ns.cmd == "assemble":
        d = assemble_delivery(json.loads(open(ns.case, encoding="utf-8").read()))
        _p(d)
        return 0 if d["consistency"]["ok"] else 1
    if ns.cmd == "independence":
        spec = json.loads(open(ns.graph, encoding="utf-8").read()) if ns.graph else {}
        g = SourceGraph(materials=spec.get("materials"), relations=spec.get("relations"))
        _p({"independence": g.independence(ns.a, ns.b),
            "per_evidence": g.per_evidence_independence(ns.a),
            "summary": g.summary()})
        return 0
    if ns.cmd == "echo":
        r = json.loads(open(ns.returned, encoding="utf-8").read())
        b = json.loads(open(ns.sent, encoding="utf-8").read())
        _p(echo_check(r, b))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

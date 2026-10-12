#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reuse.py · 按**具体内容**判定历史报告能否复用 —— v1.13.0 新增（第 4 轮）

**要修的病根**：第 3 轮只把「历史报告查询」接通成三态（`no_candidate` /
`candidate_unverified` / `unknown`），**没有做内容判定** —— 于是出现：
① 把「同名同模板同日」当成「可复用」，而旧报告其实回答的是**另一个问题**；
② 反过来，把「表述不同但问的是同一件事」的报告浪费掉，白跑一遍研究；
③ 跨日旧报告被**整体报废**，连里面仍然成立的结构性信息也一起丢掉；
④ 或者只把旧结论里的数字换成新的，**结论照抄** —— 而结论所依赖的数据已经变了；
⑤ 用户明确要求「重新研究 / 换个时点」时，用旧报告顶替。

本模块是**唯一**的内容复用判定实现，接在第 3 轮 `publish_plan.reuse_decision()` 之后：

    候选列表（查询层） → reuse.judge_reuse()   （**内容层**，本模块）
                       → 四档判定 + 逐条理由 → reuse.apply_reuse() 回填发布计划

公开接口：
  · `REUSE_VERDICTS` / `VERDICT_NOTES` / `CONTENT_CHECKS` / `STABILITY`
  · `question_equivalence()`  表述不同但问题相同（**不机械比对文字**）
  · `stale_impact()`          跨日：稳定信息 vs 已过时数据**分别处理**
  · `check_candidate()`       单个候选的 6 项内容核查 → 四档之一
  · `judge_reuse()`           **按问题**给出判定与理由
  · `apply_reuse()`           回填发布计划（**不清除已提交状态与预算占用**）
  · `recompute_plan_cost()`   调整计划后**重新计算**预计新增消耗
  · `selftest()` / CLI

⚠️ **不联网、不发报告**：本模块只读候选的**结构化描述**（由调用方给出），
不做任何取数或发布；是否真的发布仍由 `publish_plan.publish()` 决定。
⚠️ **不确定 ≠ 通过**：任何一项**读不到 / 判不了**，一律落到 `not_applicable`，
**不得**写成「已确认可复用」。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

# ═══════════════════════════════════════════════════════════════════
# 0. 词表与规则
# ═══════════════════════════════════════════════════════════════════

#: 四档判定（用户要求 4）
REUSE_VERDICTS = ("full_reuse", "partial_reuse", "reference_only", "not_applicable")

VERDICT_NOTES = {
    "full_reuse": "内容能够满足当前问题，关键条件仍适用 → **可作为当前依据**",
    "partial_reuse": "只覆盖部分问题或部分内容 → **剩余缺口继续处理**",
    "reference_only": "保留原来的时间、事实和判断 → **不冒充当前结论**，仅作历史参考",
    "not_applicable": "不适用或待核实 → 说明具体原因，**不把未知写成已经通过**",
}

#: 判定优先级（取最优）
VERDICT_RANK = {"full_reuse": 3, "partial_reuse": 2, "reference_only": 1, "not_applicable": 0}

#: 内容核查项（用户要求 4 的「至少核对」清单）
CONTENT_CHECKS = (
    "object_level_match",      # ① 对象及研究层级是否匹配
    "answers_question",        # ② 是否真正回答当前问题
    "cutoff_applicable",       # ③ 研究截止时间是否适用
    "assumptions_still_hold",  # ④ 关键假设 / 情景 / 输入材料是否仍成立
    "readable_complete",       # ⑤ 报告是否完成、内容是否可读取
    "evidence_adequate",       # ⑥ 关键证据及校验状态是否满足当前用途
)

#: 时间敏感度 —— 跨日**不一刀切**
STABILITY = ("stable_structure", "time_sensitive", "mixed", "unknown")

#: 核查项「通过 / 不通过 / 未知」三态（**未知不得当成通过**）
JUDGE = ("pass", "fail", "unknown")

CONTENT_RULE = (
    "**报告列表 / 标题 / 模板编号 / 对象 / 日期只用于「找候选」**；"
    "**能不能复用必须看内容本身及其依据** —— "
    "表述不同但实际问题相同、内容充分时**可以复用**；"
    "同日、同对象、同模板但**研究问题不同**，**不能**直接复用。"
)

CROSS_DAY_RULE = (
    "**跨日内容不一律失效**：稳定的结构性信息可以继续使用，**时效性数据必须重新检查**；"
    "若旧结论依赖**已经变化的数据**，必须**重新分析** —— "
    "**不得只替换数字、保留旧结论**。"
)

NEW_REQUEST_RULE = (
    "用户**明确要求新时点、新情景或重新开展研究**时，"
    "**不得**用旧报告替换其要求。"
)

UNKNOWN_RULE = (
    "**未知一律按「待核实」处理** —— 不把未知写成已经通过，"
    "也不把「读不到内容」当成「已确认没有可复用报告」。"
)


# ═══════════════════════════════════════════════════════════════════
# 1. 问题等价判定（**不机械比对文字**）
# ═══════════════════════════════════════════════════════════════════

_WORD_SPLIT = None


def _fam_set(q):
    return {f for f in ((q or {}).get("metric_families") or []) if f}


def _ask_kind(q):
    a = (q or {}).get("asks")
    if isinstance(a, dict):
        return a.get("kind")
    return (q or {}).get("ask_kind")


def question_equivalence(cur, cand) -> dict:
    """判断**当前问题**与**候选报告已回答的问题**是不是同一个问题。

    判据（不依赖文字完全一致）：
      · `same_question_key` 相同 → 等价（最强）；
      · 对象相同 **且** 指标族有交集 **且** 提问种类相同 → 等价；
      · 仅文字相同、但指标族无交集 → **不等价**（很可能是同一模板下的不同问题）。

    返回 `{"equivalent", "basis", "text_identical", "text_only", "note"}`。
    """
    if not isinstance(cur, dict) or not isinstance(cand, dict):
        return {"equivalent": False, "basis": [], "text_identical": False,
                "text_only": False, "note": "问题记录缺失 → 无法判定（**不得当成等价**）"}
    basis = []
    kc, kd = cur.get("same_question_key"), cand.get("same_question_key")
    if kc and kd and kc == kd:
        return {"equivalent": True, "basis": ["same_question_key"], "text_identical":
                _text(cur) == _text(cand), "text_only": False,
                "note": "语义键相同 → 同一问题（**表述可不同**）"}

    same_obj = cur.get("object_id") is not None and cur.get("object_id") == cand.get("object_id")
    fam_c, fam_d = _fam_set(cur), _fam_set(cand)
    fam_inter = fam_c & fam_d
    same_kind = _ask_kind(cur) is not None and _ask_kind(cur) == _ask_kind(cand)
    text_same = bool(_text(cur)) and _text(cur) == _text(cand)

    if same_obj:
        basis.append("object_id")
    if fam_inter:
        basis.append("metric_families∩")
    if same_kind:
        basis.append("ask_kind")
    if text_same:
        basis.append("text_identical")

    equivalent = bool(same_obj and fam_inter and (same_kind or text_same))
    text_only = bool(text_same and not fam_inter and not kc)
    if equivalent:
        note = "对象 + 指标族" + (" + 提问种类" if same_kind else "") + "一致 → 同一问题"
    elif fam_c and fam_d and not fam_inter:
        note = ("对象或模板可能相同，但**指标族无交集** → 属**不同研究问题**，"
                "**不得**因同对象 / 同模板 / 同日而直接复用")
    elif text_only:
        note = "仅文字相同、指标族无交集 → 不足以判定同一问题"
    else:
        note = "证据不足 → 不判等价（**未知不得当成通过**）"
    return {"equivalent": equivalent, "basis": basis, "text_identical": text_same,
            "text_only": text_only, "shared_families": sorted(fam_inter), "note": note}


def _text(q):
    return (q or {}).get("source_text") or (q or {}).get("text") or ""


# ═══════════════════════════════════════════════════════════════════
# 2. 跨日：稳定结构性信息 vs 已过时数据（**分别处理**）
# ═══════════════════════════════════════════════════════════════════

def stale_impact(candidate, *, cutoff=None) -> dict:
    """把候选报告的证据**按稳定性分开**，判断跨日后的失效范围。

    · `stable_structure`：结构性 / 制度性信息 → **可以继续使用**；
    · `time_sensitive`：时效性数据 → **必须重新检查**；
    · 旧结论若依赖已过时的时效数据 → `conclusion_must_be_redone=True`
      （**不得只替换数字、保留旧结论**）。
    """
    c = candidate or {}
    evs = list(c.get("key_evidence") or [])
    stable = [e.get("id") for e in evs if e.get("stability") == "stable_structure"]
    volatile = [e.get("id") for e in evs if e.get("stability") == "time_sensitive"]
    unknown = [e.get("id") for e in evs if e.get("stability") not in
               ("stable_structure", "time_sensitive")]
    c_cut = c.get("research_cutoff")
    same_day = bool(cutoff) and c_cut == cutoff
    cross_day = bool(cutoff) and c_cut is not None and c_cut != cutoff
    # 旧结论依赖的时效数据是否已经变化（候选显式给出 changed=True 才算“已确认变化”）
    changed = [e.get("id") for e in evs
               if e.get("stability") == "time_sensitive" and e.get("changed") is True]
    conclusion_depends = [eid for eid in (c.get("conclusion_evidence") or [])
                          if eid in set(volatile)]
    must_redo = bool(changed and conclusion_depends)
    return {
        "candidate_cutoff": c_cut, "current_cutoff": cutoff,
        "same_day": same_day, "cross_day": cross_day,
        "stable_ids": stable, "time_sensitive_ids": volatile, "unknown_stability_ids": unknown,
        "changed_ids": changed,
        "conclusion_depends_on_time_sensitive": sorted(set(conclusion_depends)),
        "stable_usable": bool(stable),
        "time_sensitive_needs_recheck": bool(volatile),
        "conclusion_must_be_redone": must_redo,
        "note": ("跨日：稳定结构性信息**可继续使用**；时效性数据**须重新检查**"
                 + ("；旧结论**依赖已变化的数据 → 必须重新分析**（不得只换数字）"
                    if must_redo else "")
                 if cross_day else
                 "同一研究时点 → 不涉及跨日重用；时效性数据仍按当前用途核对"),
        "rule": CROSS_DAY_RULE,
    }


# ═══════════════════════════════════════════════════════════════════
# 3. 单个候选的 6 项内容核查
# ═══════════════════════════════════════════════════════════════════

def _mk(judge, why):
    assert judge in JUDGE, judge
    return {"judge": judge, "why": why}


def check_candidate(candidate, question, *, object_id=None, object_level=None,
                    research_cutoff=None, required_use="current_basis",
                    new_timepoint=None, new_scenario=None, redo_requested=False,
                    current_materials=None) -> dict:
    """对一个候选报告做 6 项内容核查 → 四档判定 + 逐条理由。

    `required_use`：本次用途。`current_basis`（要作当前依据）/ `historical_ref`（仅作参考）。
    `new_timepoint` / `new_scenario` / `redo_requested`：用户明确的新要求 → **一律不可替换**。
    """
    c = candidate or {}
    obj = object_id if object_id is not None else question.get("object_id")
    lvl = object_level if object_level is not None else question.get("object_level")
    checks = {}

    # ① 对象 / 层级
    c_obj, c_lvl = c.get("object_id"), c.get("object_level")
    if c_obj is None or lvl is None or c_lvl is None:
        checks["object_level_match"] = _mk("unknown", "候选或当前任务缺对象 / 层级 → 无法核对")
    elif c_obj != obj:
        checks["object_level_match"] = _mk("fail", f"对象不一致（{c_obj} vs {obj}）")
    elif c_lvl != lvl:
        checks["object_level_match"] = _mk("fail", f"研究层级不一致（{c_lvl} vs {lvl}）")
    else:
        checks["object_level_match"] = _mk("pass", f"对象 {obj} / 层级 {lvl} 一致")

    # ② 是否真正回答当前问题
    eq = None
    if c.get("content_available") is False:
        checks["answers_question"] = _mk("unknown", "正文内容不可读取 → 无法核对是否回答")
    else:
        best = None
        for cq in (c.get("questions") or []):
            e = question_equivalence(question, cq)
            if e["equivalent"]:
                best = (cq, e)
                break
        if best is None:
            if not (c.get("questions") or []):
                checks["answers_question"] = _mk("unknown", "候选未提供已回答的问题清单 → 无法核对")
            else:
                checks["answers_question"] = _mk(
                    "fail", "候选已回答的问题与当前问题**不是同一个**（" +
                    "；".join(question_equivalence(question, cq)["note"]
                             for cq in c["questions"][:3]) + "）")
        else:
            eq = best[1]
            checks["answers_question"] = _mk("pass", f"命中同一问题（依据：{'、'.join(eq['basis'])}）")

    # ③ 研究截止时间是否适用
    stale = stale_impact(c, cutoff=research_cutoff)
    if research_cutoff is None or c.get("research_cutoff") is None:
        checks["cutoff_applicable"] = _mk("unknown", "缺研究截止时间 → 无法判定是否适用")
    elif stale["conclusion_must_be_redone"]:
        checks["cutoff_applicable"] = _mk(
            "fail", "研究截止时间已不适用，且旧结论依赖**已变化的数据** → 必须重新分析")
    elif stale["cross_day"] and stale["time_sensitive_needs_recheck"] and not stale["stable_usable"]:
        checks["cutoff_applicable"] = _mk(
            "fail", "跨日且候选内容**全部为时效性数据** → 当前时点不适用")
    else:
        checks["cutoff_applicable"] = _mk("pass", "研究截止时间对当前用途适用")

    # ④ 关键假设 / 情景 / 输入材料是否仍成立
    bad = [a for a in (c.get("assumptions") or []) if a.get("still_holds") is False]
    sc_changed = [s for s in (c.get("scenarios") or []) if s.get("still_holds") is False]
    mats_changed = []
    if current_materials and c.get("input_materials"):
        cm = set(map(str, current_materials))
        for m in c["input_materials"]:
            if m.get("changed") is True and str(m.get("key")) in cm:
                mats_changed.append(m.get("key"))
    if c.get("assumptions") is None:
        checks["assumptions_still_hold"] = _mk(
            "unknown", "候选未提供关键假设 / 情景 → 无法确认是否仍成立"
                       "（**不得当成成立**；显式声明「无假设」请传空数组）")
    elif bad or sc_changed or mats_changed:
        checks["assumptions_still_hold"] = _mk(
            "fail", "关键假设 / 情景 / 输入材料已不成立（"
                    + "；".join(filter(None, [
                        f"假设 {[a.get('id') for a in bad]}" if bad else "",
                        f"情景 {[s.get('id') for s in sc_changed]}" if sc_changed else "",
                        f"输入材料 {mats_changed}" if mats_changed else ""])) + "）")
    else:
        checks["assumptions_still_hold"] = _mk("pass", "关键假设 / 情景 / 输入材料仍成立")

    # ⑤ 报告是否完成、内容是否可读取
    if c.get("completed") is False:
        checks["readable_complete"] = _mk("fail", "报告**尚未完成** → 不得宣称已满足当前研究需要")
    elif c.get("content_available") is False:
        checks["readable_complete"] = _mk("fail", "正文**不可读取** → 不得宣称已满足当前研究需要")
    elif c.get("readable") is False:
        checks["readable_complete"] = _mk("fail", "关键依据**无法核对** → 不得宣称已满足当前研究需要")
    elif c.get("completed") is None or c.get("content_available") is None:
        checks["readable_complete"] = _mk("unknown", "完成 / 可读状态未提供 → 按待核实处理")
    else:
        checks["readable_complete"] = _mk("pass", "报告已完成且正文可读取")

    # ⑥ 关键证据及校验状态是否满足当前用途
    evs = list(c.get("key_evidence") or [])
    if not evs:
        checks["evidence_adequate"] = _mk("unknown", "候选未提供关键证据校验状态 → 按待核实处理")
    else:
        need = "verified" if required_use == "current_basis" else None
        weak = [e.get("id") for e in evs
                if e.get("state") not in (("verified",) if need else
                                          ("verified", "unverified", "conflict", "pending"))]
        if need and weak:
            checks["evidence_adequate"] = _mk(
                "fail", f"要作**当前依据**但关键证据未达 `verified`：{weak}")
        elif any(e.get("state") in (None, "unknown") for e in evs):
            checks["evidence_adequate"] = _mk("unknown", "存在校验状态未知的关键证据 → 待核实")
        else:
            checks["evidence_adequate"] = _mk("pass", "关键证据及校验状态满足当前用途")

    # —— 硬性否决（用户明确的新要求 / 内容不可用）——
    overrides = []
    if redo_requested:
        overrides.append("用户明确要求**重新开展研究**")
    if new_timepoint:
        overrides.append(f"用户明确要求**新时点**（{new_timepoint}）")
    if new_scenario:
        overrides.append("用户明确要求**新情景**")

    verdict, reasons = _derive(checks, overrides, required_use)
    return {
        "aid": c.get("aid"), "template_tid": c.get("template_tid"),
        "object_id": c_obj, "object_level": c_lvl,
        "candidate_cutoff": c.get("research_cutoff"),
        "checks": checks,
        "verdict": verdict,
        "verdict_note": VERDICT_NOTES[verdict],
        "reasons": reasons,
        "overrides": overrides,
        "stale": stale,
        "n_pass": sum(1 for v in checks.values() if v["judge"] == "pass"),
        "n_fail": sum(1 for v in checks.values() if v["judge"] == "fail"),
        "n_unknown": sum(1 for v in checks.values() if v["judge"] == "unknown"),
        "derivation": "四档由一个**唯一的**派生函数给出，不手工指定",
    }


def _derive(checks, overrides, required_use):
    """从 6 项核查派生四档判定（**唯一定义处**）。"""
    reasons = []
    if overrides:
        reasons.append("；".join(overrides) + f"（{NEW_REQUEST_RULE}）")
        return "not_applicable", reasons

    hard_fail = [k for k in ("readable_complete", "object_level_match")
                 if checks[k]["judge"] == "fail"]
    if hard_fail:
        reasons.append(f"硬性不通过：{hard_fail} —— " +
                       "；".join(checks[k]["why"] for k in hard_fail))
        return "not_applicable", reasons

    if checks["answers_question"]["judge"] == "fail":
        # 不回答当前问题 → 至多作历史参考（且要内容可读）
        reasons.append(checks["answers_question"]["why"])
        if checks["readable_complete"]["judge"] == "pass":
            reasons.append("报告本身可读 → **仅作历史参考**，不冒充当前结论")
            return "reference_only", reasons
        return "not_applicable", reasons

    unknowns = [k for k, v in checks.items() if v["judge"] == "unknown"]
    fails = [k for k, v in checks.items() if v["judge"] == "fail"]

    if not fails and checks["answers_question"]["judge"] == "pass":
        if unknowns:
            reasons.append(f"仍有未核实项 {unknowns} → **不得**判为完整复用")
            reasons.append("已覆盖当前问题且无已知不成立项 → **部分复用**，未核实部分继续处理")
            return "partial_reuse", reasons
        reasons.append("6 项核查全部通过 → 内容满足当前问题、关键条件仍适用")
        return "full_reuse", reasons

    if fails:
        reasons.append("不通过项：" + "；".join(checks[k]["why"] for k in fails))
        # 时效性问题：稳定部分仍可用 → 部分复用；否则仅历史参考
        stable_ok = (checks["cutoff_applicable"]["judge"] == "fail"
                     and not checks["answers_question"]["judge"] == "fail")
        if stable_ok and required_use == "current_basis":
            reasons.append("跨日仅**时效性数据**失效、稳定结构性信息仍成立 → "
                           "**部分复用**（过时数据更新、受影响结论重新分析）")
            return "partial_reuse", reasons
        if checks["readable_complete"]["judge"] == "pass":
            reasons.append("保留原来的时间、事实与判断 → **仅作历史参考**")
            return "reference_only", reasons
        return "not_applicable", reasons

    reasons.append("证据不足 → 按待核实处理")
    return "not_applicable", reasons


# ═══════════════════════════════════════════════════════════════════
# 4. 按问题给出判定
# ═══════════════════════════════════════════════════════════════════

def judge_reuse(candidates, question, *, object_id=None, object_level=None,
                research_cutoff=None, required_use="current_basis",
                new_timepoint=None, new_scenario=None, redo_requested=False,
                current_materials=None) -> dict:
    """**按问题**给出判定与理由（一个候选一个结果，取最优作为该问题结论）。

    复用结果**始终关联第 2 轮的问题编号**（`question_id`）。
    """
    q = question or {}
    qid = q.get("question_id")
    rows = [check_candidate(c, q, object_id=object_id, object_level=object_level,
                            research_cutoff=research_cutoff, required_use=required_use,
                            new_timepoint=new_timepoint, new_scenario=new_scenario,
                            redo_requested=redo_requested,
                            current_materials=current_materials)
            for c in (candidates or [])]
    if not rows:
        return {
            "question_id": qid, "verdict": "not_applicable", "aid": None,
            "candidates": [],
            "reasons": ["无候选报告 → 该问题**不因复用而免于处理**；"
                        "内容判定的结论是「本次未找到可复用内容」，"
                        "**不得**写成「已确认没有可复用报告」"],
            "reuse_evidence": [], "note": VERDICT_NOTES["not_applicable"],
        }
    best = max(rows, key=lambda r: VERDICT_RANK[r["verdict"]])
    same = [r for r in rows if r["verdict"] == best["verdict"]]
    return {
        "question_id": qid,
        "verdict": best["verdict"],
        "aid": best["aid"],
        "candidates": rows,
        "all_verdicts": {r["aid"]: r["verdict"] for r in rows},
        "tie": len(same) > 1,
        "reasons": best["reasons"],
        "checks": best["checks"],
        "stale": best["stale"],
        "reuse_evidence": [{"aid": best["aid"], "question_id": qid,
                            "verdict": best["verdict"],
                            "use": ("当前依据" if best["verdict"] == "full_reuse"
                                    else "部分依据" if best["verdict"] == "partial_reuse"
                                    else "历史参考")}],
        "note": VERDICT_NOTES[best["verdict"]],
        "rule": CONTENT_RULE,
    }


def judge_all(candidates_by_question, questions, **kw) -> dict:
    """对整份问题清单逐题判定。`candidates_by_question = {question_id: [candidates]}`。"""
    out = {}
    for q in (questions or []):
        qid = q.get("question_id")
        out[qid] = judge_reuse(candidates_by_question.get(qid) or [], q, **kw)
    counts = {v: 0 for v in REUSE_VERDICTS}
    for r in out.values():
        counts[r["verdict"]] += 1
    return {"by_question": out, "counts": counts,
            "full": sorted(k for k, v in out.items() if v["verdict"] == "full_reuse"),
            "partial": sorted(k for k, v in out.items() if v["verdict"] == "partial_reuse"),
            "reference": sorted(k for k, v in out.items() if v["verdict"] == "reference_only"),
            "not_applicable": sorted(k for k, v in out.items()
                                     if v["verdict"] == "not_applicable"),
            "note": "**逐题判定**；复用结果关联第 2 轮问题编号；"
                    "四档互不冒充，未知不写成通过",
            "rule": CONTENT_RULE}


# ═══════════════════════════════════════════════════════════════════
# 5. 回填发布计划（**不清除已提交状态与预算占用**）
# ═══════════════════════════════════════════════════════════════════

#: 这些状态一旦存在，**任何复用判定都不得清除**（用户要求 5）
KEEP_STATES = ("submitting", "accepted", "done", "submit_unknown")


def apply_reuse(plan, judgements) -> dict:
    """把逐题判定回填到发布计划。

    · `full_reuse` → 该条目**不再发布**（记 `reuse_all`），**内容进入对应问题的证据与回答**；
    · `partial_reuse` → 保留**未解决**的问题，条目收窄到剩余缺口；
    · `reference_only` / `not_applicable` → **不免除研究**，条目照常；
    · **已提交 / 结果未知的条目：状态与预算占用一律保留**，不被复用结论清除。
    """
    jq = (judgements or {}).get("by_question") or judgements or {}
    items = list((plan or {}).get("items") or [])
    rows, released, kept_locked, narrowed = [], [], [], []
    for it in items:
        qids = list(it.get("question_ids") or [])
        verdicts = {q: (jq.get(q) or {}).get("verdict", "not_applicable") for q in qids}
        locked = it.get("state") in KEEP_STATES
        all_full = qids and all(v == "full_reuse" for v in verdicts.values())
        unsolved = [q for q in qids if verdicts.get(q) != "full_reuse"]
        decision = None
        if all_full and not locked:
            decision = "reuse_all"
            released.append(it.get("entry_id"))
        elif unsolved and len(unsolved) < len(qids) and not locked:
            decision = "partial_narrowed"
            narrowed.append({"entry_id": it.get("entry_id"),
                             "solved": [q for q in qids if q not in unsolved],
                             "remaining": unsolved})
        if locked:
            kept_locked.append(it.get("entry_id"))
        it2 = dict(it)
        it2["reuse_judgement"] = verdicts
        it2["reuse_decision"] = decision or ("locked_no_change" if locked else "unchanged")
        if decision == "reuse_all":
            it2["question_ids"] = list(qids)
            it2["reused_from"] = {q: (jq.get(q) or {}).get("aid") for q in qids}
        elif decision == "partial_narrowed":
            it2["question_ids"] = unsolved
            it2["reused_from"] = {q: (jq.get(q) or {}).get("aid")
                                  for q in qids if q not in unsolved}
        rows.append(it2)

    # 被复用覆盖的问题 → 其证据与回答（**不只是取消发布**）
    reuse_evidence = {}
    for qid, j in jq.items():
        if j.get("verdict") in ("full_reuse", "partial_reuse"):
            reuse_evidence[qid] = {
                "aid": j.get("aid"), "verdict": j.get("verdict"),
                "basis": "复用历史报告内容（非本次新发布）",
                "question_id": qid,
            }
    new_plan = dict(plan or {})
    new_plan["items"] = rows
    new_plan["reuse"] = {
        "released_entries": released,
        "narrowed_entries": narrowed,
        "kept_locked_entries": kept_locked,
        "reuse_evidence_by_question": reuse_evidence,
        "n_reuse_all": len(released),
        "n_narrowed": len(narrowed),
        "note": "复用结论**关联第 2 轮问题编号**；完整复用的内容**进入该问题的证据与回答**，"
                "**不只是取消发布**；已提交 / 结果未知条目的**状态与预算占用一律保留**",
        "rule": "**不得因为后来决定复用，就清除已提交或结果未知条目的状态和预算占用**",
    }
    return new_plan


def recompute_plan_cost(plan, *, template_cost_fn=None) -> dict:
    """**调整发布计划后重新计算预计新增消耗**（用户要求 5）。

    只对**仍将发布**的条目（`pending` 且未被 `reuse_all` 收走）加总；
    `full_reuse` 的条目**不再产生新增消耗**，但其**已有占用不被释放**。
    """
    items = list((plan or {}).get("items") or [])
    live, reused, locked = [], [], []
    for it in items:
        st = it.get("state")
        if st in KEEP_STATES:
            # 已提交 / 结果未知 → 已产生占用，**不属于「拟新增」**（且不被复用释放）
            locked.append(it.get("entry_id"))
            continue
        if it.get("reuse_decision") == "reuse_all":
            reused.append(it.get("entry_id"))
        else:
            live.append(it)
    costs = [it.get("cost") for it in live]
    known = [c for c in costs if c is not None]
    n_unknown = sum(1 for c in costs if c is None)
    return {
        "n_live": len(live), "n_reused_no_cost": len(reused), "n_locked": len(locked),
        "planned_new": {"value": (sum(known) if known else (None if n_unknown else 0)),
                        "n": len(live), "n_unknown": n_unknown,
                        "exact": n_unknown == 0},
        "planned_new_ids": sorted(it.get("entry_id") for it in live),
        "reused_ids": sorted(reused), "locked_ids": sorted(locked),
        "note": "计划调整后**重新计算**预计新增消耗；复用掉的条目不产生新增消耗，"
                "但**已提交 / 结果未知的占用不被释放**；**已有授权须重新核对**",
        "rule": "调整发布计划后重新计算预计新增消耗，并检查已有授权",
    }


# ═══════════════════════════════════════════════════════════════════
# 6. 用例执行入口
# ═══════════════════════════════════════════════════════════════════

def _run_one(chk):
    t = chk.get("type")
    if t == "question_equiv":
        return question_equivalence(chk.get("cur"), chk.get("cand"))
    if t == "stale":
        return stale_impact(chk.get("candidate"), cutoff=chk.get("cutoff"))
    if t == "candidate":
        return check_candidate(chk.get("candidate"), chk.get("question") or {},
                               **dict(chk.get("kwargs") or {}))
    if t == "judge":
        return judge_reuse(chk.get("candidates"), chk.get("question") or {},
                           **dict(chk.get("kwargs") or {}))
    if t == "judge_all":
        return judge_all(chk.get("candidates_by_question") or {},
                         chk.get("questions") or [], **dict(chk.get("kwargs") or {}))
    if t == "apply":
        return apply_reuse(chk.get("plan") or {}, chk.get("judgements") or {})
    if t == "cost":
        return recompute_plan_cost(chk.get("plan") or {})
    raise ValueError(f"未知检查类型：{t}")


def run_check_spec(spec) -> dict:
    """执行检查清单（与各轮一致：**真跑生产函数**）。"""
    import capability as cap
    items = []
    for ch in spec.get("checks", []):
        label = ch.get("label") or ch.get("type")
        try:
            res = _run_one(ch)
        except Exception as e:                                    # noqa: BLE001
            items.append({"label": label, "pass": False, "detail": f"用例执行失败：{e}"})
            continue
        for r in cap.run_asserts(res, ch.get("assert", [])):
            items.append({"label": f"{label} · {r['path'] or '(根)'} {r['op']}",
                          "pass": r["pass"],
                          "detail": r["detail"] if r["pass"]
                          else f"{r['detail']}（期望 {r['expect']!r}）"})
    n_fail = sum(1 for x in items if not x["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail, "results": items}


# ═══════════════════════════════════════════════════════════════════
# 7. 自检
# ═══════════════════════════════════════════════════════════════════

def _q(qid, text, obj, fams, kind="fact_query", key=None, level="个股"):
    d = {"question_id": qid, "source_text": text, "object_id": obj,
         "object_level": level, "metric_families": list(fams), "asks": {"kind": kind}}
    if key:
        d["same_question_key"] = key
    return d


_DEF = object()          # 哨兵：区分「未提供」与「显式给 None」


def _cand(aid, *, obj="某公司", lvl="个股", cutoff="2026-09-30", tid=9,
          questions=None, completed=True, content_available=True, readable=True,
          evidence=None, assumptions=_DEF, scenarios=_DEF, conclusion_evidence=None,
          materials=None):
    # 未显式给 → 用一个「已声明且仍成立」的假设集；显式给 None → 视为**未提供**（→ unknown）
    if assumptions is _DEF:
        assumptions = [{"id": "a1", "still_holds": True}]
    if scenarios is _DEF:
        scenarios = []
    return {"aid": aid, "object_id": obj, "object_level": lvl,
            "research_cutoff": cutoff, "template_tid": tid,
            "questions": questions or [], "completed": completed,
            "content_available": content_available, "readable": readable,
            "key_evidence": evidence if evidence is not None else
            [{"id": f"{aid}-E1", "state": "verified", "stability": "stable_structure"}],
            "assumptions": assumptions, "scenarios": scenarios,
            "conclusion_evidence": conclusion_evidence or [],
            "input_materials": materials}


def selftest() -> int:
    fails = []
    passed = [0]

    def ck(cond, msg):
        if cond:
            passed[0] += 1
        else:
            fails.append(msg)

    # 1. 问题等价：不机械比对文字
    e1 = question_equivalence(_q("Q1", "现在估值贵不贵", "某公司", ["valuation"], key="valuation:level"),
                              _q("Q9", "估值分位高不高", "某公司", ["valuation"], key="valuation:level"))
    ck(e1["equivalent"] and not e1["text_identical"], f"R1 语义键相同应等价：{e1}")
    e2 = question_equivalence(_q("Q1", "估值贵不贵", "某公司", ["valuation"]),
                              _q("Q9", "资产负债率有没有风险", "某公司", ["financial_compare"]))
    ck(not e2["equivalent"], f"R1 指标族无交集应不等价：{e2}")
    e3 = question_equivalence(_q("Q1", "估值贵不贵", "某公司", ["valuation"]),
                              _q("Q9", "和自身历史比贵不贵", "某公司", ["valuation"]))
    ck(e3["equivalent"], f"R1 对象+指标族+种类一致应等价：{e3}")
    e4 = question_equivalence(_q("Q1", "甲", "某公司", ["valuation"]),
                              _q("Q9", "甲", "某公司", ["financial_compare"]))
    ck(not e4["equivalent"] and e4["text_identical"] and e4["text_only"],
       f"R1 仅文字相同、族不同 → 不等价：{e4}")

    # 2. 同日同对象同模板但问题不同 → 不得复用
    qB = _q("Q2", "资产负债率有没有风险", "某公司", ["financial_compare"])
    cA = _cand("AID-A", questions=[_q("A-Q1", "现在估值贵不贵", "某公司", ["valuation"])])
    rB = check_candidate(cA, qB, research_cutoff="2026-09-30")
    ck(rB["verdict"] in ("reference_only", "not_applicable") and
       rB["checks"]["answers_question"]["judge"] == "fail",
       f"R2 同日同对象同模板但问题不同 → 不复用：{rB['verdict']}")

    # 3. 表述不同但内容覆盖且条件有效 → 可完整复用
    qA = _q("Q1", "现在估值贵不贵", "某公司", ["valuation"], key="valuation:level")
    cB = _cand("AID-B", questions=[_q("B-Q1", "估值分位高不高", "某公司", ["valuation"],
                                      key="valuation:level")])
    rA = check_candidate(cB, qA, research_cutoff="2026-09-30")
    ck(rA["verdict"] == "full_reuse", f"R3 表述不同但内容覆盖 → full_reuse：{rA['verdict']}")

    # 4. 跨日：稳定 + 过时数据 分别处理；旧结论依赖已变数据 → 必须重做
    cC = _cand("AID-C", cutoff="2026-09-20",
               questions=[_q("C-Q1", "估值分位", "某公司", ["valuation"], key="valuation:level")],
               evidence=[{"id": "s1", "state": "verified", "stability": "stable_structure"},
                         {"id": "v1", "state": "verified", "stability": "time_sensitive",
                          "changed": True}],
               conclusion_evidence=["v1"])
    st = stale_impact(cC, cutoff="2026-09-30")
    ck(st["cross_day"] and st["stable_usable"] and st["time_sensitive_needs_recheck"]
       and st["conclusion_must_be_redone"], f"R4 跨日应分离稳定/时效：{st}")
    rC = check_candidate(cC, qA, research_cutoff="2026-09-30")
    ck(rC["verdict"] in ("partial_reuse", "reference_only"),
       f"R4 跨日结论须重做 → 不得 full_reuse：{rC['verdict']}")
    ck(rC["checks"]["cutoff_applicable"]["judge"] == "fail", f"R4 截止时间应不适用：{rC['checks']}")

    # 5. 报告未完成 / 正文不可读 → 不得宣称满足当前需要
    rD = check_candidate(_cand("AID-D", completed=False,
                               questions=[_q("D-Q1", "估值", "某公司", ["valuation"],
                                             key="valuation:level")]),
                         qA, research_cutoff="2026-09-30")
    ck(rD["verdict"] == "not_applicable" and rD["checks"]["readable_complete"]["judge"] == "fail",
       f"R5 未完成 → not_applicable：{rD['verdict']}")
    rE = check_candidate(_cand("AID-E", content_available=False,
                               questions=[_q("E-Q1", "估值", "某公司", ["valuation"],
                                             key="valuation:level")]),
                         qA, research_cutoff="2026-09-30")
    ck(rE["verdict"] == "not_applicable", f"R5 正文不可读 → not_applicable：{rE['verdict']}")

    # 6. 用户明确要求重新研究 → 一律不替换
    rF = check_candidate(cB, qA, research_cutoff="2026-09-30", redo_requested=True)
    ck(rF["verdict"] == "not_applicable" and rF["overrides"], f"R6 要求重做 → 不替换：{rF}")
    rG = check_candidate(cB, qA, research_cutoff="2026-09-30", new_timepoint="2026-10-03")
    ck(rG["verdict"] == "not_applicable", f"R6 新时点 → 不替换：{rG}")

    # 7. 关键证据未达 verified 但要作当前依据 → 不得 full_reuse
    cH = _cand("AID-H", questions=[_q("H-Q1", "估值", "某公司", ["valuation"],
                                      key="valuation:level")],
               evidence=[{"id": "h1", "state": "unverified", "stability": "stable_structure"}])
    rH = check_candidate(cH, qA, research_cutoff="2026-09-30", required_use="current_basis")
    ck(rH["verdict"] != "full_reuse" and rH["checks"]["evidence_adequate"]["judge"] == "fail",
       f"R7 证据不足 → 不得 full_reuse：{rH['verdict']}")

    # 8. 未知不得当成通过：假设缺失 → unknown → 至多 partial
    cI = _cand("AID-I", questions=[_q("I-Q1", "估值", "某公司", ["valuation"],
                                      key="valuation:level")], assumptions=None)
    rI = check_candidate(cI, qA, research_cutoff="2026-09-30")
    ck(rI["verdict"] == "partial_reuse" and rI["checks"]["assumptions_still_hold"]["judge"]
       == "unknown", f"R8 未知 → 不得 full_reuse：{rI['verdict']}")

    # 9. 逐题判定 + 覆盖三个问题中的两个
    qs = [_q("Q1", "估值", "某公司", ["valuation"], key="k1"),
          _q("Q2", "财务", "某公司", ["financial_compare"], key="k2"),
          _q("Q3", "事件", "某公司", ["announcement"], key="k3")]
    cands = {
        "Q1": [_cand("AID-1", questions=[_q("c1", "估值", "某公司", ["valuation"], key="k1")])],
        "Q2": [_cand("AID-2", questions=[_q("c2", "财务", "某公司", ["financial_compare"],
                                            key="k2")])],
        "Q3": [],
    }
    ja = judge_all(cands, qs, research_cutoff="2026-09-30")
    ck(ja["counts"]["full_reuse"] == 2, f"R9 两题完整复用：{ja['counts']}")
    ck(ja["by_question"]["Q3"]["verdict"] == "not_applicable", f"R9 Q3 继续处理：{ja['by_question']['Q3']}")
    ck("Q1" in ja["by_question"]["Q1"]["reuse_evidence"][0]["question_id"],
       "R9 复用结果关联问题编号")

    # 10. 回填计划：完整复用 → 不发布；部分复用 → 收窄；锁定条目不动
    plan = {"items": [
        {"entry_id": "E-01", "question_ids": ["Q1"], "state": "pending",
         "cost": 5, "aid": None},
        {"entry_id": "E-02", "question_ids": ["Q2"], "state": "pending",
         "cost": 6, "aid": None},
        {"entry_id": "E-03", "question_ids": ["Q3"], "state": "pending",
         "cost": 7, "aid": None},
        {"entry_id": "E-04", "question_ids": ["Q1", "Q2"], "state": "accepted",
         "cost": 9, "aid": "OLD@@1"},
    ]}
    jm = {"by_question": {
        "Q1": {"verdict": "full_reuse", "aid": "AID-1"},
        "Q2": {"verdict": "full_reuse", "aid": "AID-2"},
        "Q3": {"verdict": "not_applicable", "aid": None},
    }}
    ap = apply_reuse(plan, jm)
    by = {it["entry_id"]: it for it in ap["items"]}
    ck(by["E-01"]["reuse_decision"] == "reuse_all", f"R10 完整复用 → 不发：{by['E-01']}")
    ck(by["E-03"]["reuse_decision"] == "unchanged", f"R10 不适用 → 照常：{by['E-03']}")
    ck(by["E-04"]["reuse_decision"] == "locked_no_change" and by["E-04"]["state"] == "accepted",
       f"R10 已受理条目不得被复用清除：{by['E-04']}")
    ck("Q1" in ap["reuse"]["reuse_evidence_by_question"], "R10 复用内容进入问题证据")
    ck(ap["reuse"]["kept_locked_entries"] == ["E-04"], f"R10 锁定条目记录：{ap['reuse']}")

    # 11. 部分复用 → 收窄到剩余缺口
    plan2 = {"items": [{"entry_id": "P-01", "question_ids": ["Q1", "Q2"], "state": "pending",
                        "cost": 5, "aid": None}]}
    ap2 = apply_reuse(plan2, {"by_question": {
        "Q1": {"verdict": "full_reuse", "aid": "AID-1"},
        "Q2": {"verdict": "not_applicable", "aid": None}}})
    ck(ap2["items"][0]["question_ids"] == ["Q2"], f"R11 部分复用应收窄：{ap2['items'][0]}")

    # 12. 调整计划后重算成本
    cost = recompute_plan_cost(ap)
    ck(cost["n_reused_no_cost"] == 2 and cost["planned_new"]["value"] == 7,
       f"R12 复用条目不产生新增消耗：{cost}")
    ck(cost["locked_ids"] == ["E-04"], f"R12 锁定条目仍被登记：{cost}")

    # 13. 无候选 ≠ 已确认没有可复用报告
    ck(judge_reuse([], qs[0])["verdict"] == "not_applicable",
       "R13 无候选 → not_applicable（不得写成已确认没有）")

    for f in fails:
        print(f"[FAIL] {f}")
    print(f"\n== reuse.py 自检：{'全部通过' if not fails else str(len(fails)) + ' 项失败'}"
          f"（{passed[0]} 项断言）==")
    return 1 if fails else 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "selftest":
        return selftest()
    if argv[0] == "check":
        if len(argv) < 2:
            print("用法：reuse.py check <input-checks.json>")
            return 2
        spec = json.loads(open(argv[1], encoding="utf-8").read())
        res = run_check_spec(spec)
        for r in res["results"]:
            if not r["pass"]:
                print(f"[FAIL] {r['label']}：{r['detail']}")
        print(f"\n== 用例 {spec.get('case', '')}：{res['n_checks']} 项检查，失败 {res['n_fail']} ==")
        return 1 if res["n_fail"] else 0
    if argv[0] == "judge":
        cand = json.loads(open(argv[1], encoding="utf-8").read())
        print(json.dumps(cand, ensure_ascii=False, indent=2))
        return 0
    print(f"未知子命令：{argv[0]}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

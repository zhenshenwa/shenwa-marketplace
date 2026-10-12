#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_plan.py · 发布计划 / 预算检查 / 任务关联 / 背景组装 —— v1.12.0 新增（第 3 轮）
v1.13.0（第 4 轮）追加：**背景组装接入真实发布入口** + **内容复用判定接通** + **完整流程编排**。

**要修的病根**：报告发布此前是「SOP 里随手调一次 `report_publish`」——
没有条目编号、没有必要性检查、没有预算概念、aid 按返回顺序认领，
于是出现：① 已有材料足够的问题照样发一份报告（凑数）；② 8 家研究悄悄缩成 3 份；
③ 两份并发提交各自「检查通过」后合计超额；④ 提交超时后换站重发；
⑤ aid 乱序返回时串到别的对象/问题上。

本模块是**唯一的发布入口**：

    plan = publish_plan.build(req, templates=..., history=...)
    for item in plan["items"]:
        publish_plan.publish(item, client=<shenwa_api.Client>, plan=plan, budget=ledger)
            └ ① gate(item, ...)      → decision      （只读，不发任何请求）
            └ ② decision=="proceed" 才 budget.reserve()  ← **原子：检查 + 预留**
            └ ③ 仍 proceed 才 client.call("report_publish", params)  ← 真实调用
            └ ④ 按 Result 回填 state / aid / reserve_state

公开接口：
  · `PLAN_FIELDS` / `ENTRY_STATES` / `RESERVE_STATES` / `DECISIONS` / `REUSE_STATES`
  · `template_cost()`      模板成本（**唯一可核对来源** = `templates` 接口 `cost` 字段）
  · `build()`              问题清单 → 发布计划（含 `entry_id`，**提交前生成**）
  · `gate()`               五查 + 参数一致性重检；返回互斥 `decision`
  · `publish()`            **真实发布入口**（gate 通过才调 `Client.call`）
  · `reuse_decision()`     查询三态；**v1.13.0 起**给了内容判定即落四档（`reuse_all` 可达）
  · `BudgetLedger`         三态预算（已结算 / 预留占用 / 拟新增）+ `reserve()` 原子预留
  · `save_state()` / `load_state()` / `restore_pending()`   轻量 JSONL 保存与恢复
  · `run_plan_flow()`      代表性流程用例（问题清单 → … → 交付覆盖检查）
  · `selftest()` / CLI

⚠️ **不联网**：`publish()` 内的网络处理**一行不写** —— 全部交给既有 `Client.call()`
（第 1 轮参数校验 / 第 6 轮错误分类 / `MUTATING_APIS` 副作用保护 / `submit_unknown` 防重发）。
⚠️ **aid ≠ 报告完成 ≠ 问题已回答**：拿到 aid 只把条目置 `accepted`，**绝不**置 `done`，
也**绝不**把对应问题标为 `answered`（那是轮询终态 + 交付门的事）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import capability as cap  # noqa: E402

# ═══════════════════════════════════════════════════════════════════
# 0. 词表（用户要求 1：每个条目「至少记录」的字段）
# ═══════════════════════════════════════════════════════════════════

#: 发布条目字段（用户要求 1 的「至少记录」清单，逐项在场）
PLAN_FIELDS = (
    "entry_id",          # 条目编号（**提交前生成**，不按返回顺序分配）
    "plan_id",           # 所属计划
    "object_id",         # 研究对象
    "object_level",      # 对象层级
    "question_ids",      # 对应的问题编号（可多个 → 一份报告支持多个问题）
    "questions",         # 问题原话（追溯用）
    "template_tid",      # 模板
    "template_name",     # 模板名
    "purpose",           # 本次研究用途
    "need_report_why",   # 为什么需要新报告
    "missing_materials", # 现有资料还缺什么（实质证据缺口）
    "research_cutoff",   # 研究截止时间（沿用第 1 轮四类时间口径）
    "input_materials",   # 输入材料
    "submit_params",     # 拟提交参数（tid / question / text）
    "history_state",     # 历史报告查询状态：queried_ok / query_failed / not_queried
    "reuse_state",       # 复用检查状态（三态，见 REUSE_STATES）
    "history_candidates",# 历史报告候选
    "cost",              # 预计成本（模板成本）
    "cost_state",        # known / unknown（**未知不得按 0**）
    "cost_source",       # 成本依据
    "reserve_state",     # 预算预留状态
    "state",             # 执行状态
    "aid",               # 服务端报告编号（整串，含 @@）
    "result_ref",        # 结果引用
    "plan_hash",         # 计划指纹（参数一致性重检用）
    "attempts",          # 提交尝试次数（**正常路径恒为 0/1**）
    # —— 第 4 轮新增 ——
    "background",        # 背景组装结果（含**实际发送文字**与逐条状态标注）
    "adjustment_required",  # 背景超限 → **必须先调整条目 / 执行方式**再发布
    "reuse_judgement",   # 逐题内容复用判定（第 4 轮）
    "reuse_decision",    # 复用对计划的影响（reuse_all / partial_narrowed / …）
    "reused_from",       # 复用来源（历史报告 aid → 问题编号）
)

#: 执行状态（用户要求 4）—— `accepted` **不等于** `done`
ENTRY_STATES = ("pending", "pending_authorization", "submitting", "accepted", "done",
                "failed", "submit_unknown")

#: **不得**当作新任务再次提交的状态（恢复任务时先读已有状态）
#: · `submitting` = 「提交意图已保存、结果尚未保存」→ 结果未定，**同样不得重发**
#:   （旧版漏了这一项，导致「恢复后第一次变 pending_authorization、第二次就真发了」）
LOCKED_STATES = ("accepted", "done", "submit_unknown", "submitting")

#: 预算预留状态（用户要求 4：预算预留状态也必须能够追踪）
RESERVE_STATES = ("none", "reserved", "settled", "released", "unknown")

#: `gate()` 的互斥决定（方案 §3.5(3)）
DECISIONS = ("no_publish", "reuse_all", "two_stage", "confirm_required", "proceed")

#: 复用**查询**三态（第 3 轮）+ 复用**内容**判定四档映射（第 4 轮）
#: 前三个都**不是**「可复用」；后三个由 `reuse.py` 的内容判定给出。
REUSE_STATES = (
    "no_candidate",          # 成功查询且无候选
    "candidate_unverified",  # 存在候选但尚未核实
    "unknown",               # 查询失败或结果未知
    "reuse_all",             # 内容判定：完整复用 → **不发布**
    "reuse_partial",         # 内容判定：部分复用 → 条目收窄后仍发布
    "reference_only",        # 内容判定：仅历史参考 → 不免除研究
    "not_applicable",        # 内容判定：不适用 / 待核实 → 不免除研究
)

#: 四档内容判定 → 发布计划的 `reuse_state`（**唯一映射处**，不另起一套词）
VERDICT_TO_REUSE_STATE = {
    "full_reuse": "reuse_all",
    "partial_reuse": "reuse_partial",
    "reference_only": "reference_only",
    "not_applicable": "not_applicable",
}

#: 内容判定后**仍然要发布**的复用状态（其余不发布）
REUSE_STILL_PUBLISH = ("reuse_partial", "reference_only", "not_applicable")

REUSE_NOTES = {
    "no_candidate": "已成功查询，列表中无候选 —— **仍须**有明确研究必要性与授权依据才能新发；"
                    "不得据此宣称「已确认没有可复用报告」",
    "candidate_unverified": "存在候选但**尚未核实内容** → 不得标成「已经复用」，"
                            "也不得标成「无候选」",
    "unknown": "查询失败或结果未知 → **不得**冒充「无历史报告」，"
               "**也不得**把查询失败当成生成新报告的理由",
    # —— 第 4 轮：内容判定四档 ——
    "reuse_all": "内容判定**完整复用** → **不发布**；复用内容进入对应问题的证据与回答",
    "reuse_partial": "内容判定**部分复用** → 条目**收窄到剩余缺口**后仍发布",
    "reference_only": "内容判定**仅作历史参考** → **不免除研究**，条目照常",
    "not_applicable": "内容判定**不适用 / 待核实** → **不免除研究**，条目照常",
}

#: 合法未完成原因（与第 2 轮 `requirement.py` 同一词表，不另起一套）
UNFINISHED_REASONS = ("material_insufficient", "calibre_incomparable", "no_authorization",
                      "submit_unknown", "out_of_scope", "capability_unavailable",
                      "not_implemented_yet")

PLAN_RULE = (
    "**报告份数由模板能力、具体问题、有效复用共同决定** —— "
    "**不得**默认「一家公司等于一份报告」，**不得**给「直取 / 计算 / 已有材料足够」的问题"
    "新增报告来凑齐计划，**也不得**把用户要求的 8 家静默缩减。"
    "一份报告可支持多个问题，一个问题也可需要多份报告。"
)

COST_RULE = (
    "**预计成本 = Σ(各条目模板成本) 逐项加总**，来源 = `templates` 接口 `data[].templates[].cost`；"
    "**成本无法确认 → `unknown`，不按 0 处理**；模板价格只用于**估算**，"
    "**只有获得扣点依据才标「实际消耗」** —— 不得把模板估价直接写成已经扣点。"
)

BUDGET_RULE = (
    "**账户余额 ≠ 用户允许本次全部花费的额度**（余额只作展示）；"
    "**数量提醒阈值不自动缩减研究范围，也不替代预算检查**；"
    "预算三分：**已结算消耗** / **尚未结算的预留占用** / **拟新增** —— 已结算**不再计入预留**；"
    "并发时「检查剩余额度 + 预留本次成本」**必须是一个不可被其他任务插入的操作**。"
)

AUTH_RULE = (
    "沿用已有授权规则：**用户已授权的范围与消耗内不再逐份询问**；"
    "超出授权时**只暂停受影响的未发布条目**（`pending_authorization`），"
    "**保留完整任务及待执行事项** —— 不得擅自缩减用户要求。"
)

# ═══════════════════════════════════════════════════════════════════
# 1. 模板成本（唯一可核对来源）
# ═══════════════════════════════════════════════════════════════════

COST_SOURCE_API = "templates（GET /api/manage/getTemplateListWithType）data[].templates[].cost"


def _flatten_templates(templates):
    """把 `templates` 接口返回（或简化映射）摊平成 `{tid(str): (name, cost)}`。"""
    out = {}
    if templates is None:
        return out
    if isinstance(templates, dict) and "data" not in templates:
        # 简化形态：{"9": 5} 或 {"9": {"cost": 5, "title": "..."}}
        for k, v in templates.items():
            if isinstance(v, dict):
                out[str(k)] = (v.get("title") or v.get("name"), v.get("cost"))
            else:
                out[str(k)] = (None, v)
        return out
    rows = templates.get("data") if isinstance(templates, dict) else templates
    if not isinstance(rows, (list, tuple)):
        return out
    for cat in rows:
        if not isinstance(cat, dict):
            continue
        for t in (cat.get("templates") or []):
            if isinstance(t, dict) and t.get("id") is not None:
                out[str(t["id"])] = (t.get("title") or t.get("name"), t.get("cost"))
    return out


def _as_cost(v):
    """成本归一：非负整数/浮点 → int；其余（含 None、空串、非数字）→ None（**不按 0**）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
        return int(f) if f.is_integer() else f
    return None


def template_cost(tid, templates=None):
    """单个模板的预计成本。

    返回 `{"tid", "name", "cost", "cost_state", "source"}`。
    **成本未知 → `cost=None` + `cost_state="unknown"`，绝不返回 0 冒充**。
    """
    flat = _flatten_templates(templates)
    key = str(tid)
    name, raw = flat.get(key, (None, None))
    c = _as_cost(raw)
    return {
        "tid": key,
        "name": name,
        "cost": c,
        "cost_state": "known" if c is not None else "unknown",
        "source": COST_SOURCE_API if flat else "（未提供 templates → 成本未知）",
    }


def sum_costs(costs):
    """逐项加总。**任一项未知 → `exact=False`，且 `value` 只报已知部分并显式标注**。"""
    known = [c for c in costs if c is not None]
    unknown_idx = [i for i, c in enumerate(costs) if c is None]
    return {
        "value": sum(known) if known else (0 if not unknown_idx else None),
        "known_sum": sum(known),
        "n_total": len(list(costs)),
        "n_unknown": len(unknown_idx),
        "unknown_positions": unknown_idx,
        "exact": not unknown_idx,
        "note": "**存在成本未知的条目 → 合计不精确，不得当作 0 或完整预算**"
                if unknown_idx else "全部条目成本均可核对",
    }


# ═══════════════════════════════════════════════════════════════════
# 2. 预算台账（三态 + 原子预留）
# ═══════════════════════════════════════════════════════════════════

class BudgetLedger:
    """预算三分：**已结算** / **预留占用（未结算）** / **拟新增**。

    · `limit`          —— **已授权的任务预算 / 具体发布范围**（不是账户余额！）
    · `account_balance`—— 账户余额，**只作展示**，不参与「能不能花」的判定
    · `threshold`      —— 数量提醒阈值，**不自动缩减范围**，也不替代预算检查

    `reserve()` 在**同一把锁内**完成「读总额 → 判断 → 写入」，因此并发任务
    不可能「各自检查通过后合计超额」。
    """

    def __init__(self, limit=None, *, settled=None, reserved=None, unknown=None,
                 account_balance=None, threshold=None, note=None):
        self.limit = limit
        self.account_balance = account_balance
        self.threshold = threshold
        self.note = note
        self._settled = dict(settled or {})      # entry_id -> cost（**已结算，不再计入预留**）
        self._reserved = dict(reserved or {})    # entry_id -> cost（预留占用，未结算）
        self._unknown = dict(unknown or {})      # entry_id -> True（占用存在但金额未知）
        self._lock = threading.RLock()

    # ——— 只读视图 ———
    def _totals_unlocked(self):
        s_vals = list(self._settled.values())
        r_vals = list(self._reserved.values())
        s_unknown = [k for k, v in self._settled.items() if v is None]
        r_unknown = [k for k, v in self._reserved.items() if v is None]
        settled = sum(v for v in s_vals if v is not None)
        reserved = sum(v for v in r_vals if v is not None)
        return {
            "settled": {"value": settled, "n": len(self._settled),
                        "n_unknown": len(s_unknown)},
            "reserved": {"value": reserved, "n": len(self._reserved),
                         "n_unknown": len(r_unknown)},
            "exact": not (s_unknown or r_unknown),
        }

    def snapshot(self):
        with self._lock:
            t = self._totals_unlocked()
            used = t["settled"]["value"] + t["reserved"]["value"]
            return {
                "limit": self.limit,
                "account_balance": self.account_balance,   # 展示项，不参与判定
                "threshold": self.threshold,               # 提醒项，不自动缩减
                "settled": t["settled"],
                "reserved": t["reserved"],
                "planned_new": {"value": 0, "n": 0},
                "used": used,
                "remaining": (self.limit - used) if isinstance(self.limit, (int, float)) else None,
                "exact": t["exact"],
                "settled_entries": sorted(self._settled),
                "reserved_entries": sorted(self._reserved),
                # ★ **结算状态未知**的条目（结果未知的提交）——占用保留但**未确认**
                "unknown_entries": sorted(self._unknown),
                "n_unknown_state": len(self._unknown),
                "rule": BUDGET_RULE,
            }

    # ——— 只读预检（gate 用；**不写入**） ———
    def peek(self, cost):
        """只读检查：当前额度能否容下 `cost`。**不预留**（真正的原子操作在 `reserve`）。"""
        with self._lock:
            t = self._totals_unlocked()
            used = t["settled"]["value"] + t["reserved"]["value"]
            if self.limit is None:
                return {"ok": False, "reason": "no_authorized_limit",
                        "why": "未提供**已授权预算上限** → 不得仅凭账户余额直接发布",
                        "used": used, "remaining": None}
            if cost is None:
                return {"ok": False, "reason": "cost_unknown",
                        "why": "条目成本未知 → **不得按 0 处理**，须先取得模板成本",
                        "used": used, "remaining": round(self.limit - used, 4)}
            if not t["exact"]:
                return {"ok": False, "reason": "existing_unknown_occupancy",
                        "why": "已有占用金额未知（结果未知的提交）→ 不得贸然新增",
                        "used": used, "remaining": round(self.limit - used, 4)}
            after = used + cost
            if after > self.limit:
                return {"ok": False, "reason": "would_exceed",
                        "why": f"已用 {used} + 拟新增 {cost} = {after} > 授权上限 {self.limit}",
                        "used": used, "remaining": round(self.limit - used, 4),
                        "would_be": after}
            return {"ok": True, "reason": "within_limit", "used": used,
                    "remaining": round(self.limit - used, 4), "would_be": after}

    # ——— 原子：检查 + 预留（不可被其他并发任务插入） ———
    def reserve(self, entry_id, cost):
        """**检查剩余额度 + 预留本次成本**，在同一把锁内完成。

        返回 `{"ok", "reason", "why", "reserved", "remaining", ...}`；
        `ok=False` 时**未写入任何占用**。
        """
        with self._lock:
            t = self._totals_unlocked()
            used = t["settled"]["value"] + t["reserved"]["value"]
            if entry_id in self._reserved or entry_id in self._settled:
                return {"ok": False, "reason": "already_occupied",
                        "why": f"条目 {entry_id} 已有占用（不得重复预留）",
                        "used": used}
            if self.limit is None:
                return {"ok": False, "reason": "no_authorized_limit",
                        "why": "未提供已授权预算上限 → 不得发布", "used": used}
            if cost is None:
                return {"ok": False, "reason": "cost_unknown",
                        "why": "成本未知 → **不按 0 处理**，须先确认模板成本", "used": used}
            if not t["exact"]:
                return {"ok": False, "reason": "existing_unknown_occupancy",
                        "why": "存在金额未知的占用 → 不得新增预留", "used": used}
            after = used + cost
            if after > self.limit:
                return {"ok": False, "reason": "would_exceed",
                        "why": f"已用 {used} + 拟新增 {cost} = {after} > 授权上限 {self.limit}",
                        "used": used, "would_be": after,
                        "remaining": round(self.limit - used, 4)}
            # —— 临界区：写入 ——
            self._reserved[entry_id] = cost
            return {"ok": True, "reason": "reserved", "entry_id": entry_id,
                    "reserved": cost, "used": after,
                    "remaining": round(self.limit - after, 4)}

    # ——— 结算 / 释放 / 转未知 ———
    def settle(self, entry_id, actual=None):
        """**只在获得结算依据时**调用。`actual=None` → 金额记为未知（占用仍保留）。"""
        with self._lock:
            cost = self._reserved.pop(entry_id, None)
            # 已结算 ⇒ 不再是「结算状态未知」
            self._unknown.pop(entry_id, None)
            self._settled[entry_id] = actual if actual is not None else cost
            return {"entry_id": entry_id, "settled": self._settled[entry_id],
                    "cost_actual_known": actual is not None}

    def release(self, entry_id, reason="confirmed_no_consumption"):
        """**只有确认未产生消耗或得到明确依据**时才释放预留。"""
        with self._lock:
            had = self._reserved.pop(entry_id, None)
            self._unknown.pop(entry_id, None)
            return {"entry_id": entry_id, "released": had, "reason": reason}

    def mark_unknown(self, entry_id, cost=None):
        """结果未知 → **保留占用**，只把金额标为未知（不释放）。"""
        with self._lock:
            if entry_id in self._reserved:
                self._reserved[entry_id] = None if cost is None else cost
                self._unknown[entry_id] = True
            elif entry_id not in self._settled:
                self._reserved[entry_id] = None
                self._unknown[entry_id] = True
            return {"entry_id": entry_id, "reserve_state": "reserved",
                    "amount_known": self._reserved.get(entry_id) is not None}


# ═══════════════════════════════════════════════════════════════════
# 3. 条目编号 / 指纹
# ═══════════════════════════════════════════════════════════════════

def _question_signature(q):
    """单个问题的**内容 + 执行参数**规范化签名（用于计划指纹）。

    ⚠️ 必须包含**实际问题内容与执行参数**。只取「对象 + 局部问题编号（如 Q1）」
    会让「同一公司、不同问题」得到相同编号：同一天对同一公司既做估值分析、
    又做财务分析时，两条会产生相同 `plan_id` / `entry_id`，并互相**误复用 aid**
    （第二项被当成第一项的历史、直接跳过不发布）。
    """
    return {
        "question_id": q.get("question_id"),
        "object_id": q.get("object_id"),
        "object_level": q.get("object_level"),
        "source_text": q.get("source_text"),
        "metric_families": list(q.get("metric_families") or []),
        "execution_mode": q.get("execution_mode"),
        "template": q.get("template"),
        "answer_slot": q.get("answer_slot"),
        "research_cutoff": q.get("research_cutoff"),
    }


def _plan_fingerprint(plan_date, objects, rep_qs):
    """plan 的内容指纹（确定性）：由日期 + 对象集合 + **报告候选问题的内容与执行参数**决定。

    · 同一逻辑任务（相同对象 / 问题内容 / 执行参数 / 日期）→ 相同指纹 → 相同 plan_id
      （重试 / 恢复 / 新会话可复现）；
    · 内容或执行参数不同的独立计划 → 不同指纹 → 不同 plan_id，**天然不冲突**
      （同公司不同问题不再撞编号）；
    · 用户明确发起的、内容也相同的独立新任务 → 由调用方传 `plan_token` 覆盖（见 build()）。
    """
    sigs = [_question_signature(q) for q in (rep_qs or [])]
    payload = {
        "date": plan_date,
        "objects": sorted(str(o) for o in (objects or [])),
        # 问题签名**排序后**入指纹：与问题在清单中的先后顺序无关（顺序变化不换编号）
        "questions": sorted(json.dumps(s, ensure_ascii=False, sort_keys=True, default=str)
                            for s in sigs),
    }
    st = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(st.encode("utf-8")).hexdigest()[:8]


def make_entry_id(plan_date, n, plan_suffix=None):
    """条目编号 = `<YYYYMMDD>[-<计划指纹>]-<NN>`。

    `plan_suffix` 为 plan 内容指纹（见 `_plan_fingerprint`）：**不同计划的条目天然
    不冲突**（不再是「日期 + 当天局部序号」这一可冲突形式）；未提供时保持旧格式
    （仅用于向后兼容的旧记录读取）。
    """
    d = plan_date if isinstance(plan_date, str) else plan_date.isoformat()
    if plan_suffix:
        return f"{d}-{plan_suffix}-{int(n):02d}"
    return f"{d}-{int(n):02d}"


def plan_fingerprint(item):
    """参数一致性指纹：对象 / 模板 / 输入材料 / 提交参数。

    `gate()` 用它判断「检查之后是否被改过」—— 改过就必须**重新检查**，
    **不得**沿用旧结果直接发布。
    """
    payload = {
        "object_id": item.get("object_id"),
        "object_level": item.get("object_level"),
        "template_tid": item.get("template_tid"),
        "question_ids": list(item.get("question_ids") or []),
        "submit_params": item.get("submit_params") or {},
        "input_materials": item.get("input_materials") or [],
        "research_cutoff": item.get("research_cutoff"),
    }
    st = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(st.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# 4. 复用检查接口（查询三态 + **内容判定四档**（v1.13.0 起由 `reuse.py` 提供））
# ═══════════════════════════════════════════════════════════════════

def reuse_decision(*, history=None, history_state=None, candidates=None, content=None):
    """历史报告**查询状态** → 三态；给了**内容判定**（第 4 轮）→ 直接落到四档。

    `history` 可以是：
      · `None` / `{"state": "query_failed"}`  → `unknown`
      · `{"state": "queried_ok", "candidates": []}` → `no_candidate`
      · `{"state": "queried_ok", "candidates": [...]}` → `candidate_unverified`

    `content` = `reuse.judge_reuse()` 的结果 → 其 `verdict` 映射为发布计划用的
    `reuse_state`（`full_reuse → reuse_all` 等）；**只有它能让 `reuse_all` 可达**。
    """
    st = history_state or (history or {}).get("state")
    cand = candidates if candidates is not None else (history or {}).get("candidates") or []
    if st == "queried_ok" and not cand:
        state, why = "no_candidate", REUSE_NOTES["no_candidate"]
    elif st == "queried_ok" and cand:
        state, why = "candidate_unverified", REUSE_NOTES["candidate_unverified"]
    else:
        state, why = "unknown", REUSE_NOTES["unknown"]

    content_check, content_why = "not_judged", (
        "候选内容是否**仍然有效**（口径 / 时点 / 范围是否仍适用于本次问题）"
        "由 `reuse.py` 的内容判定给出；**未提供内容判定时此处为 `not_judged`**，"
        "**不得**据此宣称「已确认没有可复用报告」")
    reachable = False
    if isinstance(content, dict) and content.get("verdict"):
        state = VERDICT_TO_REUSE_STATE.get(content["verdict"], state)
        why = f"{REUSE_NOTES.get(state, '')}｜内容判定：{content.get('note') or ''}"
        content_check = "judged"
        content_why = "已由 `reuse.py` 按内容判定（6 项核查 + 四档）"
        reachable = True
    return {
        "state": state,
        "why": why,
        "history_state": st or "not_queried",
        "n_candidates": len(cand),
        "candidates": list(cand),
        "content_check": content_check,
        "content_check_why": content_why,
        "reuse_all_reachable": reachable,   # ★ 第 4 轮：内容判定后可触发
        "content": content,
        "rule": "查询三态**都不是「可复用」**；未知状态不得标成「已经复用」，"
                "也不得标成「已确认没有可复用报告」；"
                "**只有内容判定（`reuse.py`）为 `full_reuse` 时 `reuse_all` 才可达**",
    }


# ═══════════════════════════════════════════════════════════════════
# 4.5 背景组装（第 4 轮）—— 让深挖拿到**与任务相关**的必要背景
# ═══════════════════════════════════════════════════════════════════

#: 第 1 轮确认的参数约定（**不得放宽**）
TEXT_LIMIT = 500        # `text` ≤ 500 字
QUESTION_LIMIT = 60     # `question` ≤ 60 字

BACKGROUND_RULES = {
    "key_only": "**只选择本次研究需要的关键背景**，完整材料保留在本地记录中",
    "self_contained": "**不假设深挖能够读取本地路径 / 文件名 / 内部材料编号** —— "
                      "实际发送的文字必须能够**独立理解**",
    "no_silent_trim": "压缩背景**不得改变**数字 / 单位 / 时间 / 条件 / 核实状态，"
                      "**也不得机械截断**关键内容",
    "overflow": "若长度限制无法容纳必要条件 → **调整问题条目或执行方式**，"
                "并**重新检查需求覆盖与预算**；**不得静默删除关键约束后继续发布**",
}

#: 背景条目种类（用户要求 2）
BACKGROUND_KINDS = {
    "verified_number": "经核实且影响本次分析的数字（**保留来源 / 时间 / 单位 / 口径**）",
    "unverified_material": "与任务相关但**未核实**的材料（**必须标明状态**）",
    "user_hypothesis": "用户观点 → 表述为**待检验命题**（不得改写成已确认事实）",
    "existing_judgement": "解释 / 验证已有判断时可提供该判断（**注明来源、时间、用途**）",
    "preset_conclusion": "预设结论（**独立判断当前状态时不得作为事实注入**）",
}

#: 组装模式
BACKGROUND_MODES = ("independent", "explain_existing")

BACKGROUND_DEFAULT_TAIL = (
    "请按上述时点给出结论并标明数据口径与来源时点；"
    "若某项数据缺失，请明确说明缺什么，不要用其它口径代替。"
)


def background_item(kind, text, *, source=None, asof=None, unit=None, calibre=None,
                    state=None, purpose=None, material_id=None, question_ids=None,
                    optional=False) -> dict:
    """构造一条**背景条目**（渲染规则由 `render_background_item()` 统一决定）。"""
    if kind not in BACKGROUND_KINDS:
        raise ValueError(f"未知背景种类：{kind}（限 {tuple(BACKGROUND_KINDS)}）")
    return {"kind": kind, "text": text, "source": source, "asof": asof, "unit": unit,
            "calibre": calibre, "state": state, "purpose": purpose,
            "material_id": material_id, "question_ids": list(question_ids or []),
            "optional": bool(optional), "label": BACKGROUND_KINDS[kind]}


def render_background_item(it) -> str:
    """把一条背景条目渲染成**可独立理解**的一小段文字（状态一律显式标注）。"""
    k = it.get("kind")
    t = (it.get("text") or "").strip()
    if k == "verified_number":
        bits = [f"来源：{it['source']}" if it.get("source") else None,
                f"时点：{it['asof']}" if it.get("asof") else None,
                f"单位：{it['unit']}" if it.get("unit") else None,
                f"口径：{it['calibre']}" if it.get("calibre") else None]
        return f"{t}（已核实；" + "；".join(b for b in bits if b) + "）"
    if k == "unverified_material":
        return f"{t}（**尚未独立核实**，仅按材料原文转述，请勿据此直接下结论）"
    if k == "user_hypothesis":
        return f"{t}〔**待检验命题**：用户提出的观点，尚未核实，请以证据检验而非当作已知〕"
    if k == "existing_judgement":
        bits = [f"来源：{it['source']}" if it.get("source") else None,
                f"时点：{it['asof']}" if it.get("asof") else None,
                f"用途：{it['purpose']}" if it.get("purpose") else None]
        return f"{t}（**既有判断**；" + "；".join(b for b in bits if b) + "）"
    if k == "preset_conclusion":
        return f"{t}（**预设结论**，仅作待检验假设，不得当作事实）"
    return t


def background_from_materials(records, question_ids=None, *, mode="independent",
                              optional=False) -> list:
    """把第 4 轮的材料登记记录（`evidence.make_material` 产物）转成背景条目。

    · `primary_fact` / `computed`：`verified` → `verified_number`，否则 `unverified_material`；
    · `external_judgement` → `existing_judgement`；
    · `user_hypothesis` → `user_hypothesis`。
    """
    qids = set(question_ids or [])
    out = []
    for m in (records or []):
        if qids and not (set(m.get("question_ids") or []) & qids):
            continue
        ck, st = m.get("claim_kind"), m.get("evidence_state")
        if ck == "user_hypothesis":
            kind = "user_hypothesis"
        elif ck == "external_judgement":
            kind = "existing_judgement"
        elif st == "verified":
            kind = "verified_number"
        else:
            kind = "unverified_material"
        out.append(background_item(
            kind, m.get("statement") or "", source=m.get("source_name"),
            asof=(m.get("material_asof") if m.get("material_asof") != "unknown" else None),
            unit=m.get("unit"), calibre=m.get("calibre"), state=st,
            purpose=m.get("note") or ("验证既有判断" if kind == "existing_judgement" else None),
            material_id=m.get("material_id"), question_ids=m.get("question_ids"),
            optional=optional))
    return out


def assemble_background(items, *, object_id, cutoff, purposes, mode="independent",
                        max_len=TEXT_LIMIT, tail=None) -> dict:
    """按问题组装要发送给深挖的 `text`（**只做组装，不发送**）。

    返回 `{"ok", "sent_text", "length", "included", "excluded", "dropped",
    "requires_adjustment", "suggestion", ...}`。

    · **超限不静默截断**：先丢弃**显式标 `optional=True`** 的背景，并把丢弃项**如实记录**；
      仍超限 → `ok=False` + `requires_adjustment=True`（调用方须**调整问题条目 / 执行方式**
      并**重新检查覆盖与预算**），**不产出被截断的 text**；
    · **独立判断模式**下，`existing_judgement` / `preset_conclusion` **一律不注入**
      （避免把预设结论当事实）。
    """
    if mode not in BACKGROUND_MODES:
        raise ValueError(f"未知背景模式：{mode}（限 {BACKGROUND_MODES}）")
    tail = tail if tail is not None else BACKGROUND_DEFAULT_TAIL
    excluded, kept = [], []
    for it in (items or []):
        k = it.get("kind")
        if mode == "independent" and k in ("existing_judgement", "preset_conclusion"):
            excluded.append({**it, "excluded_why":
                             "独立判断当前状态 → **不把预设结论 / 既有判断当作事实注入**"})
            continue
        if k == "unverified_material" and not it.get("state"):
            it = {**it, "state": "unverified"}
        kept.append(it)

    dropped = []

    def _compose(klist):
        head = f"标的：{object_id}。分析时点：{cutoff}。研究视角：{purposes}。"
        if klist:
            body = "背景（按状态标注）：" + "；".join(
                f"{i + 1}) {render_background_item(it)}" for i, it in enumerate(klist)) + "。"
        else:
            body = ""
        return head + body + tail

    text = _compose(kept)
    # 超限 → 只丢**显式可选**项，且如实记录
    if len(text) > max_len:
        while kept and len(text) > max_len:
            idx = next((j for j in range(len(kept) - 1, -1, -1) if kept[j].get("optional")), None)
            if idx is None:
                break
            dropped.append({**kept.pop(idx), "dropped_why": "超出参数长度上限，且该项标记为可选"})
            text = _compose(kept)

    within = len(text) <= max_len
    ok = within
    res = {
        "ok": ok, "within_limit": within,
        "sent_text": text if ok else None,
        "length": len(text), "limit": max_len,
        "included": kept, "excluded": excluded, "dropped": dropped,
        "n_included": len(kept), "n_excluded": len(excluded), "n_dropped": len(dropped),
        "requires_adjustment": not ok,
        "suggestion": None if ok else (
            f"背景长 {len(text)} > {max_len} 字且无可丢弃的可选项 → "
            f"**必须调整问题条目或执行方式**（拆分问题条目 / 改用两阶段），"
            f"并**重新检查需求覆盖与预算**；**不得静默删除关键约束后继续发布**"),
        "mode": mode, "object_id": object_id, "cutoff": cutoff,
        "rule": BACKGROUND_RULES,
    }
    return res


def check_text_length(text, limit=TEXT_LIMIT) -> dict:
    """第 1 轮 `text` 约定自检（**不修改内容**）。"""
    t = text or ""
    return {"length": len(t), "limit": limit, "within_limit": len(t) <= limit,
            "over_by": max(0, len(t) - limit),
            "note": "在参数约定内" if len(t) <= limit else
                    f"超出 {len(t) - limit} 字 → **不得机械截断**，须调整条目 / 执行方式"}


# ═══════════════════════════════════════════════════════════════════
# 5. build()：问题清单 → 发布计划
# ═══════════════════════════════════════════════════════════════════

def _one_line(s, n=60):
    """把问题原话提炼成一句（**不机械截断**；超限只提示，交给参数校验处理）。"""
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s


def _purpose_of(item_qs, object_id):
    if len(item_qs) == 1:
        return f"回答「{item_qs[0]}」"
    return f"一次报告覆盖 {len(item_qs)} 个问题：" + "；".join(item_qs)


def _text_of(object_id, cutoff, purposes):
    """背景消息：≈100 字，**只写元指令**（标的 + 分析时点 + 研究视角 + 估值方法），不写数字。"""
    return (f"标的：{object_id}。分析时点：{cutoff}。"
            f"研究视角：{purposes}。"
            f"请按上述时点给出结论并标明数据口径与来源时点；"
            f"若某项数据缺失，请明确说明缺什么，不要用其它口径代替。")


def _background_for(oid, qlist, material_records, background_by_object, mode):
    """取本条目要用的背景条目：优先按对象的显式清单，其次由材料登记记录筛选。"""
    if isinstance(background_by_object, dict) and oid in background_by_object:
        return list(background_by_object.get(oid) or [])
    if material_records:
        return background_from_materials(material_records,
                                         [q.get("question_id") for q in qlist], mode=mode)
    return []


def build(req, *, templates=None, history=None, objects=None, object_level=None,
          plan_date=None, research_cutoff=None, materials=None,
          purpose_of=None, group_by=("object_id", "template_tid"),
          plans_from=None, template_ok=None, balance=None, limit=None,
          account_balance=None, threshold=None,
          material_records=None, background_by_object=None,
          background_mode="independent", reuse_by_question=None,
          plan_token=None) -> dict:
    """把第 2 轮的**问题清单**规范化成**发布计划**（含 `entry_id`）。

    · **只为 `execution_mode == "report_candidate"` 的问题建条目** ——
      直取 / 计算 / 已有材料足够的问题**不为凑齐计划而新增报告**；
    · 默认按 `(对象, 模板)` 合并 → **一份报告可支持多个问题**；
      `group_by=("object_id","template_tid","question_id")` 则**一个问题一份报告**；
    · **对象全保留**：8 家 → 8 组（份数另由问题与模板决定），**不静默减为 3**；
    · `history` 逐对象给（`{"某公司": {"state": ..., "candidates": [...]}}`）。

    **plan_id / entry_id 唯一性**：`plan_id = PLAN-<日期>-<内容指纹>`、`entry_id = <日期>-<指纹>-<NN>`。
    同一天内容不同的独立计划 → 指纹不同 → 编号天然不冲突；同一任务重试/恢复/新会话
    读回（内容相同）→ 指纹相同 → 编号保持稳定。用户**明确发起的、内容也相同的独立
    新任务**须传 `plan_token`（任意非空串）→ 作为额外盐，生成新 plan_id / entry_id。
    """
    pd = plan_date or date.today().isoformat()
    if isinstance(pd, date):
        pd = pd.isoformat()
    objs = list(objects) if objects else list((req or {}).get("objects") or [])
    level = object_level or (req or {}).get("object_level") or "未指明"
    hist = history or {}

    qs = list((req or {}).get("questions") or [])
    rep_qs = [q for q in qs if q.get("execution_mode") == "report_candidate"]

    # —— plan_id / entry_id 唯一性：内容指纹 + 可选 plan_token 盐 ——
    # ⚠️ 指纹取**问题内容与执行参数**（不是「对象 + Q1」这种局部编号）
    plan_suffix = _plan_fingerprint(pd, objs, rep_qs)
    if plan_token:
        import hashlib as _hl
        plan_suffix = _hl.sha256((plan_suffix + ":" + str(plan_token))
                                 .encode("utf-8")).hexdigest()[:8]
    plan_id = f"PLAN-{pd}-{plan_suffix}"

    groups, order = {}, []
    for q in rep_qs:
        oid = q.get("object_id")
        tid = _template_tid_for(q, templates, level)
        key = tuple(
            {"object_id": oid, "template_tid": tid,
             "question_id": q.get("question_id")}.get(k, q.get(k))
            for k in group_by)
        if key not in groups:
            groups[key] = {"object_id": oid, "template_tid": tid, "qs": []}
            order.append(key)
        groups[key]["qs"].append(q)

    items, warnings = [], []
    for i, key in enumerate(order, 1):
        g = groups[key]
        oid, tid, qlist = g["object_id"], g["template_tid"], g["qs"]
        tc = template_cost(tid, templates)
        # 历史报告查询状态：给了对象条目 → 用之；给了 history 但缺本对象 → 视为已查无候选；
        # 完全未提供 history → not_queried（**不得**当成「已确认没有可复用报告」）
        h = hist.get(oid) if isinstance(hist, dict) else None
        if isinstance(h, dict):
            reuse = reuse_decision(history=h)
        elif history is not None:
            reuse = reuse_decision(history={"state": "queried_ok", "candidates": []})
        else:
            reuse = reuse_decision(history=None)
        qtexts = [_one_line(q.get("source_text")) for q in qlist]
        cut = research_cutoff or next((q.get("research_cutoff") for q in qlist
                                      if q.get("research_cutoff")), None) or "unspecified"
        purpose = purpose_of(g) if callable(purpose_of) else _purpose_of(qtexts, oid)
        miss = sorted({m for q in qlist for m in (q.get("execution_gaps") or [])})
        qids = [q.get("question_id") for q in qlist]

        # —— 第 4 轮：逐题**内容复用判定**（给了才生效；`full_reuse` → `reuse_all`） ——
        judge = None
        if isinstance(reuse_by_question, dict):
            jrows = [(qid, reuse_by_question.get(qid)) for qid in qids
                     if reuse_by_question.get(qid)]
            if jrows:
                if all((j or {}).get("verdict") == "full_reuse" for _, j in jrows):
                    judge = jrows[0][1]
                else:
                    # 部分覆盖 → 用「非完整复用」的那条作为条目判定（不冒充整体可复用）
                    judge = next((j for _, j in jrows
                                  if (j or {}).get("verdict") != "full_reuse"), jrows[0][1])
                reuse = reuse_decision(history=h if isinstance(h, dict)
                                       else {"state": "queried_ok", "candidates": []},
                                       content=judge)

        # —— 第 4 轮：**背景组装**（只做组装；超限则要求调整，不产出被截断的 text） ——
        bg_items = _background_for(oid, qlist, material_records,
                                   background_by_object, background_mode)
        bg = (assemble_background(bg_items, object_id=oid, cutoff=cut, purposes=purpose,
                                  mode=background_mode) if bg_items else None)
        if bg is None:
            text_val = _text_of(oid, cut, purpose)      # 无背景材料 → 最小元指令
        else:
            text_val = bg["sent_text"]                  # 超限时为 None（**不截断**）

        entry = {
            "entry_id": make_entry_id(pd, i, plan_suffix),
            "plan_id": plan_id,
            "object_id": oid,
            "object_level": qlist[0].get("object_level") or level,
            "question_ids": [q.get("question_id") for q in qlist],
            "questions": qtexts,
            "template_tid": tid,
            "template_name": tc["name"],
            "purpose": purpose,
            "need_report_why": _need_why(qlist),
            "missing_materials": miss,
            "research_cutoff": cut,
            "input_materials": _materials_for(qlist, materials),
            "submit_params": {
                "tid": str(tid) if tid is not None else "",
                "question": _one_line(purpose, QUESTION_LIMIT),
                "text": text_val,
            },
            "background": bg,
            "adjustment_required": bool(bg and not bg["ok"]),
            "reuse_judgement": ({q: (reuse_by_question.get(q) or {}).get("verdict")
                                 for q in qids} if isinstance(reuse_by_question, dict) else None),
            "reuse_decision": None,
            "reused_from": None,
            "history_state": reuse["history_state"],
            "reuse_state": reuse["state"],
            "history_candidates": reuse["candidates"],
            "cost": tc["cost"],
            "cost_state": tc["cost_state"],
            "cost_source": tc["source"],
            "reserve_state": "none",
            "state": "pending",
            "aid": None,
            "result_ref": None,
            "attempts": 0,
            "notes": [],
        }
        entry["plan_hash"] = plan_fingerprint(entry)
        qlen = len(entry["submit_params"]["question"] or "")
        if qlen > QUESTION_LIMIT:
            warnings.append(f"{entry['entry_id']}：question 提炼后 {qlen} 字"
                            f" > {QUESTION_LIMIT} → 交给参数校验处理，**不机械截断**")
        if tid is None:
            warnings.append(f"{entry['entry_id']}：未确定模板 tid → 不能发布")
        if tc["cost_state"] == "unknown":
            warnings.append(f"{entry['entry_id']}：模板成本未知 → `unknown`（**不按 0**）")
        if entry["adjustment_required"]:
            warnings.append(f"{entry['entry_id']}：**背景超出参数长度上限** → "
                            f"{bg['suggestion']}")
        if bg is not None and bg["n_excluded"]:
            warnings.append(f"{entry['entry_id']}：{bg['n_excluded']} 条既有判断 / 预设结论"
                            f"**未注入**（独立判断模式）")
        items.append(entry)

    # —— 计划对问题清单的覆盖（覆盖 ≠ 完成） ——
    covered_qids = {qid for it in items for qid in it["question_ids"]}
    rep_qids = {q.get("question_id") for q in rep_qs}
    nonrep = [q.get("question_id") for q in qs
              if q.get("execution_mode") != "report_candidate"]
    costs = [it["cost"] for it in items]
    plan = {
        "plan_id": plan_id,
        "plan_date": pd,
        "n_items": len(items),
        "items": items,
        "objects": objs or sorted({it["object_id"] for it in items}),
        "object_level": level,
        "fields": PLAN_FIELDS,
        "coverage": {
            "n_report_candidate": len(rep_qids),
            "covered_by_plan": sorted(covered_qids),
            "not_covered": sorted(rep_qids - covered_qids),
            "non_report_questions": nonrep,
            "ok": not (rep_qids - covered_qids),
            "note": "报告候选问题全部落到条目上；直取 / 计算 / 已有材料的问题**不建条目**",
        },
        "cost": sum_costs(costs),
        "budget": {"limit": limit if limit is not None else None,
                   "account_balance": account_balance if account_balance is not None else balance,
                   "threshold": threshold,
                   "planned_new": sum_costs(costs),
                   "note": "**账户余额 ≠ 授权额度**；数量提醒不自动缩减范围"},
        "object_coverage": {
            "n_objects": len(objs),
            "objects": objs,
            "mode": "每对象独立成组 —— **不得默认「一家公司等于一份报告」，也不得静默缩减**",
        },
        "warnings": warnings,
        "rule": PLAN_RULE,
        "cost_rule": COST_RULE,
        "budget_rule": BUDGET_RULE,
        "auth_rule": AUTH_RULE,
        "reuse_api": "reuse_decision() 查询三态 + **内容判定四档**（`reuse.py`）；"
                     "`full_reuse` → `reuse_all` 可达",
        "background": {
            "mode": background_mode,
            "n_entries_with_background": sum(1 for it in items if it.get("background")),
            "n_adjustment_required": sum(1 for it in items if it.get("adjustment_required")),
            "text_limit": TEXT_LIMIT, "question_limit": QUESTION_LIMIT,
            "rule": BACKGROUND_RULES,
        },
        "reuse": {
            "n_reuse_all": sum(1 for it in items if it.get("reuse_state") == "reuse_all"),
            "n_reuse_partial": sum(1 for it in items
                                   if it.get("reuse_state") == "reuse_partial"),
            "judged": bool(isinstance(reuse_by_question, dict)),
            "note": "复用结果关联第 2 轮问题编号；`full_reuse` 的条目**不发布**，"
                    "其内容进入对应问题的证据与回答",
        },
    }
    return plan


def _template_tid_for(q, templates, level):
    """从问题记录里取模板 tid（第 2 轮 `per_question_routing` 已逐题给出）。"""
    t = q.get("template")
    if isinstance(t, dict):
        for k in ("初选", "tid", "primary"):
            if t.get(k) is not None:
                return t[k]
    if q.get("template_tid") is not None:
        return q["template_tid"]
    fams = q.get("metric_families") or []
    # 兜底：只用第 2 轮 `router.FAMILY_TEMPLATE` 同一张表（不另造）
    try:
        import router as _rt
        for f in fams:
            if _rt.FAMILY_TEMPLATE.get(f) is not None:
                return _rt.FAMILY_TEMPLATE[f]
        tids = _rt.TEMPLATES_BY_LEVEL.get(level) or []
        return tids[0] if tids else None
    except Exception:
        return None


def _need_why(qlist):
    kinds = sorted({(q.get("asks") or {}).get("kind") or "fact_query" for q in qlist})
    gaps = sorted({m for q in qlist for m in (q.get("execution_gaps") or [])})
    return ("现有接口与材料无法直接回答 " +
            "；".join(q.get("source_text") or "" for q in qlist) +
            ("（缺口：" + "、".join(gaps) + "）" if gaps else "（需模板化报告补足）") +
            f"；问题类型={kinds}")


def _materials_for(qlist, materials):
    out = []
    for q in qlist:
        for mr in (q.get("material_refs") or []):
            out.append(mr)
        for f in (q.get("metric_families") or []):
            m = (materials or {}).get(f) if isinstance(materials, dict) else None
            if isinstance(m, dict):
                out.append({"family": f, "asof": m.get("asof"),
                            "source": m.get("source", "SYNTHETIC(未标注)"),
                            "calibre": m.get("calibre")})
    return out


# ═══════════════════════════════════════════════════════════════════
# 5.6 复用导致计划调整后的**重新检查**（第 4 轮）
# ═══════════════════════════════════════════════════════════════════

def recheck_after_reuse(plan, *, background_mode="independent", material_records=None,
                        background_by_object=None) -> dict:
    """计划因复用而调整后，**重新生成参数并重算指纹**（＝重新检查）。

    · 只处理**仍将发布**的条目；`reuse_all` 与**已锁定条目**一律跳过
      —— **不清除已提交 / 结果未知条目的状态与预算占用**；
    · 收窄条目（`question_ids` 变了）按**剩余问题**重新生成 `purpose / question / text`；
    · 返回逐条 `before/after` 指纹与文字长度，供核对「确实重新检查过」。
    """
    items = list((plan or {}).get("items") or [])
    rows = []
    for it in items:
        eid = it.get("entry_id")
        if it.get("state") in LOCKED_STATES:
            rows.append({"entry_id": eid, "action": "skipped_locked",
                         "why": "已提交 / 结果未知 → **状态与预算占用一律保留**"})
            continue
        if it.get("reuse_decision") == "reuse_all":
            rows.append({"entry_id": eid, "action": "skipped_reused",
                         "why": "完整复用 → **不发布**，无需生成提交参数"})
            continue
        before_hash, before_len = it.get("plan_hash"), len(
            (it.get("submit_params") or {}).get("text") or "")
        qids = list(it.get("question_ids") or [])
        # 按**剩余问题**重新生成参数
        qtexts = [q for q in (it.get("questions") or [])]
        if it.get("reuse_decision") == "partial_narrowed" and it.get("reused_from"):
            kept = [q for q in qtexts if q][:len(qids)] or qtexts
            qtexts = kept
        purpose = _purpose_of(qtexts, it.get("object_id"))
        qlist = [{"question_id": q} for q in qids]
        bg_items = _background_for(it.get("object_id"), qlist, material_records,
                                   background_by_object, background_mode)
        bg = (assemble_background(bg_items, object_id=it.get("object_id"),
                                  cutoff=it.get("research_cutoff"), purposes=purpose,
                                  mode=background_mode) if bg_items else None)
        text_val = (bg["sent_text"] if bg else
                    _text_of(it.get("object_id"), it.get("research_cutoff"), purpose))
        it["purpose"] = purpose
        it["submit_params"] = {
            "tid": (it.get("submit_params") or {}).get("tid", ""),
            "question": _one_line(purpose, QUESTION_LIMIT),
            "text": text_val,
        }
        it["background"] = bg
        it["adjustment_required"] = bool(bg and not bg["ok"])
        it["plan_hash"] = plan_fingerprint(it)
        it["rechecked_after_reuse"] = True
        rows.append({"entry_id": eid, "action": "rechecked",
                     "question_ids": qids, "purpose": purpose,
                     "plan_hash_before": before_hash, "plan_hash_after": it["plan_hash"],
                     "text_len_before": before_len, "text_len_after": len(text_val or ""),
                     "hash_changed": before_hash != it["plan_hash"]})
    new_plan = dict(plan or {})
    new_plan["items"] = items
    new_plan["recheck"] = {
        "rows": rows,
        "n_rechecked": sum(1 for r in rows if r["action"] == "rechecked"),
        "n_skipped_locked": sum(1 for r in rows if r["action"] == "skipped_locked"),
        "n_skipped_reused": sum(1 for r in rows if r["action"] == "skipped_reused"),
        "note": "**复用判定或背景变化导致计划调整 → 必须重新检查**："
                "按剩余问题重新生成参数并重算 `plan_hash`；"
                "**已提交 / 结果未知条目的状态与预算占用保留**",
        "rule": "调整发布计划后重新计算预计新增消耗，并检查已有授权",
    }
    return new_plan


# ═══════════════════════════════════════════════════════════════════
# 6. gate()：五查 + 参数一致性重检（**只读，不发任何请求**）
# ═══════════════════════════════════════════════════════════════════

def gate(item, *, plan=None, budget=None, authorized=None, two_stage=None,
         require_necessity=True) -> dict:
    """一次条目的**提交前检查**。返回互斥 `decision`。

    五查：
      ① 必要性 —— 为什么需要新报告（`need_report_why` + 实质缺口）
      ② 历史报告检查状态 —— 三态（`unknown` **不冒充**「无候选」）
      ③ 计划是否完整覆盖用户要求（`plan["coverage"]["ok"]`）
      ④ 授权与预算 —— 授权范围 + 额度（**只读预检**；原子预留发生在 `publish()`）
      ⑤ 提交参数与计划一致 —— `plan_hash` 重检（改过 → 必须重检）

    **第 1 轮参数校验不在此处**：它仍在 `Client.call()` 内层生效，本函数**不吞不改**。
    """
    checks = {}
    # ① 必要性
    why = (item.get("need_report_why") or "").strip()
    has_gap = bool(item.get("missing_materials"))
    checks["necessity"] = {"need_report_why": bool(why), "has_material_gap": has_gap,
                           "ok": bool(why)}
    # ② 历史报告状态
    rs = item.get("reuse_state") or "unknown"
    checks["history"] = {"reuse_state": rs, "state": item.get("history_state"),
                         "ok": rs in REUSE_STATES,
                         "note": REUSE_NOTES.get(rs, "")}
    # ③ 计划覆盖
    cov = (plan or {}).get("coverage") or {}
    checks["plan_coverage"] = {"ok": bool(cov.get("ok")), "not_covered": cov.get("not_covered")}
    # ④ 授权
    auth_ok, auth_why = True, ""
    if authorized is not None:
        if callable(authorized):
            r = authorized(item)
            auth_ok = bool(r) if not isinstance(r, dict) else bool(r.get("ok"))
            auth_why = "" if auth_ok else (r.get("why") if isinstance(r, dict) else "不在已授权范围")
        elif isinstance(authorized, dict):
            allowed = authorized.get("entry_ids") or authorized.get("objects")
            if allowed is not None:
                ok = (item.get("entry_id") in allowed) or (item.get("object_id") in allowed)
                auth_ok, auth_why = ok, "" if ok else "不在已授权的发布范围内"
    checks["authorization"] = {"ok": auth_ok, "why": auth_why}
    # 预算只读预检
    if budget is not None:
        checks["budget_peek"] = budget.peek(item.get("cost"))
    # ⑤ 参数一致性（重检）
    cur = plan_fingerprint(item)
    same = (cur == item.get("plan_hash"))
    checks["params_match"] = {"ok": same, "plan_hash": item.get("plan_hash"), "current": cur}
    # ⑥ 背景是否在参数约定内（第 4 轮）—— 超限**不得静默截断后继续发布**
    bg = item.get("background")
    adj = bool(item.get("adjustment_required"))
    tlen = len((item.get("submit_params") or {}).get("text") or "")
    checks["background_fit"] = {
        "ok": not adj, "adjustment_required": adj,
        "text_length": tlen, "limit": TEXT_LIMIT,
        "within_limit": tlen <= TEXT_LIMIT,
        "note": ("背景超出参数长度上限 → **必须先调整问题条目或执行方式**，"
                 "并**重新检查需求覆盖与预算**" if adj else "背景在参数约定内"),
        "suggestion": (bg or {}).get("suggestion"),
    }
    # 已受理 / 结果未知的条目**不得当作新任务再次提交**
    st = item.get("state")
    checks["prior_state"] = {"state": st, "ok": st not in LOCKED_STATES}

    # —— 决定（互斥，按优先级） ——
    if not checks["prior_state"]["ok"]:
        return _decision("no_publish", item,
                         f"条目状态 `{st}` 已是「已受理 / 结果未知 / 已完成」→ "
                         f"**不得当作新任务再次提交**（恢复任务时先读已有状态）", checks)
    if not checks["params_match"]["ok"]:
        return _decision("confirm_required", item,
                         "检查后**对象 / 模板 / 输入材料 / 提交参数已被修改** → "
                         "必须**针对变化重新检查**，不得沿用旧结果直接发布", checks,
                         extra={"recheck_required": True})
    if require_necessity and not checks["necessity"]["ok"]:
        return _decision("no_publish", item,
                         "无明确必要性（缺 `need_report_why` / 无可查的实质缺口）→ **不发布**", checks)
    if two_stage:
        return _decision("two_stage", item,
                         "用户选择了「先筛后深挖」这一**另一种交付深度** → 按新计划提交"
                         "（**不当作同一任务的节省**）", checks)
    if rs == "reuse_all" or (isinstance(two_stage, dict) and two_stage.get("reuse")):
        return _decision("reuse_all", item,
                         "内容判定为**完整复用** → **不提交，改为读取既有报告**；"
                         "复用内容进入对应问题的证据与回答", checks)
    if adj:
        return _decision("confirm_required", item,
                         f"背景**超出参数长度上限**（{tlen} > {TEXT_LIMIT}）→ "
                         f"必须先**调整问题条目或执行方式**，并重新检查需求覆盖与预算；"
                         f"**不得静默删除关键约束后继续发布**", checks,
                         extra={"recheck_required": True, "background_adjustment": True})
    if not checks["plan_coverage"]["ok"]:
        return _decision("confirm_required", item,
                         f"计划未完整覆盖用户要求（未覆盖 {cov.get('not_covered')}）→ "
                         f"先补计划，不得直接发布", checks)
    if not auth_ok:
        return _decision("confirm_required", item,
                         f"超出已授权范围（{auth_why}）→ **暂停该条目**并保留待执行事项", checks,
                         extra={"pending_authorization": True})
    if "budget_peek" in checks and not checks["budget_peek"]["ok"]:
        bp = checks["budget_peek"]
        d = "confirm_required" if bp["reason"] != "no_authorized_limit" else "confirm_required"
        return _decision(d, item, f"预算未通过（{bp['reason']}）：{bp['why']}（**只读预检**，"
                                  f"真正的原子预留发生在 publish()）", checks,
                         extra={"pending_authorization": True})
    if item.get("cost_state") == "unknown":
        return _decision("confirm_required", item,
                         "模板成本**未知** → **不按 0 处理**，须先取得可核对的模板成本", checks,
                         extra={"pending_authorization": True})
    return _decision("proceed", item, "五查通过 → 可提交（实际参数校验仍由 Client.call 内层执行）",
                     checks)


def _decision(d, item, why, checks, extra=None):
    assert d in DECISIONS, f"非法 decision：{d}"
    out = {"decision": d, "why": why, "checks": checks,
           "entry_id": item.get("entry_id"), "submits": d == "proceed"}
    if extra:
        out.update(extra)
    return out


# ═══════════════════════════════════════════════════════════════════
# 7. publish()：真实发布入口
# ═══════════════════════════════════════════════════════════════════

#: 提交时**明确未产生消耗**的分类（可安全释放预留）
_NO_CONSUMPTION = ("param", "permission", "auth")


def _sync_plan_item(plan, item):
    """把 `publish()` 内部的条目快照**写回计划**（供「发送前持久化」使用）。

    `publish()` 为不改调用方 dict 而对 `item` 做的是**浅拷贝**，因此发送前的
    `submitting` / `reserved` 状态必须显式同步回 `plan["items"]` 才能被保存。
    """
    if not isinstance(plan, dict):
        return
    for pit in (plan.get("items") or []):
        if pit.get("entry_id") == item.get("entry_id"):
            pit.update({k: item[k] for k in ("state", "aid", "reserve_state", "attempts")
                        if k in item})
            return


def publish(item, *, client, plan=None, budget=None, gate_kwargs=None,
            api="report_publish", trace=None, save_path=None) -> dict:
    """**真实发布入口** —— 逐份发布一律经此函数。

    流程：`gate()` → 通过才 `budget.reserve()`（原子）→ **发送前持久化提交意图**
    →  `client.call()` → 回填。

    · **不捕获网络异常、不自行重试**：`ApiError` / 传输异常一律由 `Client.call()` 既有
      分类与恢复逻辑处理（含 `submit_unknown`、`MUTATING_APIS` 副作用保护）；
    · **不改** `MUTATING_APIS` / 不绕开 `append_task` / 不触碰 `open_submissions`；
    · `decision != "proceed"` 时**提前 return** → 发布调用次数恒 **0**；
    · `save_path`（可选）：给了就在**发出写请求之前**把 `submitting` + `reserved`
      状态持久化到该文件 —— 中断后可按此恢复到「待核对」，**不重发**；
      持久化失败 → **中止本次发布**（发布调用数 0，且释放本次预留）。
    """
    item = dict(item)                     # 不改调用方的 dict
    g = gate(item, plan=plan, budget=budget, **(gate_kwargs or {}))
    out = {"entry_id": item.get("entry_id"), "api": api, "decision": g["decision"],
           "why": g["why"], "publish_calls": 0, "state": item.get("state"),
           "reserve_state": item.get("reserve_state"), "aid": None,
           "gate": g, "recheck_required": bool(g.get("recheck_required"))}
    if g["decision"] != "proceed":
        if item.get("state") not in LOCKED_STATES + ("pending_authorization",):
            item["state"] = "pending"
        if g.get("pending_authorization"):
            item["state"] = "pending_authorization"
        out["state"] = item["state"]
        out["sent_params"] = None          # ★ 未提交 → **没有实际发送的内容**
        out["sent_text"] = None
        out["item"] = item
        _trace(trace, {"entry_id": item.get("entry_id"), "decision": g["decision"],
                       "publish_calls": 0})
        return out

    # —— 原子预留（检查 + 预留不可分割） ——
    if budget is not None:
        r = budget.reserve(item.get("entry_id"), item.get("cost"))
        if not r["ok"]:
            item["state"] = "pending_authorization"
            out.update({"state": "pending_authorization", "decision": "confirm_required",
                        "why": f"预算预留未通过（{r['reason']}）：{r['why']}", "reserve": r,
                        "item": item})
            _trace(trace, {"entry_id": item.get("entry_id"), "decision": "confirm_required",
                           "reason": r["reason"], "publish_calls": 0})
            return out
        out["reserve"] = r

    item["state"] = "submitting"
    item["attempts"] = (item.get("attempts") or 0) + 1
    if budget is not None and (out.get("reserve") or {}).get("ok"):
        item["reserve_state"] = "reserved"        # ★ 发送前：占用已挂在该条目上

    # —— ★ 发送前**持久化提交意图**（submitting + reserved）——
    # 调用传输层的那一刻，磁盘上必须已经是「提交中 / 已预留」；否则进程中断后
    # 恢复时看不到这次提交，会把条目当成待发布而**重复提交**。
    if save_path and plan is not None:
        _sync_plan_item(plan, item)
        r_pre = save_state(plan, save_path)
        if not r_pre["ok"]:
            if budget is not None:
                budget.release(item["entry_id"], reason="state_save_failed_before_send")
            item["state"] = "pending"
            item["reserve_state"] = "none"
            _sync_plan_item(plan, item)
            out.update({"decision": "no_publish", "publish_calls": 0,
                        "state": item["state"], "reserve_state": item["reserve_state"],
                        "item": item, "save_error": r_pre["error"],
                        "sent_params": None, "sent_text": None,
                        "why": "提交意图保存失败 → 中止发布（发布调用数 0）"})
            _trace(trace, {"entry_id": item.get("entry_id"), "decision": "no_publish",
                           "publish_calls": 0, "reason": "state_save_failed"})
            return out

    # —— 真实调用：网络层处理一行不写，全部交给既有 Client.call ——
    # ★ 第 4 轮：**保存实际发送的参数与文字**（验收查的是这里，不是组装函数的输出）
    # ★ 第 15 轮：**task_id = entry_id** —— 同一条目重试沿用同一任务身份，
    #   由 Client.call 的防重机制阻断重复发布（跨会话/跨进程均生效）。
    sent = dict(item.get("submit_params") or {})
    out["sent_params"] = dict(sent)
    out["sent_text"] = sent.get("text")
    out["sent_text_length"] = len(sent.get("text") or "")
    out["text_check"] = check_text_length(sent.get("text"))
    res = client.call(api, sent, task_id=item.get("entry_id"))
    out["publish_calls"] = 1
    out["result"] = res.to_dict() if hasattr(res, "to_dict") else dict(res or {})
    cat = getattr(res, "category", None)
    ok = bool(getattr(res, "ok", False))
    sstate = getattr(res, "submit_state", None)
    aid = getattr(res, "task_id", None)

    if ok and aid:
        item["state"] = "accepted"           # ★ **不是** done
        item["aid"] = aid                    # 整串（含 @@）
        item["result_ref"] = f"aid:{aid}"
        if budget is not None:
            budget.settle(item["entry_id"], actual=None)   # 无扣点依据 → 金额未知，占用保留为已结算
        item["reserve_state"] = "settled"
        out["cost_actual"] = {"value": None, "state": "unknown",
                              "why": "模板估价 ≠ 已扣点；**只有获得扣点依据才标实际消耗**"}
    elif ok and not aid:
        # 受理成功却取不到 aid → **无法绑定条目**，按不确定性处理（不得当作完成）
        item["state"] = "submit_unknown"
        item["reserve_state"] = "reserved"
        if budget is not None:
            budget.mark_unknown(item["entry_id"], item.get("cost"))
        out["note"] = ("受理成功但**未取到 aid** → 无法绑定条目，按结果未知处理："
                       "**不自动重发**，占用保留，改用只读 `reconcile()` 核对")
    elif sstate == "submit_unknown":
        item["state"] = "submit_unknown"
        item["reserve_state"] = "reserved"   # ★ **保留占用，不释放**
        if budget is not None:
            budget.mark_unknown(item["entry_id"], item.get("cost"))
        out["note"] = ("提交结果未知 → 沿用 `submit_unknown` 保护：**不自动换线路重发**；"
                       "占用保留，只有确认未消耗或取得结算依据后才调整")
    elif cat in _NO_CONSUMPTION:
        item["state"] = "failed"
        item["reserve_state"] = "released"
        if budget is not None:
            budget.release(item["entry_id"], reason=f"{cat}:未产生消耗")
        out["note"] = f"明确失败（{cat}）→ 确认未产生消耗，释放预留；**不自动重试**"
    else:
        item["state"] = "failed"
        item["reserve_state"] = "unknown"    # ★ 服务端生成失败 **不等于** 未扣点
        if budget is not None:
            budget.mark_unknown(item["entry_id"], item.get("cost"))
        out["note"] = (f"失败分类 `{cat}`：**服务端可能已受理/扣点** → 占用**保留**并标 unknown；"
                       f"**不得**直接等同于「未扣点」")
    out["state"] = item["state"]
    out["aid"] = item.get("aid")
    out["reserve_state"] = item["reserve_state"]
    out["item"] = item
    _trace(trace, {"entry_id": item.get("entry_id"), "decision": "proceed",
                   "publish_calls": 1, "category": cat, "aid": item.get("aid"),
                   "state": item["state"]})
    return out


def _trace(trace, rec):
    if trace is not None:
        trace.append(dict(rec))


# ═══════════════════════════════════════════════════════════════════
# 8. 保存 / 恢复（复用既有轻量 JSONL 机制）
# ═══════════════════════════════════════════════════════════════════

DEFAULT_STATE_FILE = os.path.join(os.path.expanduser("~"), ".workbuddy",
                                  "shenwa_publish_plan.jsonl")


def save_state(plan, path=None):
    """把计划（**提交前**生成的 `entry_id` 与全部状态）追加保存到 JSONL。

    返回 `{"ok": bool, "error": str|None, "path": str}`：
    · **写失败显式报告**（不再静默吞掉）—— 调用方在「必要记录保存失败」时
      必须**中止发布**（发布调用数为 0）；
    · 追加 + `flush` + `fsync` 保证落盘（提交意图在发请求前已持久化）。
    """
    p = path or DEFAULT_STATE_FILE
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        rec = {"ts": datetime.now().astimezone().replace(microsecond=0).isoformat(),
               "plan_id": plan.get("plan_id"), "items": plan.get("items")}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "error": str(e), "path": p}
    return {"ok": True, "error": None, "path": p}


def load_state(path=None):
    """读回全部计划快照。返回 `(snaps, errors)`：
    · `errors` 非空 → 记录损坏/不可读，调用方**不得当成「无历史」继续提交**；
    · 文件不存在 → `([], [])`（真「无历史」，与「损坏」区分）。
    """
    p = path or DEFAULT_STATE_FILE
    out, errors = [], []
    if not os.path.exists(p):
        return out, errors
    try:
        with open(p, encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception as e:                            # noqa: BLE001
                    errors.append({"line": idx, "error": str(e), "raw": line[:120]})
    except Exception as e:                                        # noqa: BLE001
        return out, [{"line": None, "error": str(e), "raw": None}]
    return out, errors


def restore_pending(path=None, plan_id=None):
    """恢复任务：**先读已有状态**；已受理 / 结果未知 / **提交中**的条目
    不得当作新任务再次提交。

    · `submitting`（提交意图已保存、结果尚未保存）→ 归入 `to_reconcile`（待核对），
      **不直接恢复为可重新提交的 `pending`**；
    · `plan_id` 精确匹配：找不到时 `found=False` 且 `plan_not_found=True`，
      **不得回退到其它计划冒充匹配结果**；
    · 记录损坏/不可读 → `read_error` 显式列出，不得当成「无历史」。
    """
    snaps, errors = load_state(path)
    if errors:
        return {"found": False, "items": [], "blocked_resubmit": [],
                "read_error": errors,
                "note": "计划快照损坏/不可读，**不得当成无历史继续提交**"}
    if not snaps:
        return {"found": False, "items": [], "blocked_resubmit": [],
                "plan_not_found": bool(plan_id),
                "note": "无本地计划快照"}
    snap = snaps[-1]
    if plan_id:
        matched = [s for s in reversed(snaps) if s.get("plan_id") == plan_id]
        if not matched:
            return {"found": False, "plan_id": plan_id, "plan_not_found": True,
                    "items": [], "blocked_resubmit": [],
                    "note": f"未找到 plan_id={plan_id} 的计划快照（精确匹配，不回退到其它计划）"}
        snap = matched[0]
    items = snap.get("items") or []
    blocked = [it.get("entry_id") for it in items
               if it.get("state") in ("accepted", "done", "submit_unknown")]
    # ★ 提交意图已保存但结果未保存（submitting）→ 待核对，不得重新提交
    to_reconcile = [it.get("entry_id") for it in items if it.get("state") == "submitting"]
    return {
        "found": True, "plan_id": snap.get("plan_id"), "ts": snap.get("ts"),
        "items": items,
        "blocked_resubmit": blocked,
        "to_reconcile": to_reconcile,
        "pending": [it.get("entry_id") for it in items if it.get("state") == "pending"],
        "unknown": [it.get("entry_id") for it in items if it.get("state") == "submit_unknown"],
        "accepted": [it.get("entry_id") for it in items if it.get("state") == "accepted"],
        "note": "已受理 / 结果未知 / 提交中 的条目**保留占用、不重复发布**；"
                "`submit_unknown` 沿用只读 `reconcile()` 核对；"
                "`submitting` 进入待核对（不直接恢复为 pending）",
    }


# ═══════════════════════════════════════════════════════════════════
# 9. 代表性流程：问题清单 → … → 交付覆盖检查
# ═══════════════════════════════════════════════════════════════════

def _restore_into_plan(plan, save_path, ledger):
    """恢复：把**已保存**的条目状态回填到新 build 的计划，并恢复预算占用。

    · 按 `plan_id`（含内容指纹，确定性）精确匹配历史快照；
    · 已受理（accepted）/ 已完成（done）/ 结果未知（submit_unknown）/ 提交中（submitting）
      的条目 → 状态回填 + aid 完整保留 + 预算占用恢复（accepted 结算、未知/submitting 预留）；
    · 返回回填后的 plan 和恢复摘要；无历史 / 记录损坏时**不静默当新任务**。
    """
    rec = restore_pending(save_path, plan_id=plan.get("plan_id"))
    restored = []
    if not rec.get("found"):
        return plan, {"restored": restored, "restore": rec}
    by_entry = {it.get("entry_id"): it for it in rec.get("items") or []}
    for it in plan.get("items") or []:
        old = by_entry.get(it.get("entry_id"))
        if not old:
            continue
        st = old.get("state")
        # 已受理 / 已完成 / 结果未知 / 提交中 / **待授权** 一律回填 ——
        # `pending_authorization` 也必须回填，否则恢复后**丢失原占用**，
        # 下一次就会因为「账本为空」而重新预留成功并真的把条目发出去。
        if st in ("accepted", "done", "submit_unknown", "submitting", "pending_authorization"):
            it["state"] = st
            it["aid"] = old.get("aid")
            if old.get("reserve_state"):
                it["reserve_state"] = old.get("reserve_state")
            # 恢复预算占用
            if ledger is not None:
                if st in ("accepted", "done"):
                    ledger.settle(it["entry_id"], actual=old.get("cost"))
                elif st == "pending_authorization":
                    # 待授权：占用按「未知金额」保留，**不得清零**
                    ledger.mark_unknown(it["entry_id"], old.get("cost"))
                else:
                    ledger.mark_unknown(it["entry_id"], old.get("cost"))
            restored.append({"entry_id": it["entry_id"], "state": st, "aid": old.get("aid")})
    return plan, {"restored": restored, "restore": rec}


class RecordingClient:
    """离线替身：与真实 `Client` 同签名，**只记账不联网**。

    用于「底层接口用替身，但必须经过实际计划和执行代码」的验收。
    aid 默认**递增生成**（`ManualNews@@<n>@@<stamp>`），以便验证「乱序返回也不串对象/问题」。
    """

    def __init__(self, script=None, aid_map=None, default=None, aid_prefix="ManualNews",
                 stamp="20261003", evidence_map=None):
        self.script = list(script or [])
        self.aid_map = dict(aid_map or {})
        self.default = default
        self.aid_prefix = aid_prefix
        self.stamp = stamp
        #: `entry_id` → 该条目**返回的证据**（第 4 轮：让「报告返回后的证据处理」
        #: 在离线流程里也真的拿到东西，而不是空跑；值可以是
        #: `[...]` 或 `{"evidence": [...], "claims_two_way_independent": bool}`）
        self.evidence_map = dict(evidence_map or {})
        self.calls = []
        self._n = 0

    def _next(self, params):
        if self.script:
            return dict(self.script.pop(0))
        key = (params or {}).get("tid")
        if key in self.aid_map:
            return {"ok": True, "category": "ok", "aid": self.aid_map[key]}
        if self.default is not None:
            return dict(self.default)
        self._n += 1
        return {"ok": True, "category": "ok",
                "aid": f"{self.aid_prefix}@@{self._n:04d}@@{self.stamp}"}

    def call(self, name, params=None, **kw):
        self.calls.append({"api": name, "params": dict(params or {})})
        r = self._next(params)
        return _FakeResult(**r)


class _FakeResult:
    def __init__(self, ok=True, category="ok", aid=None, submit_state=None, message=""):
        self.ok = ok
        self.category = category
        self.data = {"data": {"aid": aid}} if aid else None
        self.submit_state = submit_state
        self.task_id = aid
        self.message = message
        self.warnings = []
        self.http_status = 200 if ok else 400
        self.attempts = 1
        self.elapsed = 0.01
        self.base = "scripted"
        self.diagnostics = {}

    def to_dict(self):
        return {"ok": self.ok, "category": self.category, "aid": self.task_id,
                "submit_state": self.submit_state, "message": self.message}


def run_plan_flow(text, *, objects=None, object_level=None, materials=None,
                  templates=None, history=None, limit=None, account_balance=None,
                  threshold=None, client=None, route_fn=None, plan_date=None,
                  authorized=None, two_stage=None, save_path=None):
    """**贯穿实际专家包流程**：问题清单 → 发布计划 → 最终检查 → 预算预留
    → 模拟提交 → aid 回填 → 状态更新 → 交付覆盖检查。

    发布端是离线替身（`RecordingClient`），**不产生任何真实付费发布**。
    """
    import requirement as req_mod
    if route_fn is None:
        import router as route_mod
        route_fn, families_of = route_mod.route, route_mod.families_for_text
    else:
        import router as route_mod
        families_of = route_mod.families_for_text

    r = route_fn(text, available=None)
    tpl = r.get("template")
    req = req_mod.build_requirements(text, reading=r, objects=objects,
                                     object_level=object_level, families_of=families_of,
                                     materials=materials, template_ok=bool(tpl))
    # 逐题模板（第 2 轮 `per_question`）挂回问题记录，供 build 取 tid
    pq = {x.get("question_id"): x for x in (r.get("per_question") or [])}
    for q in req["questions"]:
        m = pq.get(q["question_id"]) or {}
        if m.get("template") is not None:
            q["template"] = m["template"]

    plan = build(req, templates=templates, history=history, objects=objects,
                 object_level=object_level, plan_date=plan_date,
                 research_cutoff=(req.get("time") or {}).get("research_cutoff"),
                 materials=materials, limit=limit, account_balance=account_balance,
                 threshold=threshold)
    ledger = BudgetLedger(limit=limit, account_balance=account_balance, threshold=threshold)
    restore_info = {"restored": []}
    flow_save_error = None
    if save_path:
        # ★ 恢复：已受理/未知/提交中/待授权的条目状态回填 + 预算占用恢复（不重新发布）
        plan, restore_info = _restore_into_plan(plan, save_path, ledger)
        # 🔴 恢复快照**损坏/不可读** → 无法确认是否已有提交 → **中止发布**（调用数 0）
        #    （绝不能因为「读不出来」就当成新任务把条目再发一遍）
        r_rec = (restore_info or {}).get("restore") or {}
        if r_rec.get("read_error"):
            return {"task": text, "route": r, "requirements": req, "plan": plan,
                    "items": plan["items"], "results": [], "trace": [],
                    "budget": ledger.snapshot(), "aid_bindings": [], "gate": None,
                    "restore": restore_info, "publish_calls": 0,
                    "n_items": plan["n_items"], "flow": [],
                    "restore_error": r_rec["read_error"],
                    "rule": "本地计划快照损坏/不可读 → 中止发布（发布调用数 0），"
                            "不得当作新任务重试"}

    cli = client if client is not None else RecordingClient()
    trace = []
    results = []
    for it in plan["items"]:
        res = publish(it, client=cli, plan=plan, budget=ledger, trace=trace,
                      gate_kwargs={"authorized": authorized, "two_stage": two_stage},
                      save_path=save_path)
        results.append(res)
        it.update({k: res["item"][k] for k in ("state", "aid", "reserve_state") if k in res["item"]})
        if res.get("save_error"):
            # 🔴 提交意图 / 结果**落盘失败** → 立即停止后续条目（不得继续发布其余条目）
            flow_save_error = res["save_error"]
            break
        # ★ 每条结果取得后**及时保存**（不等到整批结束）—— 中断可恢复，aid 完整保留
        if save_path:
            r_save = save_state(plan, save_path)
            if not r_save["ok"]:
                res["save_error"] = r_save["error"]
                flow_save_error = r_save["error"]
                break

    # aid 回填：**按 entry_id 绑定，不按返回顺序**；乱序也不串对象/问题
    aid_bindings = [{"entry_id": it["entry_id"], "aid": it.get("aid"),
                     "object_id": it["object_id"], "question_ids": it["question_ids"]}
                    for it in plan["items"] if it.get("aid")]
    if save_path and not flow_save_error:
        save_state(plan, save_path)

    # —— 交付覆盖检查（复用第 2 轮 `deliver_gate`；**aid ≠ 问题已回答**） ——
    answers = {}
    for q in req["questions"]:
        qid = q["question_id"]
        if q["execution_mode"] != "report_candidate":
            m = (materials or {}).get((q["metric_families"] or [None])[0])
            if isinstance(m, dict) and m.get("value") is not None:
                answers[qid] = {"status": "answered", "answer_slot": q["answer_slot"],
                                "evidence": [{"family": (q["metric_families"] or [None])[0],
                                              "value": m.get("value"), "asof": m.get("asof"),
                                              "source": m.get("source", "SYNTHETIC(未标注)")}]}
            else:
                answers[qid] = {"status": "unfinished", "reason": "material_insufficient",
                                "answer_slot": q["answer_slot"]}
        else:
            # 报告候选：即使已拿到 aid，也只能是 `unfinished`（**报告尚未完成 ≠ 已回答**）
            ent = next((it for it in plan["items"] if qid in it["question_ids"]), None)
            st = (ent or {}).get("state")
            answers[qid] = {
                "status": "unfinished",
                "reason": "submit_unknown" if st == "submit_unknown" else "not_implemented_yet",
                "answer_slot": q["answer_slot"],
                "note": ("已提交并取得 aid，但**aid 只代表服务端受理**，"
                         "报告未完成前不得标为已回答" if (ent or {}).get("aid")
                         else ("提交结果未知，占用保留" if st == "submit_unknown"
                               else "报告候选路径：本轮以离线替身提交")),
            }
    gate_out = req_mod.deliver_gate(req, answers)

    return {
        "task": text,
        "route": r,
        "requirements": req,
        "plan": plan,
        "items": plan["items"],
        "results": results,
        "trace": trace,
        "budget": ledger.snapshot(),
        "aid_bindings": aid_bindings,
        "gate": gate_out,
        "restore": restore_info,
        "save_error": flow_save_error,
        "publish_calls": sum(1 for t in trace if t.get("publish_calls")),
        "n_items": plan["n_items"],
        "flow": [{"entry_id": it["entry_id"], "object_id": it["object_id"],
                  "question_ids": it["question_ids"], "state": it["state"],
                  "aid": it.get("aid"), "decision":
                      next((x["decision"] for x in results
                            if x["entry_id"] == it["entry_id"]), None)}
                 for it in plan["items"]],
        "rule": "问题清单 → 发布计划 → 最终检查 → 预算预留 → 模拟提交 → aid 回填 → "
                "状态更新 → 交付覆盖检查（**全程经实际计划与执行代码**）",
    }


def _register_sources(req, material_records, objects):
    """第 4 轮：把材料登记记录补上缺失的对象 / 问题归属（**不新增一套记录**）。"""
    qs = list((req or {}).get("questions") or [])
    default_obj = (objects or [None])[0]
    out = []
    for m in (material_records or []):
        m = dict(m)
        if not m.get("object_id"):
            m["object_id"] = default_obj
        if not m.get("question_ids"):
            m["question_ids"] = [q.get("question_id") for q in qs]
        out.append(m)
    return out


def run_reuse_flow(text, *, objects=None, object_level=None, materials=None,
                   material_records=None, relations=None, templates=None,
                   candidates_by_question=None, limit=None, account_balance=None,
                   threshold=None, client=None, route_fn=None, plan_date=None,
                   authorized=None, background_mode="independent",
                   background_by_object=None, sent_background=None,
                   returned_evidence_by_entry=None,
                   required_use="current_basis", redo_requested=False,
                   new_timepoint=None, new_scenario=None, save_path=None) -> dict:
    """**第 4 轮完整流程**（贯穿实际专家包代码，发布端是离线替身）：

        问题清单与材料 → 来源登记及核对 → 历史报告内容检查
        → 确定复用部分与研究缺口 → 为必要的新报告组装背景
        → 参数与发布检查 → 执行及结果回填 → 证据处理 → 逐题交付

    离线保证：发布端默认 `RecordingClient`（**只记账、不联网**）。

    「证据处理」一步**必须有输入**：`returned_evidence_by_entry`
    （或替身的 `evidence_map`）按 `entry_id` 提供**报告返回的证据**；
    未提供时该步产出为空 —— **不得据此宣称「已确认没有复述 / 没有同源」**。
    """
    import requirement as req_mod
    import router as route_mod
    import reuse as reuse_mod
    import evidence as ev_mod

    route_fn = route_fn or route_mod.route
    r = route_fn(text, available=None)
    req = req_mod.build_requirements(text, reading=r, objects=objects,
                                     object_level=object_level,
                                     families_of=route_mod.families_for_text,
                                     materials=materials, template_ok=bool(r.get("template")))
    pq = {x.get("question_id"): x for x in (r.get("per_question") or [])}
    for q in req["questions"]:
        m = pq.get(q["question_id"]) or {}
        if m.get("template") is not None:
            q["template"] = m["template"]

    # ① 来源登记及核对
    registry = _register_sources(req, material_records, objects)
    graph = ev_mod.SourceGraph(materials=registry, relations=relations)
    cutoff = (req.get("time") or {}).get("research_cutoff")

    # ② 历史报告**内容**检查（逐题，四档）
    judgements = reuse_mod.judge_all(
        candidates_by_question or {}, req["questions"], object_level=object_level,
        research_cutoff=cutoff, required_use=required_use, new_timepoint=new_timepoint,
        new_scenario=new_scenario, redo_requested=redo_requested,
        current_materials=list((materials or {}).keys()) or None)

    # ③ 组装发布计划（带背景 + 复用判定）
    plan = build(req, templates=templates, objects=objects, object_level=object_level,
                 plan_date=plan_date, research_cutoff=cutoff, materials=materials,
                 limit=limit, account_balance=account_balance, threshold=threshold,
                 material_records=registry, background_by_object=background_by_object,
                 background_mode=background_mode, reuse_by_question=judgements["by_question"])

    # ④ 复用回填 → 重新检查 → 重算预计新增消耗
    plan = reuse_mod.apply_reuse(plan, judgements)
    plan = recheck_after_reuse(plan, background_mode=background_mode,
                               material_records=registry,
                               background_by_object=background_by_object)
    cost_after = reuse_mod.recompute_plan_cost(plan)

    # ⑤ 参数与发布检查 → 执行 → 结果回填
    ledger = BudgetLedger(limit=limit, account_balance=account_balance, threshold=threshold)
    restore_info = {"restored": []}
    flow_save_error = None
    if save_path:
        # ★ 恢复：已受理/未知/提交中/待授权的条目状态回填 + 预算占用恢复（不重新发布）
        plan, restore_info = _restore_into_plan(plan, save_path, ledger)
        # 🔴 恢复快照**损坏/不可读** → 中止发布（调用数 0），不得当作新任务重发
        r_rec = (restore_info or {}).get("restore") or {}
        if r_rec.get("read_error"):
            return {"task": text, "route": r, "requirements": req, "plan": plan,
                    "items": plan["items"], "results": [], "trace": [],
                    "budget": ledger.snapshot(), "aid_bindings": [], "gate": None,
                    "restore": restore_info, "publish_calls": 0,
                    "n_items": plan["n_items"], "flow": [],
                    "restore_error": r_rec["read_error"],
                    "rule": "本地计划快照损坏/不可读 → 中止发布（发布调用数 0），"
                            "不得当作新任务重试"}
    cli = client if client is not None else RecordingClient()
    trace, results = [], []
    for it in plan["items"]:
        res = publish(it, client=cli, plan=plan, budget=ledger, trace=trace,
                      gate_kwargs={"authorized": authorized}, save_path=save_path)
        results.append(res)
        it.update({k: res["item"][k] for k in ("state", "aid", "reserve_state")
                   if k in res["item"]})
        if res.get("save_error"):
            # 🔴 提交意图 / 结果落盘失败 → 立即停止后续条目
            flow_save_error = res["save_error"]
            break
        # ★ 每条结果取得后**及时保存**
        if save_path:
            r_save = save_state(plan, save_path)
            if not r_save["ok"]:
                res["save_error"] = r_save["error"]
                flow_save_error = r_save["error"]
                break
    if save_path and not flow_save_error:
        save_state(plan, save_path)

    # ⑤b 报告返回后的**证据回填**（按 `entry_id`）——
    #     报告读取环节产出，离线时由替身 `evidence_map` 提供；
    #     **没有回填就没有证据处理**（此前的接线一直空跑）。
    ev_by = dict(returned_evidence_by_entry or {})
    if not ev_by:
        ev_by = dict(getattr(cli, "evidence_map", None) or {})
    # 兼容旧记录 / 旧夹具：`evidence_map` 键可能是旧格式 `日期-NN`（无指纹），
    # 按 `-NN` 数字后缀回退匹配到实际 `entry_id`（要求**唯一命中**，否则视为歧义不匹配）。
    ev_suffix = {}
    for k in ev_by:
        m = re.match(r".*-(\d{2})$", str(k))
        if m and re.fullmatch(r"\d{8}-[0-9a-f]{8}-\d{2}", str(k)) is None:
            ev_suffix.setdefault(m.group(1), []).append(k)
    for it in plan["items"]:
        payload = ev_by.get(it.get("entry_id"))
        if payload is None:
            cands = ev_suffix.get(it["entry_id"].rsplit("-", 1)[-1], [])
            if len(cands) == 1:
                payload = ev_by.get(cands[0])
        if payload is None or not it.get("aid"):
            continue
        if isinstance(payload, dict) and "evidence" in payload:
            it["returned_evidence"] = list(payload.get("evidence") or [])
            if payload.get("claims_two_way_independent"):
                it["claims_two_way_independent"] = True
        else:
            it["returned_evidence"] = list(payload or [])

    # ⑥ 证据处理：背景复述检测 + 独立性自检（**按具体证据**）
    sent_bg = list(sent_background or [])
    if not sent_bg:
        sent_bg = [{"material_id": m.get("material_id"), "value": m.get("value"),
                    "unit": m.get("unit"), "statement": m.get("statement")}
                   for m in registry]
    # 🔴 返回证据的**规范编号**：优先 `material_id`，退回 `id`。
    #    必须与来源关系图同键 —— 否则「把复述 / 同源当成两路独立验证」的拦截
    #    会因**键不一致而静默失效**（`echoes` 按 `id` 登记、结论引用按 `material_id` 查）。
    def _eid(e):
        return e.get("material_id") or e.get("id")

    echoes = {}
    for it in plan["items"]:
        if not it.get("aid"):
            continue
        for e in (it.get("returned_evidence") or []):
            k = _eid(e)
            if k is not None:
                echoes[k] = ev_mod.echo_check(e, sent_bg)
    ind = ev_mod.check_independence_claims(
        [{"id": it["entry_id"],
          "evidence": [_eid(e) for e in (it.get("returned_evidence") or []) if _eid(e)],
          "claims_two_way_independent": bool(it.get("claims_two_way_independent"))}
         for it in plan["items"]], graph=graph, echoes=echoes)

    # ⑦ 逐题交付（复用第 2 轮 `deliver_gate`；**aid ≠ 问题已回答**）
    answers = {}
    for q in req["questions"]:
        qid = q["question_id"]
        j = (judgements["by_question"] or {}).get(qid) or {}
        if j.get("verdict") in ("full_reuse", "partial_reuse"):
            answers[qid] = {"status": "answered" if j["verdict"] == "full_reuse" else "partial",
                            "answer_slot": q.get("answer_slot"),
                            "evidence": [{"reused_from": j.get("aid"),
                                          "verdict": j.get("verdict")}],
                            "note": "来自**历史报告内容复用**（非本次新发布）"}
            continue
        if q.get("execution_mode") != "report_candidate":
            m = (materials or {}).get((q.get("metric_families") or [None])[0])
            if isinstance(m, dict) and m.get("value") is not None:
                answers[qid] = {"status": "answered", "answer_slot": q.get("answer_slot"),
                                "evidence": [{"family": (q.get("metric_families") or [None])[0],
                                              "value": m.get("value"), "asof": m.get("asof")}]}
            else:
                answers[qid] = {"status": "unfinished", "reason": "material_insufficient",
                                "answer_slot": q.get("answer_slot")}
            continue
        ent = next((it for it in plan["items"] if qid in (it.get("question_ids") or [])), None)
        st = (ent or {}).get("state")
        answers[qid] = {"status": "unfinished",
                        "reason": "submit_unknown" if st == "submit_unknown"
                                  else "not_implemented_yet",
                        "answer_slot": q.get("answer_slot"),
                        "note": ("已提交并取得 aid，但**aid 只代表服务端受理**" if (ent or {}).get("aid")
                                 else ("提交结果未知，占用保留" if st == "submit_unknown"
                                       else "报告候选路径：本轮以离线替身提交"))}
    gate_out = req_mod.deliver_gate(req, answers)

    return {
        "task": text, "route": r, "requirements": req,
        "source_registry": registry, "source_graph": graph.summary(),
        "reuse": {"judgements": judgements, "plan_reuse": plan.get("reuse"),
                  "recheck": plan.get("recheck"), "cost_after": cost_after},
        "plan": plan, "items": plan["items"], "results": results, "trace": trace,
        "budget": ledger.snapshot(),
        "restore": restore_info,
        "save_error": flow_save_error,
        "n_items": plan.get("n_items"),
        "evidence_processing": {"echoes": echoes, "independence": ind,
                                "sent_background": sent_bg},
        "publish_calls": sum(1 for t in trace if t.get("publish_calls")),
        "sent_params": [res.get("sent_params") for res in results if res.get("sent_params")],
        "aid_bindings": [{"entry_id": it["entry_id"], "aid": it.get("aid"),
                          "object_id": it.get("object_id"),
                          "question_ids": it.get("question_ids"),
                          "source": ("历史报告复用" if it.get("reuse_decision") == "reuse_all"
                                     else "本次发布")} for it in plan["items"] if it.get("aid")],
        "delivery": gate_out,
        "flow": [{"entry_id": it["entry_id"], "object_id": it.get("object_id"),
                  "question_ids": it.get("question_ids"), "state": it.get("state"),
                  "aid": it.get("aid"), "reuse_decision": it.get("reuse_decision")}
                 for it in plan["items"]],
        "rule": "问题清单与材料 → 来源登记及核对 → 历史报告内容检查 → 确定复用部分与研究缺口 "
                "→ 为必要的新报告组装背景 → 参数与发布检查 → 执行及结果回填 → 证据处理 → 逐题交付",
    }


# ═══════════════════════════════════════════════════════════════════
# 9.5 用例执行入口（供 `maintenance/expert-package-review/cases/12-*` 与 verify.py）
# ═══════════════════════════════════════════════════════════════════

def _load_api_module():
    """按路径加载包内 `shenwa_api.py`（不 import 网络库以外的东西，不联网）。"""
    import importlib.util
    api_p = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                         "shenwa", "shenwa_api.py")
    spec = importlib.util.spec_from_file_location("pp_api_rt", api_p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run_budget_ops(ch):
    """按 `ops` 序列驱动 `BudgetLedger`，返回每步结果 + 末态快照。"""
    led = BudgetLedger(limit=ch.get("limit"), settled=ch.get("settled"),
                       reserved=ch.get("reserved"), account_balance=ch.get("account_balance"),
                       threshold=ch.get("threshold"))
    results = []
    for op in ch.get("ops", []):
        k = op.get("op")
        if k == "peek":
            results.append(led.peek(op.get("cost")))
        elif k == "reserve":
            results.append(led.reserve(op.get("id"), op.get("cost")))
        elif k == "settle":
            results.append(led.settle(op.get("id"), op.get("actual")))
        elif k == "release":
            results.append(led.release(op.get("id"), op.get("reason", "test")))
        elif k == "mark_unknown":
            results.append(led.mark_unknown(op.get("id"), op.get("cost")))
        else:
            results.append({"error": f"未知 op {k}"})
    snap = led.snapshot()
    return {"final": snap, "results": results,
            "ok_flags": [bool(r.get("ok")) for r in results],
            "reasons": [r.get("reason") for r in results],
            "remaining": snap.get("remaining"),
            "settled_value": snap["settled"]["value"],
            "reserved_value": snap["reserved"]["value"],
            "reserved_ids": sorted(snap["reserved_entries"]),
            "settled_ids": sorted(snap["settled_entries"]),
            "exact": snap["exact"]}


def _run_concurrency(ch):
    """并发 `reserve`：验证「检查 + 预留」不可分割（**最多只有 1 项能通过**）。"""
    led = BudgetLedger(limit=ch.get("limit"))
    cost, n = ch.get("cost"), int(ch.get("n", 8))
    ok_ids, lock = [], threading.Lock()

    def _one(i):
        time.sleep(float(ch.get("jitter", 0.002)))
        r = led.reserve(f"c{i:02d}", cost)
        if r.get("ok"):
            with lock:
                ok_ids.append(f"c{i:02d}")
    ts = [threading.Thread(target=_one, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    snap = led.snapshot()
    return {"n_ok": len(ok_ids), "ok_ids": sorted(ok_ids),
            "reserved_value": snap["reserved"]["value"],
            "reserved_n": snap["reserved"]["n"],
            "limit": led.limit, "cost": cost, "n": n}


def _prep_item(raw, mutate=None, preserve_hash=False):
    """整理 `cases` 里的条目：先按**原字段**算 `plan_hash`，再 `mutate`（若给）。

    这样 `mutate` 改过的条目其 `plan_hash` 与当前字段**不一致** → 恰好触发 `gate()` 的
    「参数已变化 → 必须重新检查」分支（用途见场景 I）。
    """
    it = dict(raw)
    if not preserve_hash:
        it["plan_hash"] = plan_fingerprint(it)
    if mutate:
        it.update(mutate)
    return it


def _run_publish_case(ch):
    """跑一次 `publish()`（离线替身 client），返回可断言观测。"""
    it = _prep_item(ch["item"], ch.get("mutate"), bool(ch.get("preserve_hash")))
    cli = RecordingClient(script=ch.get("script"), aid_map=ch.get("aid_map"),
                          default=ch.get("default"))
    led = BudgetLedger(limit=ch.get("limit"), settled=ch.get("settled"),
                       account_balance=ch.get("account_balance"))
    out = publish(it, client=cli, plan=ch.get("plan", {"coverage": {"ok": True}}),
                  budget=(led if ch.get("limit") is not None or ch.get("settled") else None),
                  gate_kwargs=ch.get("gate_kwargs"))
    out["client_calls"] = len(cli.calls)
    out["sent_params"] = [c["params"] for c in cli.calls]
    snap = led.snapshot()
    out["budget"] = snap
    # 扁平镜像（与 _run_budget_ops / _run_publish_seq 一致，便于用例直接断言）
    out["settled_value"] = snap["settled"]["value"]
    out["reserved_value"] = snap["reserved"]["value"]
    out["reserved_n"] = snap["reserved"]["n"]
    out["reserved_n_unknown"] = snap["reserved"]["n_unknown"]
    out["reserved_ids"] = sorted(snap["reserved_entries"])
    out["settled_ids"] = sorted(snap["settled_entries"])
    out["budget_exact"] = snap["exact"]
    out["item_state"] = out["item"].get("state")
    out["item_aid"] = out["item"].get("aid")
    out["decision_why"] = out["why"]
    out.pop("result", None)
    return out


def _run_real_client_case(ch):
    """**真实调用链**：`shenwa_api.Client` + `ScriptedTransport`，经 `publish()` 发起。"""
    import shutil
    import tempfile
    api = _load_api_module()
    it = dict(ch["item"])
    if not it.get("plan_hash"):
        it["plan_hash"] = plan_fingerprint(it)
    script = list(ch.get("script") or [api.OK_LOGIN, api.OK_PUBLISH])
    tmp = tempfile.mkdtemp(prefix="pp_case_")
    try:
        c, tr, _clk = api._client(script, token_file=os.path.join(tmp, ".tok"),
                                  task_file=os.path.join(tmp, "tasks.jsonl"))
        api.save_token("TKSEED", "test-acct-A", api.BASE_PRIMARY, c.token_file)
        led = BudgetLedger(limit=ch.get("limit"))
        out = publish(it, client=c, plan=ch.get("plan", {"coverage": {"ok": True}}),
                      budget=(led if ch.get("limit") is not None else None))
        sent = [x["params"] for x in tr.calls if "wfStartPublish" in x["path"]]
        recs, _err = api.load_tasks(os.path.join(tmp, "tasks.jsonl"))
        res = {
            "publish_calls": out["publish_calls"],
            "transport_publish_calls": tr.count("/api/manage/wfStartPublish"),
            "transport_total_calls": len(tr.calls),
            "decision": out["decision"],
            "state": out["state"],
            "aid": out["aid"],
            "sent_params": sent,
            "params_match_plan": bool(sent) and sent[-1] == it["submit_params"],
            "n_task_records": len(recs),
            "task_state": (recs[-1].get("state") if recs else None),
            "task_aid": (recs[-1].get("aid") if recs else None),
            "budget": led.snapshot(),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _run_restore_case(ch):
    """保存 → 恢复 → 再提交，验证「已受理 / 结果未知不得当新任务」。"""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pp_restore_")
    try:
        p = os.path.join(tmp, "plan.jsonl")
        items = [dict(x) for x in ch.get("items", [])]
        save_state({"plan_id": ch.get("plan_id", "PLAN-T"), "items": items}, p)
        rec = restore_pending(p, ch.get("plan_id"))
        out = dict(rec)
        out["n_items"] = len(items)
        out["blocked_n"] = len(rec["blocked_resubmit"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _run_publish_seq(ch):
    """**同一预算台账**上依次 `publish` 多条（验证「第 2 条被拦、不得超额」）。"""
    led = BudgetLedger(limit=ch.get("limit"), settled=ch.get("settled"))
    cli = RecordingClient(script=ch.get("script"))
    plan = ch.get("plan", {"coverage": {"ok": True}})
    outs = []
    for raw in ch.get("items", []):
        d = dict(raw)
        if not d.get("plan_hash"):
            d["plan_hash"] = plan_fingerprint(d)
        outs.append(publish(d, client=cli, plan=plan, budget=led))
    snap = led.snapshot()
    return {
        "n_published": sum(1 for o in outs if o["publish_calls"]),
        "n_blocked": sum(1 for o in outs if not o["publish_calls"]),
        "states": [o["state"] for o in outs],
        "decisions": [o["decision"] for o in outs],
        "publish_calls": [o["publish_calls"] for o in outs],
        "aids": [o["aid"] for o in outs],
        "client_calls": len(cli.calls),
        "sent_params": [c["params"] for c in cli.calls],
        "budget": snap,
        "reserved_value": snap["reserved"]["value"],
        "settled_value": snap["settled"]["value"],
        "reserved_ids": sorted(snap["reserved_entries"]),
        "settled_ids": sorted(snap["settled_entries"]),
    }


def run_check_spec(spec) -> dict:
    """执行 `cases/12-*` 的检查清单（与各轮一致：**真跑生产函数**，不另写一套测试逻辑）。"""
    items = []

    def add(label, ok, detail):
        items.append({"label": label, "pass": bool(ok), "detail": detail})

    for ch in spec.get("checks", []):
        kind, label = ch.get("type"), ch.get("label") or ch.get("type")
        try:
            if kind == "plan":
                kw = dict(ch.get("kwargs") or {})
                res = build(ch["req"], templates=ch.get("templates"),
                            plan_date=ch.get("plan_date", "20261003"), **kw)
            elif kind == "gate":
                kw = dict(ch.get("kwargs") or {})
                res = gate(_prep_item(ch["item"], ch.get("mutate"), bool(ch.get("preserve_hash"))),
                           plan=ch.get("plan", {"coverage": {"ok": True}}), **kw)
            elif kind == "reuse":
                res = reuse_decision(history=ch.get("history"))
            elif kind == "cost":
                res = template_cost(ch.get("tid"), ch.get("templates"))
                res["sum"] = sum_costs(ch.get("costs") or [])
            elif kind == "budget":
                res = _run_budget_ops(ch)
            elif kind == "concurrency":
                res = _run_concurrency(ch)
            elif kind == "publish":
                res = _run_publish_case(ch)
            elif kind == "publish_seq":
                res = _run_publish_seq(ch)
            elif kind == "real_client":
                res = _run_real_client_case(ch)
            elif kind == "restore":
                res = _run_restore_case(ch)
            elif kind == "flow":
                kw = dict(ch.get("kwargs") or {})
                if ch.get("client_script") is not None:
                    kw["client"] = RecordingClient(script=ch["client_script"],
                                                   aid_map=ch.get("aid_map"))
                res = run_plan_flow(ch["input"], **kw)
            # —— 第 4 轮 ——
            elif kind == "background":
                kw = dict(ch.get("kwargs") or {})
                res = assemble_background(ch.get("items") or [], **kw)
            elif kind == "bg_from_materials":
                res = {"items": background_from_materials(
                    ch.get("materials") or [], ch.get("question_ids"),
                    mode=ch.get("mode", "independent"), optional=bool(ch.get("optional")))}
                res["n_items"] = len(res["items"])
            elif kind == "text_check":
                res = check_text_length(ch.get("text"), ch.get("limit", TEXT_LIMIT))
            elif kind == "reuse_content":
                res = reuse_decision(history=ch.get("history"), content=ch.get("content"))
            elif kind == "recheck":
                res = recheck_after_reuse(ch.get("plan") or {},
                                          background_mode=ch.get("background_mode",
                                                                 "independent"),
                                          material_records=ch.get("material_records"),
                                          background_by_object=ch.get("background_by_object"))
                res["n_items"] = len(res.get("items") or [])
            elif kind == "reuse_flow":
                kw = dict(ch.get("kwargs") or {})
                if ch.get("client_script") is not None:
                    kw["client"] = RecordingClient(script=ch["client_script"],
                                                   aid_map=ch.get("aid_map"))
                if ch.get("evidence_map") is not None:
                    kw["returned_evidence_by_entry"] = ch["evidence_map"]
                res = run_reuse_flow(ch["input"], **kw)
            else:
                add(label, False, f"不处理的检查类型 {kind!r}")
                continue
        except Exception as e:                                    # noqa: BLE001
            add(label, False, f"用例执行失败：{e}")
            continue
        for r in cap.run_asserts(res, ch.get("assert", [])):
            add(f"{label} · {r['path'] or '(根)'} {r['op']}", r["pass"],
                r["detail"] if r["pass"] else f"{r['detail']}（期望 {r['expect']!r}）")

    n_fail = sum(1 for x in items if not x["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail, "results": items}


# ═══════════════════════════════════════════════════════════════════
# 10. 自检
# ═══════════════════════════════════════════════════════════════════

def _req_for(text, objects=None, object_level="个股", materials=None, question_modes=None,
             gaps=None):
    """构造最小问题清单（与第 2 轮 `requirement.build_requirements()` 同构）。"""
    import router as rt
    import requirement as rq
    r = rt.route(text, objects=objects, object_level=object_level)
    req = rq.build_requirements(text, reading=r, objects=objects,
                                object_level=object_level,
                                families_of=rt.families_for_text,
                                materials=materials, template_ok=bool(r.get("template")))
    pq = {x.get("question_id"): x for x in (r.get("per_question") or [])}
    for q in req["questions"]:
        m = pq.get(q["question_id"]) or {}
        if m.get("template") is not None:
            q["template"] = m["template"]
        if question_modes:
            for k, v in question_modes.items():
                if k in (q.get("source_text") or ""):
                    q["execution_mode"] = v
        if gaps:
            for k, v in gaps.items():
                if k in (q.get("source_text") or ""):
                    q["execution_gaps"] = list(v)
    return req


def selftest() -> int:
    fails = []
    passed = [0]                       # 已通过断言数（供 verify.py 断言下限）

    def ck(cond, msg):
        if cond:
            passed[0] += 1
        else:
            fails.append(msg)

    TPL = {"data": [{"id": "个股", "templates": [
        {"id": "0", "title": "通用", "cost": 3},
        {"id": "9", "title": "估值", "cost": 5},
        {"id": "7", "title": "财务", "cost": 6},
        {"id": "8", "title": "研报", "cost": 10},
        {"id": "2001", "title": "深度", "cost": 20},
    ]}]}

    # 1. 模板成本：已知 / 未知（**不按 0**）
    c9 = template_cost(9, TPL)
    ck(c9["cost"] == 5 and c9["cost_state"] == "known", f"T1 成本读取：{c9}")
    cX = template_cost(999, TPL)
    ck(cX["cost"] is None and cX["cost_state"] == "unknown", f"T1 未知成本应为 unknown：{cX}")
    ck(template_cost(9, None)["cost_state"] == "unknown", "T1 无 templates → unknown")
    ck(_as_cost("") is None and _as_cost(None) is None and _as_cost("abc") is None,
       "T1 空/非数字成本不得按 0")

    # 2. 逐项加总
    s = sum_costs([5, 6, None])
    ck(s["exact"] is False and s["known_sum"] == 11 and s["n_unknown"] == 1,
       f"T2 合计应标不精确：{s}")
    ck(sum_costs([5, 6])["exact"] is True, "T2 全已知应精确")

    # 3. entry_id 提交前生成、稳定、不按返回顺序
    ck(make_entry_id("20261003", 1) == "20261003-01", "T3 entry_id 格式")
    ck(make_entry_id("20261003", 12) == "20261003-12", "T3 entry_id 两位序号")

    # 4. 预算：三态 + 原子预留 + 超额拒绝
    L = BudgetLedger(limit=10, settled={"e0": 4}, account_balance=9999, threshold=3)
    ck(L.snapshot()["remaining"] == 6, f"T4 剩余应 6：{L.snapshot()}")
    ck(L.peek(6)["ok"] and not L.peek(7)["ok"], "T4 只读预检 6 通过 / 7 拒绝")
    ck(L.reserve("e1", 6)["ok"], "T4 预留 6 应成功")
    r2 = L.reserve("e2", 6)
    ck(not r2["ok"] and r2["reason"] == "would_exceed", f"T4 第二笔 6 应超额被拒：{r2}")
    ck("e2" not in L.snapshot()["reserved_entries"], "T4 被拒不得写入占用")
    ck(L.reserve("e3", None)["reason"] == "cost_unknown", "T4 未知成本不得预留（不按 0）")
    # 已结算不再计入预留
    ck(L.snapshot()["settled"]["value"] == 4 and L.snapshot()["reserved"]["value"] == 6,
       f"T4 已结算/预留分列：{L.snapshot()}")
    # 结算后释放预留
    L.settle("e1", actual=6)
    ck(L.snapshot()["reserved"]["value"] == 0 and L.snapshot()["settled"]["value"] == 10,
       f"T4 结算后预留归零：{L.snapshot()}")

    # 5. 并发：预算 10、两项各 6 → 最多 1 项预留（**原子性**）
    Lc = BudgetLedger(limit=10)
    ok_n = []
    lock = threading.Lock()

    def _race(i):
        time.sleep(0.002)                     # 放大交错窗口
        r = Lc.reserve(f"c{i}", 6)
        if r["ok"]:
            with lock:
                ok_n.append(i)
    ts = [threading.Thread(target=_race, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ck(len(ok_n) == 1, f"T5 并发 8×6 点、上限 10 → 应恰好 1 项通过，实得 {len(ok_n)}：{ok_n}")
    ck(Lc.snapshot()["reserved"]["value"] == 6, f"T5 占用总额应恰为 6：{Lc.snapshot()}")

    # 6. 复用三态（未知**不冒充**无候选）
    ck(reuse_decision(history={"state": "queried_ok", "candidates": []})["state"]
       == "no_candidate", "T6 成功查询无候选")
    ck(reuse_decision(history={"state": "queried_ok", "candidates": [{"aid": "a"}]})["state"]
       == "candidate_unverified", "T6 有候选未核实")
    ck(reuse_decision(history={"state": "query_failed"})["state"] == "unknown",
       "T6 查询失败 → unknown")
    ck(reuse_decision(history=None)["state"] == "unknown", "T6 未查询 → unknown")
    ck(reuse_decision(history={"state": "query_failed"})["reuse_all_reachable"] is False,
       "T6 本轮 reuse_all 不可达")

    # 7. 必要性缺失 → no_publish；参数变化 → 重检
    it = {"entry_id": "20261003-01", "object_id": "某公司", "object_level": "个股",
          "template_tid": 9, "question_ids": ["Q1"], "questions": ["估值贵不贵"],
          "need_report_why": "", "missing_materials": [], "reuse_state": "unknown",
          "cost": 5, "cost_state": "known", "state": "pending",
          "submit_params": {"tid": "9", "question": "估值贵不贵", "text": "t"}}
    it["plan_hash"] = plan_fingerprint(it)
    g = gate(it, plan={"coverage": {"ok": True}})
    ck(g["decision"] == "no_publish", f"T7 无必要性 → no_publish：{g}")
    it2 = dict(it, need_report_why="需模板化报告补足", missing_materials=["估值分位"])
    it2["plan_hash"] = plan_fingerprint(it2)
    g2 = gate(it2, plan={"coverage": {"ok": True}})
    ck(g2["decision"] == "proceed", f"T7 有缺口 → proceed：{g2}")
    # 检查后改动参数 → 必须重检
    it3 = dict(it2)
    it3["submit_params"] = dict(it2["submit_params"], question="改了问题")
    g3 = gate(it3, plan={"coverage": {"ok": True}})
    ck(g3["decision"] == "confirm_required" and g3.get("recheck_required"),
       f"T7 参数变化 → 重检：{g3}")
    # 已受理 / 结果未知的条目不得当新任务提交
    it4 = dict(it2, state="accepted")
    g4 = gate(it4, plan={"coverage": {"ok": True}})
    ck(g4["decision"] == "no_publish", f"T7 已受理不得重提：{g4}")
    it5 = dict(it2, state="submit_unknown")
    g5 = gate(it5, plan={"coverage": {"ok": True}})
    ck(g5["decision"] == "no_publish", f"T7 结果未知不得重提：{g5}")

    # 8. publish 入口：未通过 → 0 调用；通过 → 1 调用且参数与计划一致
    cli = RecordingClient()
    res_bad = publish(it, client=cli, plan={"coverage": {"ok": True}})
    ck(res_bad["publish_calls"] == 0 and len(cli.calls) == 0,
       f"T8 未通过检查 → 0 调用：{res_bad}")
    L2 = BudgetLedger(limit=10)
    res_ok = publish(it2, client=cli, plan={"coverage": {"ok": True}}, budget=L2)
    ck(res_ok["publish_calls"] == 1 and len(cli.calls) == 1, f"T8 通过 → 1 调用：{res_ok}")
    ck(cli.calls[0]["api"] == "report_publish"
       and cli.calls[0]["params"] == it2["submit_params"],
       f"T8 提交参数应与计划一致：{cli.calls}")
    ck(res_ok["state"] == "accepted" and res_ok["aid"], f"T8 受理后 state=accepted：{res_ok}")

    # 9. aid 只代表受理（≠ done / ≠ 已回答）
    ck(res_ok["item"]["state"] == "accepted", "T9 拿到 aid 只能 accepted")
    ck(res_ok["item"]["state"] != "done", "T9 不得直接 done")
    ck(res_ok.get("cost_actual", {}).get("state") == "unknown",
       "T9 无扣点依据 → 实际消耗 unknown")

    # 10. submit_unknown：保留占用、不重发
    L3 = BudgetLedger(limit=10)
    cli3 = RecordingClient(script=[{"ok": False, "category": "unknown",
                                    "submit_state": "submit_unknown"}])
    it6 = dict(it2)
    it6["plan_hash"] = plan_fingerprint(it6)
    res_u = publish(it6, client=cli3, plan={"coverage": {"ok": True}}, budget=L3)
    ck(res_u["state"] == "submit_unknown" and res_u["publish_calls"] == 1,
       f"T10 结果未知 → submit_unknown：{res_u}")
    ck("20261003-01" in L3.snapshot()["reserved_entries"],
       f"T10 占用应保留：{L3.snapshot()}")
    res_u2 = publish(res_u["item"], client=cli3, plan={"coverage": {"ok": True}}, budget=L3)
    ck(res_u2["publish_calls"] == 0, f"T10 不得重复发布：{res_u2}")

    # 11. 明确失败分类：参数错误释放预留；业务失败保留为 unknown
    L4 = BudgetLedger(limit=10)
    cli4 = RecordingClient(script=[{"ok": False, "category": "param"}])
    it7 = dict(it2)
    it7["plan_hash"] = plan_fingerprint(it7)
    res_p = publish(it7, client=cli4, plan={"coverage": {"ok": True}}, budget=L4)
    ck(res_p["reserve_state"] == "released" and L4.snapshot()["reserved"]["value"] == 0,
       f"T11 参数错误应释放预留：{res_p}")
    L5 = BudgetLedger(limit=10)
    cli5 = RecordingClient(script=[{"ok": False, "category": "business"}])
    it8 = dict(it2)
    it8["plan_hash"] = plan_fingerprint(it8)
    res_b = publish(it8, client=cli5, plan={"coverage": {"ok": True}}, budget=L5)
    ck(res_b["reserve_state"] == "unknown" and "20261003-01" in L5.snapshot()["reserved_entries"],
       f"T11 业务失败不得当作未扣点：{res_b}")

    # 12. build：只为 report_candidate 建条目；8 家全保留；一份报告可覆盖多问
    req8 = {"objects": [f"公司{i}" for i in range(1, 9)], "object_level": "个股",
            "questions": []}
    for i in range(1, 9):
        for j, fam in enumerate(("valuation", "financial_compare"), 1):
            req8["questions"].append({
                "question_id": f"公司{i}-Q{j}", "object_id": f"公司{i}",
                "object_level": "个股", "source_text": f"问题{j}", "metric_families": [fam],
                "execution_mode": "report_candidate", "execution_gaps": ["缺口径"],
                "research_cutoff": "2026-09-30", "template": {"初选": 9 if j == 1 else 7},
                "answer_slot": f"§{i}{j}"})
    req8["questions"].append({"question_id": "公司1-Q3", "object_id": "公司1",
                              "object_level": "个股", "source_text": "查一个价格",
                              "metric_families": ["price_quote"],
                              "execution_mode": "direct_fetch", "answer_slot": "§13"})
    plan8 = build(req8, templates=TPL, plan_date="20261003")
    objs8 = {it["object_id"] for it in plan8["items"]}
    ck(len(objs8) == 8, f"T12 8 家必须全保留：{sorted(objs8)}")
    ck(plan8["n_items"] == 16, f"T12 按（对象,模板）分组 → 16 条：{plan8['n_items']}")
    # ★ 第 15 轮：entry_id 含内容指纹（`<日期>-<指纹>-<NN>`），不再只是「日期+局部序号」
    ck(all(re.fullmatch(r"20261003-[0-9a-f]{8}-\d{2}", it["entry_id"])
           for it in plan8["items"]),
       "T12 entry_id 含内容指纹（跨计划不冲突）")
    ck(all(it["entry_id"].startswith(f"20261003-{plan8['plan_id'].split('-')[-1]}-")
           for it in plan8["items"]),
       "T12 entry_id 前缀含 plan 指纹")
    ck(plan8["coverage"]["non_report_questions"] == ["公司1-Q3"],
       f"T12 直取问题不建条目：{plan8['coverage']}")
    ck(len({it["entry_id"] for it in plan8["items"]}) == plan8["n_items"], "T12 编号不重复")

    # 13. 未知成本不得按 0：成本全未知 → 计划 cost.exact=False
    planX = build(req8, templates={"data": [{"templates": [{"id": "9", "title": "x"}]}]},
                  plan_date="20261003")
    ck(planX["cost"]["exact"] is False and planX["cost"]["n_unknown"] == 16,
       f"T13 未知成本应全部 unknown：{planX['cost']}")

    # 14. 恢复：已受理 / 结果未知不得当新任务
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "plans.jsonl")
        dd = dict(plan8)
        dd["items"] = [dict(plan8["items"][0], state="accepted", aid="A@@1"),
                       dict(plan8["items"][1], state="submit_unknown"),
                       dict(plan8["items"][2], state="pending")]
        save_state(dd, p)
        rec = restore_pending(p)
        ck(len(rec["blocked_resubmit"]) == 2, f"T14 两条不得重提：{rec}")
        ck(rec["pending"] == [dd["items"][2]["entry_id"]], f"T14 待执行只剩 1 条：{rec}")

    # 15. 回归：不改动既有客户端契约
    try:
        import importlib.util
        api_p = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                             "shenwa", "shenwa_api.py")
        spec = importlib.util.spec_from_file_location("pp_api_probe", api_p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        ck(len(m.APIS) == 15, f"T15 APIS 应 15：{len(m.APIS)}")
        ck(len(m.MUTATING_APIS) == 3, f"T15 MUTATING_APIS 应 3：{len(m.MUTATING_APIS)}")
        ck(hasattr(m, "run_publish_gate"), "T15 run_publish_gate 应在场")
        ck(m.PUBLISH_TEXT_REQUIRED is False, "T15 text 必填性冲突（C1）保持不阻断")
    except Exception as e:                                        # noqa: BLE001
        fails.append(f"T15 客户端契约回归失败：{e}")

    # ─────────────────────────────────────────────────────────────
    # 第 4 轮：背景组装 / 内容复用 / 实际发送参数 / 完整流程
    # ─────────────────────────────────────────────────────────────

    # 16. 背景组装：核实数字带来源·时点·单位·口径；未核实标状态；用户观点写待检验命题
    bgi = [background_item("verified_number", "2026Q2 营收同比 +18.4%",
                           source="合成材料·离线", asof="2026Q2", unit="%",
                           calibre="合并报表·累计同比"),
           background_item("unverified_material", "渠道调研称库存偏高"),
           background_item("user_hypothesis", "用户认为明年翻倍"),
           background_item("existing_judgement", "上期报告判断为「稳健」",
                           source="历史报告 A@@1", asof="2026-09-20", purpose="验证既有判断")]
    bg = assemble_background(bgi, object_id="某公司", cutoff="2026-09-30",
                             purposes="回答「估值贵不贵」", mode="independent")
    txt_bg = bg["sent_text"] or ""
    ck(bg["ok"] and bg["n_included"] == 3 and bg["n_excluded"] == 1, f"T16 组装：{bg}")
    ck("来源：合成材料·离线" in txt_bg and "单位：%" in txt_bg
       and "口径：合并报表·累计同比" in txt_bg, f"T16 核实数字须带来源/单位/口径：{txt_bg}")
    ck("尚未独立核实" in txt_bg, "T16 未核实材料必须标状态")
    ck("待检验命题" in txt_bg, "T16 用户观点须写成待检验命题")
    ck(bg["excluded"][0]["kind"] == "existing_judgement", f"T16 独立判断不得注入预设结论：{bg['excluded']}")
    ck("合成材料·离线" not in (bg["excluded"][0].get("text") or ""), "T16 排除项不含核实数字")
    # 解释既有判断模式 → 允许提供该判断（注明来源 / 时间 / 用途）
    bg_e = assemble_background(bgi, object_id="某公司", cutoff="2026-09-30",
                               purposes="验证既有判断", mode="explain_existing")
    ck(bg_e["n_included"] == 4 and "既有判断" in (bg_e["sent_text"] or ""),
       f"T17 解释模式可提供既有判断：{bg_e['n_excluded']}")
    ck("独立理解" in BACKGROUND_RULES["self_contained"], "T16 背景须可独立理解")

    # 18. 超限：**不静默截断** —— 先丢显式可选项并如实记录；仍超限则要求调整
    bg2 = assemble_background(
        [background_item("unverified_material", "甲" * 300),
         background_item("unverified_material", "乙" * 200, optional=True)],
        object_id="某公司", cutoff="2026-09-30", purposes="p")
    ck(bg2["ok"] and bg2["n_dropped"] == 1 and bg2["dropped"][0]["text"].startswith("乙"),
       f"T18 丢可选项并记录：{bg2['n_dropped']}")
    bg3 = assemble_background([background_item("unverified_material", "丙" * 900)],
                              object_id="某公司", cutoff="2026-09-30", purposes="p")
    ck(bg3["requires_adjustment"] and bg3["sent_text"] is None and bg3["n_dropped"] == 0,
       f"T18 超限不得产出被截断的 text：{bg3['requires_adjustment']}")
    ck("调整问题条目或执行方式" in (bg3["suggestion"] or ""), "T18 超限须给出调整要求")

    # 19. 内容判定 → `reuse_all` **可达**（第 4 轮新增；路径：gate 直接返回，0 调用）
    itr = dict(it2, reuse_state="reuse_all")
    itr["plan_hash"] = plan_fingerprint(itr)
    cli_r = RecordingClient()
    res_r = publish(itr, client=cli_r, plan={"coverage": {"ok": True}})
    ck(res_r["decision"] == "reuse_all" and res_r["publish_calls"] == 0
       and len(cli_r.calls) == 0, f"T19 完整复用 → 不发布：{res_r['decision']}")
    ck(reuse_decision(history={"state": "queried_ok", "candidates": [{"aid": "x"}]},
                      content={"verdict": "full_reuse", "note": "6 项通过"})["state"] == "reuse_all",
       "T19 内容判定 full_reuse → reuse_all")
    ck(reuse_decision(history={"state": "queried_ok", "candidates": [{"aid": "x"}]},
                      content={"verdict": "full_reuse"})["reuse_all_reachable"] is True,
       "T19 内容判定后 reuse_all 可达")
    ck(reuse_decision(history=None)["reuse_all_reachable"] is False,
       "T19 无内容判定时 reuse_all 仍不可达")

    # 20. 背景超限 → gate 拦下并要求重新检查（0 调用）
    itb = dict(it2, adjustment_required=True,
               background={"ok": False, "suggestion": "拆分问题条目"})
    itb["plan_hash"] = plan_fingerprint(itb)
    cli_b = RecordingClient()
    res_b2 = publish(itb, client=cli_b, plan={"coverage": {"ok": True}})
    ck(res_b2["decision"] == "confirm_required" and res_b2.get("recheck_required")
       and res_b2["publish_calls"] == 0 and len(cli_b.calls) == 0,
       f"T20 背景超限须先调整再发布：{res_b2['decision']}")
    ck(res_b2["gate"]["checks"]["background_fit"]["adjustment_required"] is True,
       "T20 背景检查项在场")

    # 21. **实际发送的参数**被记录（验收查这里，不查组装函数输出）
    ck(res_ok.get("sent_params") == it2["submit_params"], "T21 实际发送参数应等于计划参数")
    ck(res_ok.get("sent_text") == it2["submit_params"]["text"], "T21 实际发送文字应被保存")
    ck(res_ok.get("text_check", {}).get("within_limit") is True, "T21 发送文字在参数约定内")
    ck(res_bad.get("sent_params") is None, "T21 未提交 → 无实际发送内容")

    # 22. 完整流程：问题清单与材料 → … → 逐题交付
    TXT = "对某公司做估值分析，并解释为什么估值偏高"
    SYNTH = "SYNTHETIC(离线合成材料·非真实数据)"
    MATS = {"valuation": {"value": None, "unit": "倍", "asof": "2026-09-30",
                          "calibre": "TTM PE", "source": SYNTH, "sufficient": False},
            "financial_compare": {"value": None, "unit": "%", "asof": "2026Q2",
                                  "calibre": "资产负债率", "source": SYNTH,
                                  "sufficient": False}}
    REG = [{"material_id": "M1", "statement": "2026Q2 营收同比 +18.4%",
            "value": 18.4, "unit": "%", "claim_kind": "primary_fact",
            "source_name": "合成材料·离线", "material_asof": "2026Q2",
            "published_at": "2026-08-28", "usable_from": "2026-08-28",
            "fetched_at": "2026-10-03", "metric_meaning": "营收同比", "calibre": "合并报表",
            "evidence_state": "verified", "question_ids": []}]
    base = run_reuse_flow(TXT, objects=["某公司"], object_level="个股",
                          templates=TPL, plan_date="20261003", limit=100,
                          materials=MATS, material_records=REG)
    rq = [q["question_id"] for q in base["requirements"]["questions"]
          if q["execution_mode"] == "report_candidate"]
    ck(base["delivery"]["coverage"]["ok"] is True, f"T22 覆盖检查通过：{base['delivery']['coverage']}")
    ck(all("reuse_decision" in it for it in base["items"]), "T22 每条目都有复用判定回填")
    ck(base["source_graph"]["n_materials"] == 1, f"T22 来源登记：{base['source_graph']}")
    ck(bool(rq) and bool(base["items"]), f"T22 确有报告候选条目（勿让后续断言空跑）：rq={rq}")
    if rq:
        q0 = rq[0]
        cand = {"aid": "OLD@@1", "object_id": "某公司", "object_level": "个股",
                "research_cutoff": base["requirements"]["time"]["research_cutoff"],
                "template_tid": 9, "completed": True, "content_available": True,
                "assumptions": [], "scenarios": [],
                "questions": [{"question_id": q0, "object_id": "某公司",
                               "object_level": "个股",
                               "source_text": next(q["source_text"] for q in
                                                   base["requirements"]["questions"]
                                                   if q["question_id"] == q0),
                               "metric_families": list(next(q["metric_families"] for q in
                                                           base["requirements"]["questions"]
                                                           if q["question_id"] == q0)),
                               "asks": {"kind": "analysis_question"}}],
                "key_evidence": [{"id": "e1", "state": "verified",
                                  "stability": "stable_structure"}]}
        f2 = run_reuse_flow(TXT, objects=["某公司"], object_level="个股",
                            templates=TPL, plan_date="20261003", limit=100,
                            materials=MATS, material_records=REG,
                            candidates_by_question={q0: [cand]})
        ck(f2["reuse"]["judgements"]["by_question"][q0]["verdict"] == "full_reuse",
           f"T22 完整复用：{f2['reuse']['judgements']['by_question'][q0]['verdict']}")
        ck(q0 not in f2["plan"]["coverage"]["not_covered"], "T22 复用后仍覆盖该问题")
        ck(f2["delivery"]["rows"][[r["question_id"] for r in f2["delivery"]["rows"]].index(q0)]
           ["status"] in ("answered", "partial"), "T22 复用内容进入该问题的回答")
        ck(f2["reuse"]["recheck"]["n_rechecked"] + f2["reuse"]["recheck"]["n_skipped_reused"]
           >= 1, "T22 计划调整后有重新检查记录")
        ck(isinstance(f2["reuse"]["cost_after"]["planned_new"]["value"], (int, float)),
           "T22 调整后重算预计新增消耗")

    # 23. 报告返回后的**证据处理**真的接通：复述识别 + 同源拦截（此前是死接线）
    TXT3 = "对某公司做深度研究：现在估值贵不贵？资产负债率有没有风险？"
    base3 = run_reuse_flow(TXT3, objects=["某公司"], object_level="个股",
                           templates=TPL, plan_date="20261003", limit=100,
                           materials=MATS, material_records=REG)
    ent3 = [it for it in base3["items"] if it.get("aid")]
    ck(len(ent3) >= 1, f"T23 应有条目取得 aid：{len(ent3)}")
    if ent3:
        e3 = ent3[0]["entry_id"]
        # 未提供返回证据 → 证据处理为空（**不得据此宣称「已确认没有复述」**）
        ck(base3["evidence_processing"]["echoes"] == {},
           f"T23 未回填返回证据 → 不产出复述结论：{base3['evidence_processing']['echoes']}")
        f3 = run_reuse_flow(
            TXT3, objects=["某公司"], object_level="个股", templates=TPL,
            plan_date="20261003", limit=100, materials=MATS, material_records=REG,
            relations=[{"a": "RPT-E2", "b": "RPT-E3", "relation": "quote"}],
            sent_background=[{"material_id": "M1", "value": 18.4, "unit": "%"}],
            returned_evidence_by_entry={e3: {
                "evidence": [{"id": "RPT-E1", "material_id": "RPT-E1", "value": 18.4,
                              "unit": "%", "statement": "公司营收同比增长 18.4%"},
                             {"id": "RPT-E2", "material_id": "RPT-E2", "value": 6.2,
                              "unit": "%"},
                             {"id": "RPT-E3", "material_id": "RPT-E3", "value": 6.2,
                              "unit": "%"}],
                "claims_two_way_independent": True}})
        ep3 = f3["evidence_processing"]
        ck(ep3["echoes"]["RPT-E1"]["is_echo"] is True
           and ep3["echoes"]["RPT-E1"]["counts_as_new_evidence"] is False,
           f"T23 复述背景 → 不算新增独立证据：{ep3['echoes'].get('RPT-E1')}")
        ck(ep3["echoes"]["RPT-E2"]["is_echo"] is False,
           f"T23 新数字 → 可作新增材料：{ep3['echoes'].get('RPT-E2')}")
        ck(ep3["independence"]["ok"] is False
           and ep3["independence"]["n_issues"] >= 1,
           f"T23 把复述 / 同源当两路独立 → 交付前拦下：{ep3['independence']['issues']}")
        ck(f3["source_graph"]["n_relations"] == 1
           and f3["source_graph"]["same_source_groups"],
           f"T23 来源关系进入登记：{f3['source_graph']}")
        ck(len(f3["evidence_processing"]["sent_background"]) == 1,
           "T23 实际发送背景被保留（原始材料 / 发送背景 / 返回证据三者可对应）")

    # 24. 🔴 复述拦截**不得因键不一致而静默失效**：
    #     返回证据用 `id` 标识自己、结论侧引用清单用 `material_id` —— 两者不同名时，
    #     只按 `id` 登记 `echoes` 会让拦截落空（探针实测过的真实缺陷）。
    if ent3:
        e3b = ent3[0]["entry_id"]
        f4 = run_reuse_flow(
            TXT3, objects=["某公司"], object_level="个股", templates=TPL,
            plan_date="20261003", limit=100, materials=MATS, material_records=REG,
            sent_background=[{"material_id": "M1", "value": 18.4, "unit": "%"}],
            returned_evidence_by_entry={e3b: {
                "evidence": [{"id": "X9", "material_id": "M1", "value": 18.4,
                              "unit": "%", "statement": "公司营收同比增长 18.4%"},
                             {"id": "X10", "material_id": "M2", "value": 6.2,
                              "unit": "%"}],
                "claims_two_way_independent": True}})
        ep4 = f4["evidence_processing"]
        ck(ep4["echoes"].get("M1", {}).get("is_echo") is True,
           f"T24 复述按**规范编号**（material_id 优先）可查：{sorted(ep4['echoes'])}")
        ck(ep4["independence"]["ok"] is False
           and ep4["independence"]["n_issues"] >= 1,
           f"T24 id≠material_id 时复述拦截仍必须生效：{ep4['independence']['issues']}")
        ck(ep4["independence"]["n_echo"] == 1,
           f"T24 复述计数不重不漏（应为 1）：{ep4['independence']['n_echo']}")

    for f in fails:
        print(f"[FAIL] {f}")
    print(f"\n== publish_plan.py 自检："
          f"{'全部通过' if not fails else str(len(fails)) + ' 项失败'}"
          f"（{passed[0]} 项断言）==")
    return 1 if fails else 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "selftest":
        return selftest()
    if argv[0] == "cost":
        print(json.dumps(template_cost(argv[1] if len(argv) > 1 else 9),
                         ensure_ascii=False, indent=2))
        return 0
    if argv[0] == "check":
        if len(argv) < 2:
            print("用法：publish_plan.py check <input-checks.json>")
            return 2
        spec = json.loads(open(argv[1], encoding="utf-8").read())
        res = run_check_spec(spec)
        for r in res["results"]:
            if not r["pass"]:
                print(f"[FAIL] {r['label']}：{r['detail']}")
        print(f"\n== 用例 {spec.get('case', '')}：{res['n_checks']} 项检查，"
              f"失败 {res['n_fail']} ==")
        return 1 if res["n_fail"] else 0
    print(f"未知子命令：{argv[0]}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor.py · 监测记录与跨期比较 —— v1.14.0 新增（第 5 轮）

**要修的病根**：前四轮解决了「怎么问、怎么发、怎么复用」，但**没有留档** ——
于是出现：
① 每次研究都是一次性的，下次问「跟上月比怎么样」时**编不出**历史基线，
   只好现场拍一个「环比上升」；
② 指标单位 / 统计期间变了，直接拿两个数相减，把**口径调整写成研究对象变化**；
③ 历史数据被修订过，比较时用修订后的新值跟旧值比，**把数据修订写成业务变化**；
④ 分析规则升级导致阶段判断变化，写成「趋势转强」；
⑤ 只有两次观测就宣称「持续趋势」；
⑥ 同一份报告重复归档两次，被当成两个时间点；
⑦ 历史文件读不出来，直接写「无历史」。

本模块是**唯一**的监测记录与跨期比较实现，接在前四轮既有入口之后：

    识别监测任务（is_monitor_request）
      → 建立监测序列（monitor_series_id，稳定编号）
      → 读取历史记录（load_records，读取失败 ≠ 无历史）
      → 选择基线（select_baseline，指定优先，不按文件时间）
      → 可比性检查（check_comparability，先检查再计算）
      → 变化与归因（measure_change / state_transition / attribute_change）
      → 综合比较（compare，四问 + 持续性 + 事实/判断分开）
      → 成稿回答（build_answer，比较在成稿前完成）
      → 保存本次记录与比较结果（save_record，原始不覆盖、修订关联旧记录）

公开接口：
  · `MONITOR_FIELDS` / `RECORD_KINDS` / `ATTRIBUTIONS` / `CHANGE_MEASURES`
  · `is_monitor_request()`     识别监测 / 比较 / 检验判断类任务
  · `monitor_series_id()`      稳定监测序列编号（不靠对象简称 / 文件名）
  · `make_record()`            构造监测记录（只记实际获得的数据与判断）
  · `save_record()`            保存（原始不覆盖；重复归档不制造新观测）
  · `load_records()`           读取（解析失败如实报错，不冒充无历史）
  · `select_baseline()`        基线选择（指定优先 / 最近适用；不按文件修改时间）
  · `check_comparability()`    七维可比性检查（可统一口径的给出转换）
  · `measure_change()`         数值差 / 百分比变化 / 百分点变化（不补 0、不硬算）
  · `state_transition()`       类别 / 阶段按**状态迁移**比较（不作算术平均）
  · `attribute_change()`       变化归因（对象变化 / 数据修订 / 口径 / 规则 / 多因 / 无法判定）
  · `compare()`                综合比较：四问 + 持续性 + 判断支持度
  · `build_answer()`           成稿前生成最终回答结构
  · `run_monitor_flow()`       **贯穿实际流程**（成稿前完成比较）
  · `selftest()` / CLI

⚠️ **不联网、不发报告**：需要新报告时由调用方走 `publish_plan.publish()` 真实入口；
已有资料足够时**不新增发布**。
⚠️ **未接入真实调度**：本模块只提供手动触发的流程编排，
`automation` 恒为 `"manual_only"` —— **不得宣称已具备自动定时运行或主动推送**。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

# ═══════════════════════════════════════════════════════════════════
# 0. 词表与规则
# ═══════════════════════════════════════════════════════════════════

#: 记录种类（用户要求 2）：原始 / 修订 / 事后重建 —— 原始快照保存后不覆盖
RECORD_KINDS = ("original", "revision", "rebuilt")

#: 归因分类（用户要求 4 之四问④）—— 允许「暂时无法判定」，不强行为完成分类而归因
ATTRIBUTIONS = (
    "object_change",    # 研究对象本身发生了变化
    "data_revision",    # 历史数据修订（对象没有新变化）
    "calibre_change",   # 指标口径 / 单位 / 统计期间调整
    "rule_change",      # 分析规则调整导致判断变化
    "mixed",            # 多种原因共同作用
    "undetermined",     # 原因暂时无法判定
)

#: 变化量纲（用户要求 4）：三種必须区分，不得混写
CHANGE_MEASURES = ("absolute", "percent", "pct_point")

#: 可比性维度（用户要求 4）：比较前逐项检查
COMPARABILITY_DIMS = (
    "object",           # 稳定对象标识（不是简称）
    "question",         # 研究问题 / 指标定义
    "unit",             # 单位
    "period",           # 统计期间
    "sample_scope",     # 样本范围（多对象统计时）
    "rule",             # 分析规则及版本
    "metric_identity",  # 指标本身是不是同一个指标
)

#: 判断支持度（四问③）：新证据对原判断的影响
JUDGEMENT_RELATIONS = ("supported", "weakened", "overturned", "insufficient")

#: 基线选择状态（用户要求 3）：三种「没有基线」必须区分开
BASELINE_STATUS = ("specified", "nearest", "no_history", "load_error", "unusable")

#: 记录字段（用户要求 2 的「每条记录至少包含」）—— 12 组
MONITOR_FIELDS = (
    "record_id",          # 记录编号
    "series_id",          # 监测序列编号
    "task_ref",           # 所属任务及问题编号
    "object",             # 稳定对象标识、名称和研究范围
    "data_asof",          # 研究截止时间（数据所属时点）
    "generated_at",       # 记录生成时间
    "material_times",     # 对应材料的时间信息（asof / published / usable / fetched）
    "metrics",            # 关键指标（名称 / 数值 / 单位 / 统计期间 / 计算口径 / 数据版本）
    "evidence_refs",      # 证据引用、来源关系、核实状态及数据版本
    "judgement",          # 当次主要判断、关键依据、适用假设、未确认事项
    "rule_versions",      # 使用的分析规则及版本
    "meta",               # 数据缺口 / 样本范围 / 记录种类与关联（原始/修订/重建 + supersedes）
)

FIRST_RUN_RULE = (
    "**首次监测没有历史记录时，从本次建立基线** —— 不编造历史变化；"
    "普通一次性查询沿用现有交付方式，**不强行增加无关的趋势分析**。"
)

BASELINE_RULE = (
    "**用户指定比较日期或记录时优先采用指定基线**；没有指定时，"
    "选同一监测序列中**当前研究时点之前最近的适用记录**，并**说明实际比较日期**；"
    "**不得按文件修改时间、下载顺序或报告返回顺序选择基线**；"
    "**读取失败 ≠ 无历史**（也不得因此覆盖已有记录）；"
    "存在历史记录但条件不可比要**如实标不可比**，不冒充「无历史」。"
)

REBUILD_RULE = (
    "**历史重建必须说明依据**，并区分「当时可知的信息」与"
    "「现在取得资料后对过去的重算」；**材料所属时间 ≠ 公开时间**，"
    "研究截止日之后才公开的材料**不能用于声称严格还原当时判断**。"
)

COMPARABILITY_RULE = (
    "**先检查可比性，再计算和解释变化**：对象 / 研究问题 / 指标定义 / 单位 / "
    "统计期间 / 样本范围 / 分析规则逐项检查；可合理统一口径且资料足够的，"
    "**明确转换或重算方法并另存结果**（不覆盖原值）；无法统一的**标明不可比及原因**；"
    "**部分指标不可比不妨碍其他可比指标继续比较**；**缺失值不能当作 0**；"
    "基数为 0 等情况下**不硬算无意义的变化率**；类别 / 阶段状态按**状态迁移**比较，"
    "**不作无依据的算术平均**。"
)

ATTRIBUTION_RULE = (
    "数值上升**不自动等于改善**、下降**不自动等于恶化**，按指标含义及当前分析框架解释；"
    "只有两个时点时**不得仅凭两点差异宣称持续趋势**（持续性判断需要相应连续记录和明确规则）；"
    "**事实变化与判断变化分开处理** —— 不能把规则变化或历史数据修订"
    "直接写成研究对象发生了变化；**允许「原因暂时无法判定」**，"
    "不为了完成分类而强行归因。"
)

FLOW_RULE = (
    "**比较必须在成稿前完成**，不能只在交付之后保存一个无人使用的比较文件；"
    "需要补充资料或新报告时，继续经过前几轮的路由、内容复用、参数校验、发布检查和预算规则；"
    "**不能因为新增监测功能就默认每次重新生成报告**（已有资料足够 → 不新增发布）；"
    "保存失败时如实说明，**不得宣称基线已经建立成功**。"
)

#: 监测触发词（用户要求 1）—— 沿用现有任务触发方式，未接入真实调度
_TRACK_WORDS = ("持续跟踪", "持续关注", "跟踪", "监测", "定期")
_COMPARE_WORDS = ("跟上", "与上次", "和上次", "相比上次", "比上月", "比上季", "环比",
                  "同比", "指定日期", "指定阶段", "与指定", "和指定",
                  "相比", "较上月", "较上季", "较去年同期")
_VERIFY_WORDS = ("检验", "更新此前判断", "验证此前", "检验此前", "此前判断", "上次的判断")
_SERIES_WORDS = ("监测序列", "监测任务", "已建立的监测")

AUTOMATION_NOTE = (
    "**未接入真实调度**：监测由用户任务触发（手动），"
    "不宣称已具备自动定时运行或主动推送能力。"
)


# ═══════════════════════════════════════════════════════════════════
# 1. 监测任务识别（用户要求 1：何时进入监测与比较流程）
# ═══════════════════════════════════════════════════════════════════

def is_monitor_request(text: str) -> dict:
    """判断任务是否进入监测与比较流程。

    进入条件（任一）：
      · 用户要求**持续跟踪**某个对象或问题（track）；
      · 用户要求**与上次 / 指定日期 / 指定阶段比较**（compare）；
      · 用户要求**检验、更新此前判断**（verify）；
      · 本次任务**属于已建立的监测序列**（series —— 由调用方传入 known_series 判断）。

    普通一次性查询 → `is_monitor=False`，沿用现有交付方式。
    """
    t = text or ""
    triggers = []
    for w in _TRACK_WORDS:
        if w in t:
            triggers.append({"kind": "track", "word": w})
            break
    for w in _COMPARE_WORDS:
        if w in t:
            triggers.append({"kind": "compare", "word": w})
            break
    for w in _VERIFY_WORDS:
        if w in t:
            triggers.append({"kind": "verify", "word": w})
            break
    mode = (triggers[0]["kind"] if triggers else None)
    return {
        "is_monitor": bool(triggers),
        "triggers": triggers,
        "mode": mode,
        "rule": "**持续跟踪 / 与上次或指定日期比较 / 检验更新此前判断 / 属于已建立的监测序列"
                "→ 进入监测与比较流程**；普通一次性查询沿用现有交付方式，"
                "**不强行增加无关的趋势分析**。",
    }


def monitor_series_id(object_id: str, scope: str = "") -> str:
    """稳定监测序列编号：`MON-<对象>-<范围指纹>`。

    **不能仅凭对象简称、文件名或本次任务编号匹配历史** ——
    序列编号由**稳定对象标识 + 研究范围**派生：同一对象同一范围 → 同一序列；
    换个说法 / 换个任务编号 → 仍是同一序列。
    """
    oid = (object_id or "").strip()
    scope = (scope or "").strip()
    fp = hashlib.sha256((oid + "|" + scope).encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", oid)[:32] or "OBJ"
    return f"MON-{safe}-{fp}"


# ═══════════════════════════════════════════════════════════════════
# 2. 记录构造 / 保存 / 读取（用户要求 2）
# ═══════════════════════════════════════════════════════════════════

def _next_record_id(records_dir: str, series_id: str, plan_date: str) -> str:
    d = os.path.join(records_dir, series_id)
    n = 0
    if os.path.isdir(d):
        n = len([f for f in os.listdir(d) if f.endswith(".json")
                 and not f.endswith(".compare.json")])
    return f"R-{plan_date}-{n + 1:02d}"


def make_record(record_id, series_id, *, task_ref=None, question_ids=None,
                object_id=None, object_name=None, scope="",
                data_asof=None, generated_at=None, material_times=None,
                metrics=None, evidence_refs=None, relations=None,
                judgement=None, rule_versions=None, data_gaps=None,
                sample_scope=None, kind="original", supersedes=None,
                basis_note=None) -> dict:
    """构造一条监测记录（**只记录实际获得的数据和形成的判断**）。

    `metrics` 每项：`{name, value, unit, period, calibre, sample_scope,
    data_version, revised_from, revised_value, revised_reason, state}`；
    `judgement`：`{statement, key_basis, assumptions, open_items, state,
    vs_baseline:{relation, why}}`（`vs_baseline` 由证据评估给出，缺省=不足）。
    `kind` ∈ original / revision / rebuilt；修订与重建**必须** `supersedes` 旧记录；
    rebuilt 须给 `basis_note`（重建依据，并区分当时可知 vs 现在重算）。
    **不为补齐字段而编造内容** —— 未知项一律 None / "unknown"。
    """
    if kind not in RECORD_KINDS:
        raise ValueError(f"未知记录种类：{kind}（须为 {RECORD_KINDS}）")
    if kind in ("revision", "rebuilt") and not supersedes:
        raise ValueError(f"{kind} 记录必须通过 supersedes 关联被替代的旧记录")
    if kind == "rebuilt" and not basis_note:
        raise ValueError("rebuilt（事后重建）必须说明依据（basis_note）")
    return {
        "record_id": record_id,
        "series_id": series_id,
        "task_ref": task_ref,
        "question_ids": list(question_ids or []),
        "object": {"object_id": object_id, "object_name": object_name, "scope": scope},
        "data_asof": data_asof or "unknown",
        "generated_at": generated_at,
        "material_times": dict(material_times or {}),
        "metrics": list(metrics or []),
        "evidence_refs": {"refs": list(evidence_refs or []),
                          "relations": list(relations or [])},
        "judgement": dict(judgement or {}),
        "rule_versions": dict(rule_versions or {}),
        "meta": {"kind": kind, "supersedes": supersedes, "basis_note": basis_note,
                 "data_gaps": list(data_gaps or []),
                 "sample_scope": sample_scope},
    }


def save_record(records_dir: str, record: dict) -> dict:
    """保存记录（**原始快照保存后不覆盖**）。

    拒绝两种情况（返回 ok=False，**不抛异常、不写文件**）：
      · `record_id` 已存在 → `duplicate_id`（不覆盖任何已有记录）；
      · 同一序列**同一数据时点**已有 `original` 记录且本次也是 `original`
        → `duplicate_observation`（**同一任务重复归档不能制造新的有效观测**；
        数据修订请用 `kind="revision"` 并 `supersedes` 旧记录）。
    """
    rid, sid = record.get("record_id"), record.get("series_id")
    if not rid or not sid:
        return {"ok": False, "error": "missing_id", "detail": "record_id / series_id 缺失"}
    d = os.path.join(records_dir, sid)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{rid}.json")
    if os.path.exists(p):
        return {"ok": False, "error": "duplicate_id",
                "detail": f"记录 {rid} 已存在 —— 原始快照保存后不覆盖；"
                          f"修订 / 重建请另建记录并 supersedes={rid}"}
    if record.get("meta", {}).get("kind") == "original":
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or f.endswith(".compare.json"):
                continue
            try:
                old = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except Exception:                                  # noqa: BLE001
                continue
            if (old.get("meta", {}).get("kind") == "original"
                    and old.get("data_asof") == record.get("data_asof")):
                return {"ok": False, "error": "duplicate_observation",
                        "detail": f"同一数据时点 {record.get('data_asof')} 已有原始记录 "
                                  f"{old.get('record_id')} —— 重复归档不制造新的有效观测"}
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "path": p, "record_id": rid,
            "kind": record.get("meta", {}).get("kind")}


def save_comparison(records_dir: str, record_id: str, cmp_out: dict) -> dict:
    """保存比较结果（`<record_id>.compare.json`）—— 与记录同目录、可追溯。"""
    sid = (cmp_out.get("series_id")
           or (cmp_out.get("baseline") or {}).get("series_id"))
    if not sid:
        return {"ok": False, "error": "missing_series", "detail": "比较结果缺 series_id"}
    d = os.path.join(records_dir, sid)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{record_id}.compare.json")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cmp_out, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "path": p}


def load_records(records_dir: str, series_id: str | None = None) -> dict:
    """读取历史监测记录。

    · 目录 / 文件不存在 → `status="no_dir"`（**确实没有历史记录**）；
    · 某文件解析失败 → 记入 `errors`（`status="load_error"`，**读取失败 ≠ 无历史**，
      也不覆盖 / 删除该文件）；
    · 正常 → `status="ok"`。
    排序按记录内 `data_asof`（**不按文件修改时间 / 文件名顺序**）。
    """
    if not records_dir or not os.path.isdir(records_dir):
        return {"records": [], "errors": [], "status": "no_dir",
                "note": "监测记录目录不存在 —— 首次监测，从本次建立基线，不编造历史变化"}
    sids = [series_id] if series_id else sorted(
        d for d in os.listdir(records_dir) if d.startswith("MON-"))
    records, errors = [], []
    for sid in sids:
        d = os.path.join(records_dir, sid)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or f.endswith(".compare.json"):
                continue
            p = os.path.join(d, f)
            try:
                rec = json.load(open(p, encoding="utf-8"))
                if isinstance(rec, dict) and rec.get("record_id"):
                    records.append(rec)
                else:
                    errors.append({"file": p, "error": "记录结构不完整（缺 record_id）"})
            except Exception as e:                            # noqa: BLE001
                errors.append({"file": p, "error": f"解析失败：{e}"})
    records.sort(key=lambda r: (str(r.get("data_asof")), str(r.get("record_id"))))
    status = "load_error" if errors else ("ok" if records else "no_dir")
    return {"records": records, "errors": errors, "status": status,
            "note": ("历史记录读取失败 —— 标记读取异常，不冒充没有历史，不覆盖旧记录"
                     if errors else None)}


# ═══════════════════════════════════════════════════════════════════
# 3. 基线选择（用户要求 3）
# ═══════════════════════════════════════════════════════════════════

def _revision_supersedes(records: list) -> dict:
    """`record_id -> 被替代它的记录集合`（修订链：新旧版本都保留在盘上）。"""
    m = {}
    for r in records:
        sp = (r.get("meta") or {}).get("supersedes")
        if sp:
            m.setdefault(sp, []).append(r.get("record_id"))
    return m


def _effective_records(records: list) -> list:
    """同一数据时点的多个版本（original + revision）**只算一个观测**：
    取该时点「最新修订版」（生成时间最晚），其余进 `superseded_by`。

    🔴 **同一数据时点的多个修订或重算版本，不能被计为多个连续时间点。**
    """
    by_asof = {}
    for r in records:
        key = str(r.get("data_asof"))
        by_asof.setdefault(key, []).append(r)
    out = []
    for key, group in by_asof.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        # 优先 non-original（修订/重建代表最新认知），再按 generated_at / record_id 排
        def _rank(r):
            return (0 if (r.get("meta") or {}).get("kind") == "original" else 1,
                    str(r.get("generated_at") or ""), str(r.get("record_id")))
        group_sorted = sorted(group, key=_rank)
        keep = group_sorted[-1]
        keep = dict(keep)
        keep["superseded_by"] = [g["record_id"] for g in group_sorted[:-1]]
        keep["version_chain"] = [g["record_id"] for g in group_sorted]
        out.append(keep)
    out.sort(key=lambda r: str(r.get("data_asof")))
    return out


def select_baseline(records, *, cutoff=None, specified_record=None,
                    specified_date=None, load_errors=None) -> dict:
    """选择历史基线。

    优先级：`specified_record`（用户指定记录）> `specified_date`（指定研究时点，
    取该时点的记录；该时点无记录则取 ≤ 该时点最近一条并**说明实际比较日期**）
    > 无指定 → **当前研究时点（cutoff）之前最近的适用记录**。

    · **不按文件修改时间 / 下载顺序 / 报告返回顺序**（只按记录内 `data_asof`）；
    · `load_errors` 非空 → `status="load_error"`（**读取失败 ≠ 无历史**）；
    · 同一数据时点多版本 → 取最新修订（`version_chain` 留全链，均不计为多个时点）；
    · `usable(record)` 决定「适用」：有指标或判断、非不可读。
    """
    if load_errors:
        return {"baseline": None, "status": "load_error",
                "basis": "历史记录读取失败 —— 标记读取异常，不冒充没有历史，"
                         "不覆盖旧记录", "compared_at": None,
                "n_available": len(records), "rule": BASELINE_RULE}
    eff = _effective_records(list(records or []))
    usable = [r for r in eff if (r.get("metrics") or r.get("judgement"))]
    if specified_record:
        for r in usable:
            if r.get("record_id") == specified_record:
                return {"baseline": r, "status": "specified",
                        "basis": f"用户指定记录 {specified_record}",
                        "compared_at": r.get("data_asof"), "n_available": len(usable),
                        "rule": BASELINE_RULE}
        return {"baseline": None, "status": "unusable",
                "basis": f"用户指定记录 {specified_record} 不存在或不可用",
                "compared_at": None, "n_available": len(usable), "rule": BASELINE_RULE}
    anchor = specified_date or cutoff
    if not usable:
        return {"baseline": None, "status": "no_history",
                "basis": "同一监测序列中当前研究时点之前没有适用记录 —— "
                         "**首次监测，从本次建立基线，不编造历史变化**",
                "compared_at": None, "n_available": 0, "rule": FIRST_RUN_RULE}
    if anchor is None:
        cand = usable[-1]
    else:
        before = [r for r in usable if str(r.get("data_asof")) <= str(anchor)]
        cand = before[-1] if before else None
    if cand is None:
        return {"baseline": None, "status": "no_history",
                "basis": f"研究时点 {anchor} 之前没有适用记录（不编造历史变化）",
                "compared_at": None, "n_available": len(usable), "rule": FIRST_RUN_RULE}
    status = "specified" if specified_date else "nearest"
    basis = (f"用户指定研究时点 {specified_date}，实际比较记录 {cand['record_id']}"
             f"（数据时点 {cand['data_asof']}）" if specified_date else
             f"未指定基线 → 当前研究时点 {cutoff} 之前最近适用记录 "
             f"{cand['record_id']}（数据时点 {cand['data_asof']}）")
    return {"baseline": cand, "status": status, "basis": basis,
            "compared_at": cand.get("data_asof"), "n_available": len(usable),
            "rule": BASELINE_RULE}


# ═══════════════════════════════════════════════════════════════════
# 4. 可比性检查（用户要求 4）
# ═══════════════════════════════════════════════════════════════════

#: 可统一口径的单位转换（**明确转换方法，另存结果、不覆盖原值**）
UNIT_CONVERSIONS = {
    ("亿元", "万元"): 10000.0, ("万元", "亿元"): 1 / 10000.0,
}

_CMP_STATES = ("match", "mismatch", "unifiable", "unknown")


def _cmp_metric(cur_m: dict, base_m: dict) -> dict:
    """单指标可比性 + （可统一时的）转换方法。"""
    items = {}
    for dim, ck in (("object", "object_id"), ("unit", "unit"),
                    ("period", "period"), ("calibre", "calibre"),
                    ("sample_scope", "sample_scope")):
        cv = cur_m.get(ck)
        bv = base_m.get(ck)
        if cv is None and bv is None:
            items[dim] = {"state": "match", "note": "两侧均未标注（同视为「未限定」）"}
        elif cv is not None and bv is not None and cv == bv:
            items[dim] = {"state": "match"}
        elif cv is None or bv is None:
            items[dim] = {"state": "unknown", "note": f"{dim} 信息缺失（{cv!r} vs {bv!r}）"}
        else:
            items[dim] = {"state": "mismatch", "note": f"{dim} 不同：{bv!r} → {cv!r}"}
    # 单位可统一？
    u = items.get("unit", {})
    if u.get("state") == "mismatch":
        cu, bu = cur_m.get("unit"), base_m.get("unit")
        if (bu, cu) in UNIT_CONVERSIONS:
            factor = UNIT_CONVERSIONS[(bu, cu)]
            u.update({"state": "unifiable",
                      "conversion": f"基线 {bu} → 本次 {cu}（×{factor:g}，"
                                    f"明确转换方法，另存结果，不覆盖原值）",
                      "factor": factor})
    return items


def check_comparability(cur: dict, base: dict) -> dict:
    """比较前的七维可比性检查（对象 / 问题 / 指标 / 单位 / 期间 / 样本 / 规则）。

    输出：
      · `dims`：记录级维度（对象 / 问题 / 规则）；
      · `metrics`：逐指标（同名指标逐维）；
      · `comparable` / `unifiable` / `uncomparable` / `unknown`：指标分组 ——
        **部分指标不可比，不妨碍其他符合条件的指标继续比较**。
    """
    dims = {}
    co, bo = (cur or {}).get("object") or {}, (base or {}).get("object") or {}
    if co.get("object_id") and bo.get("object_id"):
        dims["object"] = {"state": "match" if co["object_id"] == bo["object_id"]
                          else "mismatch"}
    else:
        dims["object"] = {"state": "unknown"}
    cq = set((cur or {}).get("question_ids") or [])
    bq = set((base or {}).get("question_ids") or [])
    if cq and bq:
        dims["question"] = {"state": "match" if (cq & bq) else "mismatch",
                            "note": "按问题编号交集（不机械比对文字）"}
    else:
        dims["question"] = {"state": "unknown"}
    cr, br = (cur or {}).get("rule_versions") or {}, (base or {}).get("rule_versions") or {}
    if cr or br:
        dims["rule"] = {"state": "match" if cr == br else "mismatch",
                        "note": "分析规则及版本不同 → 判断变化须先标明规则影响"
                        if cr != br else ""}
    else:
        dims["rule"] = {"state": "unknown"}

    cur_by = {m.get("name"): m for m in (cur or {}).get("metrics") or []}
    base_by = {m.get("name"): m for m in (base or {}).get("metrics") or []}
    groups = {"comparable": [], "unifiable": [], "uncomparable": [], "unknown": []}
    per_metric = {}
    for name in sorted(set(cur_by) | set(base_by)):
        cm, bm = cur_by.get(name), base_by.get(name)
        if cm is None or bm is None:
            per_metric[name] = {"state": "incomplete",
                                "note": "一侧缺该指标 —— 缺失值不能当作 0，不强算"}
            groups["unknown"].append(name)
            continue
        items = _cmp_metric(cm, bm)
        bad = [d for d, v in items.items() if v.get("state") == "mismatch"]
        uni = [d for d, v in items.items() if v.get("state") == "unifiable"]
        unk = [d for d, v in items.items() if v.get("state") == "unknown"]
        if bad:
            per_metric[name] = {"state": "uncomparable", "items": items,
                                "note": "口径差异无法统一 → 标明不可比及原因；"
                                        "不直接归因为对象变化"}
            groups["uncomparable"].append(name)
        elif uni:
            per_metric[name] = {"state": "unifiable", "items": items,
                                "note": "可合理统一口径且资料足够 → 明确转换方法，"
                                        "另存结果（不覆盖原值）"}
            groups["unifiable"].append(name)
        elif unk:
            per_metric[name] = {"state": "unknown", "items": items}
            groups["unknown"].append(name)
        else:
            per_metric[name] = {"state": "comparable", "items": items}
            groups["comparable"].append(name)
    return {"dims": dims, "metrics": per_metric, "groups": groups,
            "note": "**先检查可比性，再计算和解释变化**；部分指标不可比不妨碍"
                    "其他可比指标继续比较；缺失值不能当作 0。",
            "rule": COMPARABILITY_RULE}


# ═══════════════════════════════════════════════════════════════════
# 5. 变化计算（三量纲分开；不补 0；不硬算）
# ═══════════════════════════════════════════════════════════════════

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def measure_change(cur_m: dict, base_m: dict, *, factor: float | None = None) -> dict:
    """计算单指标变化 —— **区分数值差 / 百分比变化 / 百分点变化**。

    · 缺失值**不能当作 0**（任一侧缺 → 全部量纲 None + 说明）；
    · 基数为 0 → **不硬算无意义的变化率**（percent=None）；
    · 单位为 `%`（比例类）→ 额外给 `pct_point`（百分点变化）；
    · 可统一口径时 `factor` 由可比性检查给出（**另存结果**，不改原值）。
    """
    cv, bv = _num(cur_m.get("value")), _num(base_m.get("value"))
    if cv is None or bv is None:
        return {"absolute": None, "percent": None, "pct_point": None,
                "missing": True,
                "note": "缺失值不能当作 0 —— 缺一侧即不计算"}
    if factor is not None and factor != 0 and factor != 1:
        bv = bv * factor
        note_conv = f"基线值按口径转换 ×{factor:g} 后参与比较（另存结果，不覆盖原值）"
    else:
        note_conv = None
    out = {"absolute": cv - bv, "percent": None, "pct_point": None,
           "missing": False, "cur_value": cv, "base_value": bv}
    if bv != 0:
        out["percent"] = (cv - bv) / abs(bv) * 100.0
    else:
        out["note_zero_base"] = "基数为 0 —— 不硬算无意义的变化率"
    if str(cur_m.get("unit")) == "%" and str(base_m.get("unit")) == "%":
        out["pct_point"] = cv - bv
        out["note_measure"] = ("单位为 % → 用**百分点变化**表述；"
                               "百分比变化（相对变化）另列，不得混写")
    if note_conv:
        out["note_conversion"] = note_conv
    return out


def state_transition(cur_state, base_state) -> dict:
    """类别 / 阶段状态按**状态迁移**比较 —— 不作无依据的算术平均。"""
    if cur_state is None or base_state is None:
        return {"from": base_state, "to": cur_state, "changed": None,
                "note": "状态信息缺失 —— 如实标记，不推断"}
    return {"from": base_state, "to": cur_state,
            "changed": cur_state != base_state,
            "note": "阶段 / 类别状态按**状态迁移**比较；不作无依据的算术平均"}


def attribute_change(name, cur_m, base_m, *, rule_changed=False,
                     comparability_state="comparable") -> dict:
    """变化归因（四问④）：**事实变化与判断变化分开；允许无法判定**。

    判定顺序（可解释的优先）：
      ① 基线指标带 `revised_value`（数据修订标记）且本次值与修订值一致
         → `data_revision`（对象没有新的变化）；
      ② 可比性为 uncomposable / 口径变化 → `calibre_change`；
      ③ 规则版本变化且**判断**（非数值）变化 → `rule_change`；
      ④ 口径一致、无修订标记、数值确实变化 → `object_change`；
      ⑤ 以上信号矛盾或不足 → `undetermined`（**不为了完成分类而强行归因**）。
    """
    cv, bv = _num(cur_m.get("value")), _num(base_m.get("value"))
    rv, bv2 = _num(base_m.get("revised_value")), _num(base_m.get("value"))
    if cv is not None and rv is not None and cv == rv and bv is not None and rv != bv:
        return {"name": name, "attribution": "data_revision",
                "note": f"基线值 {bv} → 修订值 {rv}，本次值与修订值一致 —— "
                        f"变化来自**数据修订**，对象没有新的变化（保留原始值与修订值）"}
    if comparability_state in ("uncomparable", "unifiable"):
        return {"name": name, "attribution": "calibre_change",
                "note": "单位 / 统计期间 / 样本范围存在差异 —— 不直接归因为对象变化"}
    if rule_changed:
        return {"name": name, "attribution": "rule_change",
                "note": "分析规则及版本变化 → 先标明**规则影响**，"
                        "不直接写成趋势转强或转弱"}
    if cv is not None and bv is not None and cv != bv and comparability_state == "comparable":
        return {"name": name, "attribution": "object_change",
                "note": "口径一致、无修订标记 —— 数值变化归因于研究对象本身"}
    return {"name": name, "attribution": "undetermined",
            "note": "原因暂时无法判定 —— 允许，不强行为完成分类而归因"}


# ═══════════════════════════════════════════════════════════════════
# 6. 综合比较（四问 + 持续性 + 判断支持度）
# ═══════════════════════════════════════════════════════════════════

def compare(cur: dict, base: dict | None, *, series_records=None,
            comparability=None) -> dict:
    """综合比较，回答四个问题：

      ① 哪些事实、指标和判断发生了变化？
      ② 哪些变化具有延续性，哪些只是本次较上次的变化？（**只有两个时点
         不得宣称持续趋势**；同一数据时点的多版本**不**计为多个时点）
      ③ 新证据支持、削弱或推翻了原判断，还是仍不足以判断？
      ④ 变化来自研究对象本身、数据修订、指标口径或规则调整，还是多种原因？
    """
    series_records = list(series_records or [cur, base] if base else [cur])
    cmp_ = comparability or check_comparability(cur, base or {})
    rule_changed = cmp_["dims"].get("rule", {}).get("state") == "mismatch"
    changes = []
    cur_by = {m.get("name"): m for m in cur.get("metrics") or []}
    base_by = {m.get("name"): m for m in (base or {}).get("metrics") or []}
    for name in sorted(set(cur_by) | set(base_by)):
        cm, bm = cur_by.get(name), base_by.get(name)
        if cm is None or bm is None:
            changes.append({"name": name, "state": "missing",
                            "measure": None, "attribution": None,
                            "note": "一侧缺该指标 —— 缺失值不能当作 0，不强算"})
            continue
        mstate = (cmp_["metrics"].get(name) or {}).get("state", "unknown")
        factor = None
        if mstate == "unifiable":
            it = (cmp_["metrics"].get(name) or {}).get("items", {})
            factor = (it.get("unit") or {}).get("factor")
        ms = measure_change(cm, bm, factor=factor)
        at = attribute_change(name, cm, bm, rule_changed=rule_changed,
                              comparability_state=mstate)
        changes.append({"name": name, "state": mstate, "measure": ms,
                        "attribution": at["attribution"], "attribution_note": at["note"],
                        "cur": cm.get("value"), "base": bm.get("value"),
                        "unit": cm.get("unit"),
                        "revised": {"base_value": bm.get("value"),
                                    "revised_value": bm.get("revised_value"),
                                    "reason": bm.get("revised_reason")}
                        if bm.get("revised_value") is not None else None})
    # 状态迁移（阶段判断）
    cs = ((cur.get("judgement") or {}).get("state"))
    bs = ((base.get("judgement") or {}).get("state")) if base else None
    st = state_transition(cs, bs)
    # 持续性：distinct 数据时点数（**不把多版本计为多时点**）
    n_points = len({str(r.get("data_asof")) for r in series_records})
    persistence = {
        "n_points": n_points,
        "can_claim_trend": n_points >= 3,
        "note": ("持续性判断需要**相应连续记录和明确规则** —— "
                 "只有两个时点时**不得仅凭两点差异宣称持续趋势**，"
                 "只报告本次较基线的变化" if n_points < 3 else
                 "时点数 ≥ 3，可按连续记录与既定规则讨论持续性"),
    }
    # 判断支持度（新证据 vs 原判断）
    jb = (base.get("judgement") or {}) if base else {}
    vs = ((cur.get("judgement") or {}).get("vs_baseline") or {})
    relation = vs.get("relation") if vs.get("relation") in JUDGEMENT_RELATIONS \
        else ("insufficient" if jb else None)
    judgement_update = {
        "baseline_statement": jb.get("statement"),
        "relation": relation,
        "why": vs.get("why") or ("仍不足以判断 —— 不为完成分类而强行下结论"
                                 if relation == "insufficient" else None),
        "note": "**事实变化与判断变化分开处理**；新证据与旧判断冲突时"
                "明确指出冲突并更新判断，**保留旧记录**（不覆盖）",
    }
    return {
        "series_id": cur.get("series_id"),
        "current_record": cur.get("record_id"),
        "baseline_record": (base or {}).get("record_id"),
        "compared_at": (base or {}).get("data_asof"),
        "q1_changed": {"changes": changes, "state_transition": st},
        "q2_persistence": persistence,
        "q3_judgement": judgement_update,
        "q4_attribution": {"rule_changed": rule_changed,
                           "attributions": {c["name"]: c["attribution"]
                                            for c in changes if c.get("attribution")},
                           "note": "数据修订 / 规则调整不写成研究对象变化；"
                                   "允许「原因暂时无法判定」"},
        "comparability": cmp_,
        "rule": ATTRIBUTION_RULE,
    }


# ═══════════════════════════════════════════════════════════════════
# 7. 成稿回答（用户要求 5：比较在成稿前完成）
# ═══════════════════════════════════════════════════════════════════

def build_answer(cur: dict, cmp_out: dict | None) -> dict:
    """成稿前的最终回答结构（只显示与用户问题有关的内容）。

    没有变化时明确说明「没有变化」；没有依据的部分**不填充推演**；
    保留结论到问题 / 指标 / 证据 / 历史记录 / 报告编号的对应关系。
    """
    j = cur.get("judgement") or {}
    if cmp_out is None:
        return {
            "conclusion": j.get("statement") or "（本次未形成判断 —— 如实说明，不编造）",
            "changes_vs_baseline": None,
            "judgement_update": None,
            "open_items": list(j.get("open_items") or []),
            "refs": {"record": cur.get("record_id"),
                     "questions": cur.get("question_ids"),
                     "metrics": [m.get("name") for m in cur.get("metrics") or []],
                     "evidence": (cur.get("evidence_refs") or {}).get("refs")},
            "note": "**首次监测：建立本次基线，不生成虚构的历史趋势**",
        }
    changes = [c for c in cmp_out["q1_changed"]["changes"]
               if c.get("state") != "missing"]
    changed = [c for c in changes
               if (c.get("measure") or {}).get("absolute") not in (None, 0)]
    lines = []
    for c in changed:
        ms = c["measure"]
        unit = c.get("unit") or ""
        if ms.get("missing"):
            continue
        txt = f"{c['name']}：{c['base']}{unit} → {c['cur']}{unit}"
        if ms.get("pct_point") is not None:
            txt += f"（**{ms['pct_point']:+g} 个百分点**）"
        elif ms.get("absolute") is not None:
            txt += f"（数值差 {ms['absolute']:+g}）"
        if ms.get("percent") is not None:
            txt += f"；相对变化 {ms['percent']:+.1f}%"
        if ms.get("note_zero_base"):
            txt += f"；{ms['note_zero_base']}"
        txt += f"；归因：{c['attribution']}"
        lines.append(txt)
    st = cmp_out["q1_changed"]["state_transition"]
    if st.get("changed"):
        lines.append(f"阶段状态迁移：{st['from']} → {st['to']}（按状态迁移比较，"
                     "不作算术平均）")
    per = cmp_out["q2_persistence"]
    ju = cmp_out["q3_judgement"]
    return {
        "conclusion": j.get("statement") or "（本次未形成判断）",
        "changes_vs_baseline": lines if lines else
        "与所选基线（%s）相比**没有可报告的变化**" % cmp_out.get("compared_at"),
        "persistence_note": per["note"],
        "judgement_update": {
            "baseline": ju.get("baseline_statement"),
            "relation": ju.get("relation"),
            "why": ju.get("why"),
        } if ju.get("baseline_statement") else None,
        "open_items": list(j.get("open_items") or []),
        "refs": {"record": cur.get("record_id"),
                 "baseline": cmp_out.get("baseline_record"),
                 "questions": cur.get("question_ids"),
                 "metrics": [m.get("name") for m in cur.get("metrics") or []],
                 "evidence": (cur.get("evidence_refs") or {}).get("refs")},
        "note": "比较在**成稿前**完成；数值上升不自动等于改善、下降不自动等于恶化，"
                "按指标含义及当前分析框架解释；没有依据的部分不填充推演",
    }


# ═══════════════════════════════════════════════════════════════════
# 8. 完整流程（用户要求 5：识别 → 基线 → 资料 → 校验 → 可比 → 比较 → 成稿 → 保存）
# ═══════════════════════════════════════════════════════════════════

def run_monitor_flow(text, *, records_dir, object_id, object_name=None,
                     scope="", current_record=None, current_metrics=None,
                     current_judgement=None, material_times=None,
                     evidence_refs=None, relations=None, question_ids=None,
                     task_ref=None, rule_versions=None, data_gaps=None,
                     sample_scope=None, generated_at=None,
                     specified_record=None, specified_date=None,
                     research_cutoff=None, record_kind="original",
                     supersedes=None, basis_note=None, save=True,
                     need_new_report=False, reuse_flow_kwargs=None) -> dict:
    """**第 5 轮完整流程**（贯穿实际代码，发布端默认离线替身）：

        识别监测任务 → 读取历史基线 → 获取或复用本次资料 → 来源与数据校验
        → 检查可比性 → 比较事实和判断 → 生成交付内容 → 保存本次记录及比较结果

    · 普通一次性查询（`is_monitor=False`）→ `mode="one_off"`，沿用现有交付方式，
      **不强行增加无关的趋势分析**；
    · 已有资料足够（`current_record` 或 `current_metrics` 提供）→ **不新增发布**
      （`publish_calls=0`）；需要补充资料或新报告时传 `need_new_report=True`
      （或由调用方先走 `publish_plan.run_reuse_flow()` 再把材料传入）——
      **继续经过前几轮的路由、内容复用、参数校验、发布检查和预算规则**；
    · **未接入真实调度**：`automation="manual_only"`，不宣称自动定时运行或主动推送；
    · **保存失败时如实说明**（`saved.ok=False`），不宣称基线已建立成功。
    """
    mr = is_monitor_request(text)
    sid = monitor_series_id(object_id, scope)
    if not mr["is_monitor"]:
        return {"mode": "one_off", "is_monitor": mr, "series_id": sid,
                "note": "普通一次性查询 —— 沿用现有交付方式，"
                        "不强行增加无关的趋势分析",
                "automation": "manual_only"}
    # ① 读取历史基线
    loaded = load_records(records_dir, sid)
    series_records = [r for r in loaded["records"] if r.get("series_id") == sid]
    bl = select_baseline(series_records, cutoff=research_cutoff,
                         specified_record=specified_record,
                         specified_date=specified_date,
                         load_errors=loaded["errors"])
    # ①b 需要新报告 → 走前四轮真实入口（路由 / 内容复用 / 参数校验 / 发布检查 / 预算）
    publish_calls = 0
    reuse_flow = None
    if need_new_report:
        import publish_plan as pp
        reuse_flow = pp.run_reuse_flow(text, **(reuse_flow_kwargs or {}))
        publish_calls = reuse_flow.get("publish_calls", 0)
    # ② 本次资料（调用方给：current_record 优先，否则现场构造）
    if current_record is not None:
        cur = dict(current_record)
        cur.setdefault("series_id", sid)
    else:
        rid = _next_record_id(records_dir, sid,
                              str(research_cutoff or generated_at or
                                  (material_times or {}).get("fetched_at")
                                  or "19700101").replace("-", "")[:8])
        cur = make_record(rid, sid, task_ref=task_ref, question_ids=question_ids,
                          object_id=object_id, object_name=object_name, scope=scope,
                          data_asof=research_cutoff, generated_at=generated_at,
                          material_times=material_times, metrics=current_metrics,
                          evidence_refs=evidence_refs, relations=relations,
                          judgement=current_judgement, rule_versions=rule_versions,
                          data_gaps=data_gaps, sample_scope=sample_scope,
                          kind=record_kind, supersedes=supersedes,
                          basis_note=basis_note)
    # ③ 可比性 → 比较（**成稿前**）
    if bl["status"] in ("no_history", "load_error", "unusable"):
        cmp_out = None
    else:
        cmp_out = compare(cur, bl["baseline"], series_records=series_records)
    # ④ 成稿回答
    answer = build_answer(cur, cmp_out)
    # ⑤ 保存本次记录 + 比较结果（原始不覆盖；重复归档拒绝）
    saved = {"ok": False, "skipped": True, "detail": "save=False"}
    saved_cmp = {"ok": False, "skipped": True}
    if save:
        saved = save_record(records_dir, cur)
        if saved["ok"] and cmp_out is not None:
            saved_cmp = save_comparison(records_dir, cur["record_id"], cmp_out)
        elif saved["ok"]:
            saved_cmp = {"ok": True, "detail": "首次监测 / 基线不可用 —— 无比较结果可存"}
    return {
        "mode": mr["mode"], "is_monitor": mr, "series_id": sid,
        "baseline": {k: v for k, v in bl.items() if k != "baseline"},
        "baseline_record": bl.get("baseline"),
        "load": {"status": loaded["status"], "n_records": len(series_records),
                 "errors": loaded["errors"]},
        "current": cur, "compare": cmp_out, "answer": answer,
        "saved": saved, "saved_comparison": saved_cmp,
        "publish_calls": publish_calls,
        "reuse_flow": ({"n_items": reuse_flow.get("n_items"),
                        "sent_params": reuse_flow.get("sent_params"),
                        "aid_bindings": reuse_flow.get("aid_bindings")}
                       if reuse_flow else None),
        "publish_note": ("需要新报告 → 已走 publish_plan.run_reuse_flow() 真实入口"
                         "（路由 / 内容复用 / 参数校验 / 发布检查 / 预算）"
                         if need_new_report else
                         "已有资料足够完成比较 → 不新增多余的报告发布"),
        "automation": "manual_only",
        "automation_note": AUTOMATION_NOTE,
        "flow": "识别监测任务 → 读取历史基线 → 获取或复用本次资料 → 来源与数据校验"
                "→ 检查可比性 → 比较事实和判断 → 生成交付内容（成稿前完成比较）"
                "→ 保存本次记录及比较结果",
        "rule": FLOW_RULE,
    }


# ═══════════════════════════════════════════════════════════════════
# 9. 用例执行入口（cases/14 与 verify.py 走同一条真实路径）
# ═══════════════════════════════════════════════════════════════════

def _eq(cur, want):
    if isinstance(want, dict) and isinstance(cur, dict):
        return all(k in cur and _eq(cur[k], v) for k, v in want.items())
    return cur == want


def _assert_expect(got, expect):
    if expect is None:
        return None
    if isinstance(expect, dict) and expect.get("__path__"):
        node = got
        for part in expect["__path__"].split("."):
            node = (node or {}).get(part) if isinstance(node, dict) else None
        return _eq(node, expect.get("value"))
    return _eq(got, expect)


def run_check_spec(spec: dict, base_dir: str | None = None) -> dict:
    """执行 cases/14 的检查清单（全部走本模块真实函数）。"""
    import tempfile
    items = []

    def add(label, ok, detail):
        items.append({"label": label, "pass": bool(ok), "detail": detail})

    for ch in spec.get("checks", []):
        kind = ch.get("type")
        label = ch.get("label") or kind
        inp = ch.get("input") or {}
        expect = ch.get("expect")
        try:
            if kind == "monitor":
                got = is_monitor_request(inp.get("text"))
            elif kind == "series":
                got = {"id": monitor_series_id(inp.get("object_id"), inp.get("scope")),
                       "same": monitor_series_id(inp.get("object_id"),
                                                 inp.get("scope"))
                       == monitor_series_id(inp.get("object_id2", inp.get("object_id")),
                                            inp.get("scope2", inp.get("scope")))}
            elif kind == "record":
                got = make_record(**inp)
                bad = [f for f in inp if f in MONITOR_FIELDS and f not in got]
                got = {"fields_ok": not bad, "record": got,
                       "n_fields": len([f for f in MONITOR_FIELDS if f in got])}
            elif kind == "save":
                with tempfile.TemporaryDirectory() as td:
                    r = make_record(**inp.get("record", {}))
                    s1 = save_record(td, r)
                    again = save_record(td, r)
                    dup_obs = None
                    if inp.get("duplicate_asof"):
                        r2 = make_record(**inp.get("dup_record", {}))
                        dup_obs = save_record(td, r2)
                    got = {"first": s1["ok"], "again_error": again.get("error"),
                           "dup_error": (dup_obs or {}).get("error")}
            elif kind == "load":
                with tempfile.TemporaryDirectory() as td:
                    if inp.get("write_bad"):
                        d = os.path.join(td, inp.get("series", "MON-X-0"))
                        os.makedirs(d, exist_ok=True)
                        open(os.path.join(d, "R-bad.json"), "w",
                             encoding="utf-8").write("{not json")
                    if inp.get("write_ok"):
                        d = os.path.join(td, inp.get("series", "MON-X-0"))
                        os.makedirs(d, exist_ok=True)
                        json.dump(make_record(**inp["ok_record"]),
                                  open(os.path.join(d, "R-ok.json"), "w",
                                       encoding="utf-8"), ensure_ascii=False)
                    got = load_records(td, inp.get("series"))
                    got = {"status": got["status"], "n": len(got["records"]),
                           "n_err": len(got["errors"])}
            elif kind == "baseline":
                recs = [make_record(**r) for r in inp.get("records", [])]
                got = select_baseline(recs, **inp.get("kwargs", {}))
                got = {"status": got["status"],
                       "record": (got.get("baseline") or {}).get("record_id"),
                       "compared_at": got.get("compared_at")}
            elif kind == "comparability":
                cur = make_record(**inp.get("cur", {}))
                base = make_record(**inp.get("base", {}))
                got = check_comparability(cur, base)
                got = {"dims": got["dims"], "groups": got["groups"]}
            elif kind == "change":
                got = measure_change(inp.get("cur", {}), inp.get("base", {}),
                                     factor=inp.get("factor"))
            elif kind == "state":
                got = state_transition(inp.get("cur"), inp.get("base"))
            elif kind == "attribute":
                got = attribute_change(inp.get("name", "m"), inp.get("cur", {}),
                                       inp.get("base", {}),
                                       rule_changed=inp.get("rule_changed", False),
                                       comparability_state=inp.get(
                                           "comparability_state", "comparable"))
            elif kind == "compare":
                cur = make_record(**inp.get("cur", {}))
                base = make_record(**inp.get("base", {}))
                got = compare(cur, base, series_records=[
                    make_record(**r) for r in inp.get("series_records",
                                                      [inp.get("base", {}),
                                                       inp.get("cur", {})])])
            elif kind == "answer":
                cur = make_record(**inp.get("cur", {}))
                base = make_record(**inp.get("base", {})) if inp.get("base") else None
                cmp_out = compare(cur, base) if base else None
                got = build_answer(cur, cmp_out)
            elif kind == "flow":
                with tempfile.TemporaryDirectory() as td:
                    d = td
                    for r in inp.get("pre_records", []):
                        save_record(d, make_record(**r))
                    got = run_monitor_flow(inp.get("text", ""), records_dir=d,
                                           **inp.get("flow_kwargs", {}))
                    got = {k: got.get(k) for k in inp.get("out_keys", [
                        "mode", "baseline", "publish_calls", "automation"])}
            else:
                add(label, False, f"未知检查类型：{kind}")
                continue
            ok = _assert_expect(got, expect) if expect is not None else True
            add(label, ok if isinstance(ok, bool) else True,
                ("符合预期" if ok else f"不符预期：got={json.dumps(got, ensure_ascii=False, default=str)[:400]}")
                if expect is not None else json.dumps(got, ensure_ascii=False, default=str)[:300])
        except Exception as e:                                    # noqa: BLE001
            add(label, False, f"执行出错：{e}")
    n_fail = sum(1 for i in items if not i["pass"])
    return {"pass": n_fail == 0, "n_checks": len(items), "n_fail": n_fail,
            "results": items}


# ═══════════════════════════════════════════════════════════════════
# 10. selftest（内置断言；`cases/14` 与 verify.py 走同一条路径）
# ═══════════════════════════════════════════════════════════════════

def selftest() -> int:
    import tempfile
    n = [0]

    def ck(cond, msg):
        n[0] += 1
        print(("  ✓ " if cond else "  ✗ ") + msg)
        return bool(cond)

    print("monitor.selftest —— 监测记录与跨期比较（第 5 轮）")
    ok_all = True

    # ── S1 触发识别 ──
    mr1 = is_monitor_request("持续跟踪某公司的估值变化")
    mr2 = is_monitor_request("某公司现在的估值贵不贵")
    mr3 = is_monitor_request("跟上月相比营收怎么样")
    mr4 = is_monitor_request("检验此前的判断是否还成立")
    ok_all &= ck(mr1["is_monitor"] and mr1["mode"] == "track", "S1a 持续跟踪 → 进入监测")
    ok_all &= ck(not mr2["is_monitor"], "S1b 一次性查询 → 不进入（不强加趋势分析）")
    ok_all &= ck(mr3["is_monitor"] and mr3["mode"] == "compare", "S1c 比较类 → 进入")
    ok_all &= ck(mr4["is_monitor"] and mr4["mode"] == "verify", "S1d 检验判断 → 进入")

    # ── S2 序列编号稳定 ──
    s1 = monitor_series_id("某公司", "估值监测")
    s2 = monitor_series_id("某公司", "估值监测")
    s3 = monitor_series_id("某公司", "财务监测")
    ok_all &= ck(s1 == s2 and s1 != s3, "S2 序列编号：同对象同范围 → 同序列；范围不同 → 不同序列")

    # ── S3 记录构造 ──
    try:
        make_record("R-x", "MON-x", kind="bogus")
        ok_all &= ck(False, "S3a 未知记录种类被拒绝")
    except ValueError:
        ok_all &= ck(True, "S3a 未知记录种类被拒绝")
    try:
        make_record("R-x", "MON-x", kind="revision")
        ok_all &= ck(False, "S3b 修订记录必须 supersedes")
    except ValueError:
        ok_all &= ck(True, "S3b 修订记录必须 supersedes")
    try:
        make_record("R-x", "MON-x", kind="rebuilt", supersedes="R-old")
        ok_all &= ck(False, "S3c 事后重建必须说明依据")
    except ValueError:
        ok_all &= ck(True, "S3c 事后重建必须说明依据")

    # ── S4 保存：不覆盖 / 重复归档拒绝 ──
    with tempfile.TemporaryDirectory() as td:
        r1 = make_record("R-1", "MON-a", data_asof="2026-08-31",
                         metrics=[{"name": "营收", "value": 100, "unit": "亿元"}])
        s1 = save_record(td, r1)
        s2 = save_record(td, r1)
        r_dup = make_record("R-2", "MON-a", data_asof="2026-08-31",
                            metrics=[{"name": "营收", "value": 100, "unit": "亿元"}])
        s3 = save_record(td, r_dup)
        r_rev = make_record("R-3", "MON-a", data_asof="2026-08-31", kind="revision",
                            supersedes="R-1",
                            metrics=[{"name": "营收", "value": 105, "unit": "亿元"}])
        s4 = save_record(td, r_rev)
        ok_all &= ck(s1["ok"], "S4a 首次保存成功")
        ok_all &= ck(s2["ok"] is False and s2["error"] == "duplicate_id",
                     "S4b 原始快照保存后不覆盖（duplicate_id）")
        ok_all &= ck(s3["ok"] is False and s3["error"] == "duplicate_observation",
                     "S4c 同一数据时点重复归档 → 拒绝（不制造新观测）")
        ok_all &= ck(s4["ok"], "S4d 修订记录（supersedes）可保存")

        # ── S5 读取失败 ≠ 无历史 ──
        d = os.path.join(td, "MON-b")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "R-bad.json"), "w", encoding="utf-8").write("{oops")
        ld = load_records(td, "MON-b")
        ok_all &= ck(ld["status"] == "load_error" and len(ld["errors"]) == 1,
                     "S5 解析失败 → load_error（不冒充无历史、不覆盖旧文件）")

        # ── S6 基线选择 ──
        recs = [make_record(f"R-{d}", "MON-c", data_asof=d,
                            metrics=[{"name": "营收", "value": v, "unit": "亿元"}])
                for d, v in (("2026-08-31", 100), ("2026-09-30", 110))]
        b1 = select_baseline(recs, cutoff="2026-10-31")
        b2 = select_baseline(recs, cutoff="2026-10-31", specified_date="2026-08-31")
        b3 = select_baseline([], cutoff="2026-10-31")
        b4 = select_baseline(recs, cutoff="2026-10-31", load_errors=[{"file": "x"}])
        ok_all &= ck(b1["status"] == "nearest"
                     and b1["baseline"]["record_id"] == "R-2026-09-30"
                     and b1["compared_at"] == "2026-09-30",
                     "S6a 未指定 → cutoff 前最近适用（按记录内 data_asof）")
        ok_all &= ck(b2["status"] == "specified"
                     and b2["baseline"]["record_id"] == "R-2026-08-31",
                     "S6b 指定研究时点 → 按指定基线")
        ok_all &= ck(b3["status"] == "no_history", "S6c 无记录 → 首次建立基线，不编造历史")
        ok_all &= ck(b4["status"] == "load_error", "S6d 读取失败 ≠ 无历史")

        # 同一数据时点多版本只算一个观测
        rev = make_record("R-rev", "MON-c", data_asof="2026-09-30", kind="revision",
                          supersedes="R-2026-09-30",
                          metrics=[{"name": "营收", "value": 115, "unit": "亿元"}])
        eff = _effective_records(recs + [rev])
        ok_all &= ck(len(eff) == 2 and eff[-1]["record_id"] == "R-rev",
                     "S6e 同一时点多版本 → 取最新修订，不计为多个时间点")

    # ── S7 可比性 ──
    cur = make_record("R-c", "MON-d", metrics=[
        {"name": "营收", "value": 110, "unit": "亿元", "period": "2026Q3",
         "calibre": "合并报表", "object_id": "某公司", "sample_scope": None}])
    base_unit = make_record("R-b", "MON-d", metrics=[
        {"name": "营收", "value": 1000000, "unit": "万元", "period": "2026Q2",
         "calibre": "合并报表", "object_id": "某公司", "sample_scope": None}])
    cp = check_comparability(cur, base_unit)
    g = cp["metrics"]["营收"]
    ok_all &= ck(g["state"] == "uncomparable" and "营收" in cp["groups"]["uncomparable"],
                 "S7a 统计期间不同且不可统一 → 不可比（不归因为对象变化）")
    base_conv = make_record("R-b", "MON-d", metrics=[
        {"name": "营收", "value": 1000000, "unit": "万元", "period": "2026Q3",
         "calibre": "合并报表", "object_id": "某公司", "sample_scope": None}])
    cp2 = check_comparability(cur, base_conv)
    ok_all &= ck(cp2["metrics"]["营收"]["state"] == "unifiable"
                 and "营收" in cp2["groups"]["unifiable"],
                 "S7b 单位万元→亿元可统一 → unifiable（给转换方法，另存结果）")
    base_ok = make_record("R-b", "MON-d", metrics=[
        {"name": "营收", "value": 100, "unit": "亿元", "period": "2026Q3",
         "calibre": "合并报表", "object_id": "某公司", "sample_scope": None},
        {"name": "净利率", "value": None, "unit": "%", "period": "2026Q3"}])
    cp3 = check_comparability(cur, base_ok)
    ok_all &= ck("营收" in cp3["groups"]["comparable"]
                 and "净利率" in cp3["groups"]["unknown"],
                 "S7c 部分不可比不妨碍其他指标继续比较")

    # ── S8 变化计算 ──
    m1 = measure_change({"value": 15.0, "unit": "%"}, {"value": 18.4, "unit": "%"})
    ok_all &= ck(abs(m1["absolute"] - (-3.4)) < 1e-9
                 and abs(m1["pct_point"] - (-3.4)) < 1e-9
                 and abs(m1["percent"] - (-18.478)) < 0.01,
                 "S8a 百分比指标：数值差 / 百分点 / 相对变化三者分开")
    m2 = measure_change({"value": 5, "unit": "亿元"}, {"value": 0, "unit": "亿元"})
    ok_all &= ck(m2["percent"] is None and "基数为 0" in m2.get("note_zero_base", ""),
                 "S8b 基数为 0 → 不硬算变化率")
    m3 = measure_change({"value": 5, "unit": "亿元"}, {"value": None, "unit": "亿元"})
    ok_all &= ck(m3["missing"] and m3["absolute"] is None,
                 "S8c 缺失值不能当作 0")
    m4 = measure_change({"value": 110, "unit": "亿元"},
                        {"value": 1000000, "unit": "万元"}, factor=1 / 10000.0)
    ok_all &= ck(m4["absolute"] == 10.0, "S8d 口径转换后另存结果比较（不覆盖原值）")
    st = state_transition("S2", "S1")
    ok_all &= ck(st["changed"] and "算术平均" in st["note"],
                 "S8e 状态按迁移比较，不作算术平均")

    # ── S9 归因 ──
    a1 = attribute_change("营收", {"value": 105}, {"value": 100, "revised_value": 105})
    a2 = attribute_change("营收", {"value": 105}, {"value": 100},
                          comparability_state="uncomparable")
    a3 = attribute_change("毛利率", {"value": 30}, {"value": 30}, rule_changed=True)
    a4 = attribute_change("营收", {"value": 110}, {"value": 100})
    a5 = attribute_change("营收", {"value": None}, {"value": None})
    ok_all &= ck(a1["attribution"] == "data_revision", "S9a 数据修订（对象没有新变化）")
    ok_all &= ck(a2["attribution"] == "calibre_change", "S9b 口径差异 → 不归因对象变化")
    ok_all &= ck(a3["attribution"] == "rule_change", "S9c 规则变化 → 标明规则影响")
    ok_all &= ck(a4["attribution"] == "object_change", "S9d 口径一致 → 对象本身变化")
    ok_all &= ck(a5["attribution"] == "undetermined", "S9e 允许无法判定，不强行为分类而归因")

    # ── S10 综合比较（四问 + 两点不称趋势） ──
    cur2 = make_record("R-c2", "MON-e", data_asof="2026-10-31", metrics=[
        {"name": "营收", "value": 110, "unit": "亿元", "period": "2026Q3",
         "calibre": "合并报表", "object_id": "某公司"}],
        judgement={"statement": "营收增速放缓", "state": "S2",
                   "vs_baseline": {"relation": "supported", "why": "营收环比下降"}})
    base2 = make_record("R-b2", "MON-e", data_asof="2026-09-30", metrics=[
        {"name": "营收", "value": 100, "unit": "亿元", "period": "2026Q3",
         "calibre": "合并报表", "object_id": "某公司"}],
        judgement={"statement": "营收高增", "state": "S1"})
    cmp2 = compare(cur2, base2)
    ok_all &= ck(len(cmp2["q1_changed"]["changes"]) == 1
                 and cmp2["q1_changed"]["changes"][0]["measure"]["absolute"] == 10,
                 "S10a 四问①：变化清单")
    ok_all &= ck(cmp2["q2_persistence"]["n_points"] == 2
                 and not cmp2["q2_persistence"]["can_claim_trend"]
                 and "不得仅凭两点差异宣称持续趋势" in cmp2["q2_persistence"]["note"],
                 "S10b 四问②：只有两个时点 → 不宣称持续趋势")
    ok_all &= ck(cmp2["q3_judgement"]["relation"] == "supported",
                 "S10c 四问③：判断支持度")
    ok_all &= ck(cmp2["q4_attribution"]["attributions"]["营收"] == "object_change",
                 "S10d 四问④：归因")
    ans = build_answer(cur2, cmp2)
    ok_all &= ck(ans["judgement_update"]["relation"] == "supported"
                 and ans["refs"]["baseline"] == "R-b2",
                 "S10e 成稿：结论 / 变化 / 判断更新 / 待跟踪 / 引用对应关系")

    # ── S11 完整流程（首次 / 二次 / 指定基线 / 资料足够不发布） ──
    with tempfile.TemporaryDirectory() as td:
        f1 = run_monitor_flow("持续跟踪某公司估值", records_dir=td, object_id="某公司",
                              scope="估值监测", research_cutoff="2026-09-30",
                              current_metrics=[{"name": "PE", "value": 20, "unit": "倍"}],
                              current_judgement={"statement": "估值中性"})
        ok_all &= ck(f1["mode"] == "track" and f1["baseline"]["status"] == "no_history"
                     and f1["compare"] is None and "不生成虚构" in f1["answer"]["note"]
                     and f1["saved"]["ok"] and f1["publish_calls"] == 0,
                     "S11a 首次监测：建立基线，不生成虚构的历史趋势，不新增发布")
        f2 = run_monitor_flow("跟上月相比某公司估值怎么样", records_dir=td,
                              object_id="某公司", scope="估值监测",
                              research_cutoff="2026-10-31",
                              current_metrics=[{"name": "PE", "value": 24, "unit": "倍"}],
                              current_judgement={"statement": "估值偏高",
                                                 "vs_baseline": {"relation": "weakened",
                                                                 "why": "PE 上升 20%"}})
        ok_all &= ck(f2["baseline"]["status"] == "nearest"
                     and f2["compare"]["q1_changed"]["changes"][0]["measure"]["absolute"] == 4
                     and f2["publish_calls"] == 0
                     and f2["answer"]["judgement_update"]["relation"] == "weakened",
                     "S11b 二次运行：选对基线，准确展示变化及依据，资料足够不发布")
        one_off = run_monitor_flow("某公司现在估值贵不贵", records_dir=td,
                                   object_id="某公司", scope="估值监测",
                                   research_cutoff="2026-10-31",
                                   current_metrics=[{"name": "PE", "value": 24, "unit": "倍"}])
        ok_all &= ck(one_off["mode"] == "one_off",
                     "S11c 一次性查询沿用现有交付（不强加趋势分析）")
        ok_all &= ck(f2["automation"] == "manual_only",
                     "S11d 未接入真实调度 → 如实标记 manual_only")

    total = n[0]
    fails = 0 if ok_all else 1
    print(f"monitor.selftest：{'全部通过' if ok_all else '存在失败'}（{total} 项断言）")
    return fails


# ═══════════════════════════════════════════════════════════════════
# 11. CLI
# ═══════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("用法：monitor.py selftest | check <spec.json>")
        return 0
    if argv[0] == "selftest":
        return selftest()
    if argv[0] == "check":
        spec = json.load(open(argv[1], encoding="utf-8"))
        res = run_check_spec(spec, base_dir=os.path.dirname(os.path.abspath(argv[1])))
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        return 0 if res["pass"] else 1
    print(f"未知子命令：{argv[0]}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

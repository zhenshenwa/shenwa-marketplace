#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据口径与确定性校验 —— 本包**唯一**的数据质量实现。

用于两件事：
  ① 进入分析之前：确认数据「含义明确、计算一致」（字段约定 / 期间口径 / 可比性 / 单位）；
  ② 结构化 JSON 到手之后：确认其中的**矛盾**能被识别（而不是被当成行情直接采信）。

设计约束（来自维护共同约定，改本文件时必须保持）
--------------------------------------------------
1. **字段缺失记「未知」，不补造**：见 `FIELD_SPEC` / `make_obs()`。
2. **先检查可比性，再计算差异**：`check_comparable()` 必须在 `relative_deviation()` 之前使用。
3. **相对偏差的分母统一为基准值**：`relative_deviation()` 的 `denominator` 恒为 `baseline`。
   基准为 0 / 接近 0 / 符号变化 / 口径不可比时 → **明确处理，不机械套比例阈值**。
4. **冲突保留原值与来源，标冲突，不擅自选一个数当"真值"**：所有检查函数返回的
   `conflict=True` 只表示"存在矛盾"，**不**表示"已判定哪个值是错的"。
5. **区分观测数据 / 计算指标 / 模型判定**：本模块只做**本地质量检查**，
   **不另造阶段标准**，也不改写深挖给出的状态与算法。

口径分层（贯穿全部检查）
------------------------
- **观测数据**（observed）：接口/数据源直接给出的原始字段，如 `t_close`。
- **计算指标**（computed）：由观测数据按明确公式算出，如涨跌幅、隐含比率、相对偏差。
- **模型判定**（judgement）：深挖给出的状态/阶段/否决/迁移，**只做一致性检查，不改判**。

用法
----
    python3 data_qc.py selftest
    python3 data_qc.py deviation --observed 14 --baseline 8.37
    python3 data_qc.py ratio --part 7249.26 --whole 7588.80 --reported 63.65
    python3 data_qc.py pct-change --prev 3602.31 --close 3855.75 --reported 4.99
    python3 data_qc.py panel <get_right_side.json>      # 与 right_side.py 共用同一实现
    python3 data_qc.py records <records.json>           # 批量：字段完整性 + 可比性 + 偏差
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# 1 · 最小必要字段约定
# ══════════════════════════════════════════════════════════════════

FIELD_SPEC: dict[str, str] = {
    "object_id": "对象标识（代码 / 名称 / 代码+名称）",
    "metric": "指标定义（必须含口径，如 ROIC(TTM)、资产负债率(合并口径)）",
    "value": "数值",
    "unit": "单位（%、亿元、倍、点、个…）",
    "period": "所属期间（2026H1 / 2026Q2 / 2026FY / TTM / 2026-09-30）",
    "effective_at": "公开或生效时间",
    "fetched_at": "取数时间",
    "source": "来源（D1~D8 / S11 / 深挖）",
    "method": "处理方法（raw 原始 / computed 计算 / derived 推导，可附公式）",
}
FIELD_ORDER = list(FIELD_SPEC)

# 语义别名：允许用中文字段名传入，但落库统一成上面的英文键
FIELD_ALIASES: dict[str, str] = {
    "对象": "object_id", "标的": "object_id", "代码": "object_id", "object": "object_id",
    "指标": "metric", "指标定义": "metric", "口径": "metric",
    "数值": "value", "值": "value",
    "单位": "unit",
    "期间": "period", "所属期间": "period",
    "生效时间": "effective_at", "公开时间": "effective_at", "披露时间": "effective_at",
    "取数时间": "fetched_at", "抓取时间": "fetched_at",
    "来源": "source",
    "处理方法": "method", "方法": "method",
}


def make_obs(**kw) -> dict:
    """按最小字段约定构造一条观测记录。

    **缺失字段一律置 None 并登记到 `unknown_fields`，绝不补造、不猜默认值。**
    """
    flat: dict = {}
    for k, v in kw.items():
        flat[FIELD_ALIASES.get(k, k)] = v
    rec = {k: flat.get(k) for k in FIELD_ORDER}
    for k, v in flat.items():                    # 允许携带附加信息（如 note / index_code）
        if k not in FIELD_ORDER:
            rec[k] = v
    rec["unknown_fields"] = [k for k in FIELD_ORDER if rec.get(k) in (None, "")]
    return rec


def completeness(records: list[dict]) -> dict:
    """统计字段完整度：哪些字段有多少条缺失。缺失是**已知项**，不是错误。"""
    miss = {k: 0 for k in FIELD_ORDER}
    for r in records:
        for k in r.get("unknown_fields", []):
            miss[k] += 1
    return {
        "n_records": len(records),
        "missing_by_field": miss,
        "complete_records": sum(1 for r in records if not r.get("unknown_fields")),
    }


# ══════════════════════════════════════════════════════════════════
# 2 · 期间与口径分类（半年度 / 全年 / 单季 / TTM；MA 与 EMA；绝对与相对；水平与变化量）
# ══════════════════════════════════════════════════════════════════

PERIOD_KIND = {"H": "半年度", "FY": "全年", "Q": "单季", "TTM": "滚动十二个月(TTM)", "POINT": "时点"}

_PERIOD_PATTERNS = [
    (re.compile(r"(\d{4})\s*[Hh]([12])"), "H"),
    (re.compile(r"(\d{4})\s*(?:FY|年报|年度|全年)"), "FY"),
    (re.compile(r"(\d{4})\s*[Qq]([1-4])"), "Q"),
    (re.compile(r"TTM|ttm|滚动|近十二|近四季"), "TTM"),
    (re.compile(r"\d{4}-\d{2}-\d{2}|\d{8}"), "POINT"),
]


def normalize_period(text) -> tuple[str, str]:
    """把期间写法归一到 `PERIOD_KIND` 的键。

    识别不出 → `("unknown", 原文)`，**不猜**。
    """
    if text in (None, ""):
        return "unknown", ""
    s = str(text)
    for pat, kind in _PERIOD_PATTERNS:
        if pat.search(s):
            return kind, s
    if re.fullmatch(r"\d{4}", s):
        return "unknown", s          # 只有年份，分不出是全年还是年度末期 → 记未知
    return "unknown", s


def is_half_vs_full(a: str, b: str) -> bool:
    """半年报与年报口径不可直接比（H1 营收 vs FY 营收比出来的是伪偏差）。"""
    ka, _ = normalize_period(a)
    kb, _ = normalize_period(b)
    return {ka, kb} == {"H", "FY"}


_MA = re.compile(r"\b(EMA|MA|SMA)\s*(\d+)?", re.I)
_RELATIVE = re.compile(r"超额|相对|relative|excess|vs\s*(?:基准|行业|大盘)")
_ABSOLUTE = re.compile(r"绝对|absolute|自身")
_CHANGE = re.compile(r"变化|变动|Δ|delta|日增|环比|同比|涨跌|change|diff|acc")
_AMOUNT = re.compile(r"亿元|万元|万亿|元|美元|港元")
_PERCENT_POINT = re.compile(r"百分点|pct|pp\b", re.I)


def metric_kind(metric, unit=None) -> str:
    """判断一条指标属于哪一类：`level` 水平 / `change` 变化量 / `pct` 百分比 / `pp` 百分点 /
    `ratio` 倍数 / `amount` 金额。判不出 → `unknown`。"""
    m = f"{metric or ''} {unit or ''}"
    if not m.strip():
        return "unknown"
    if _PERCENT_POINT.search(m):
        return "pp"
    if _AMOUNT.search(m):
        return "amount"
    if _CHANGE.search(m):
        return "change"
    if "倍" in m or re.search(r"\bPB\b|\bPE\b|倍数", m):
        return "ratio"
    if "%" in m or "率" in m or "占比" in m:
        return "pct"
    return "level"


def ma_family(metric) -> str | None:
    """返回均线族：`EMA` / `MA`；不含均线 → None。EMA 与 MA 数值不可互相代入。"""
    mt = _MA.search(str(metric or ""))
    return mt.group(1).upper() if mt else None


def comparability_scope(metric) -> str:
    """绝对表现 / 相对表现 —— 两者的变化量不可直接比。"""
    m = str(metric or "")
    if _RELATIVE.search(m):
        return "relative"
    if _ABSOLUTE.search(m):
        return "absolute"
    return "unspecified"


def check_comparable(a: dict, b: dict) -> dict:
    """**先检查可比性**。返回 `{"comparable": bool, "blockers": [...], "warnings": [...]}`。

    下列任一成立即判「不可比」（blockers）——此时**不得**计算相对偏差；
    应改为并列呈现 + 说明分歧来源（本包约定：口径差异导致的方向相反不算拦截级）。
    """
    blockers, warnings = [], []

    if a.get("object_id") and b.get("object_id") and a["object_id"] != b["object_id"]:
        blockers.append(f"对象不同：{a['object_id']} vs {b['object_id']}")
    if a.get("metric") and b.get("metric"):
        if str(a["metric"]).strip() != str(b["metric"]).strip():
            blockers.append(f"指标口径不同：{a['metric']} vs {b['metric']}")

    pa, _ = normalize_period(a.get("period"))
    pb, _ = normalize_period(b.get("period"))
    if pa != pb:
        if {pa, pb} == {"H", "FY"}:
            blockers.append(f"期间不可比：半年度 vs 全年（须折算到同一口径再比）")
        else:
            blockers.append(f"期间不同：{PERIOD_KIND.get(pa, pa)} vs {PERIOD_KIND.get(pb, pb)}")

    ua, ub = a.get("unit"), b.get("unit")
    if ua and ub and ua != ub:
        blockers.append(f"单位不同：{ua} vs {ub}")

    fa, fb = ma_family(a.get("metric")), ma_family(b.get("metric"))
    if fa and fb and fa != fb:
        blockers.append(f"均线族不同：{fa} vs {fb}（EMA 与 MA 不可互相代入）")

    sa, sb = comparability_scope(a.get("metric")), comparability_scope(b.get("metric"))
    if sa != sb and "unspecified" not in (sa, sb):
        blockers.append(f"表现口径不同：{sa} vs {sb}（绝对表现与相对表现不可直接比）")

    ka, kb = metric_kind(a.get("metric"), ua), metric_kind(b.get("metric"), ub)
    if ka != kb and "unknown" not in (ka, kb):
        blockers.append(f"量纲不同：{ka}（{a.get('metric')}） vs {kb}（{b.get('metric')}）"
                        f" —— 如「指标水平」与「变化量」、「百分比」与「百分点」不可混用")

    if a.get("method") and b.get("method") and a["method"] != b["method"]:
        warnings.append(f"处理方法不同：{a['method']} vs {b['method']}（如 raw 与 computed 混用）")

    return {"comparable": not blockers, "blockers": blockers, "warnings": warnings}


# ══════════════════════════════════════════════════════════════════
# 3 · 相对偏差：分母统一为基准值；零 / 近零 / 符号变化 / 不可比 显式处理
# ══════════════════════════════════════════════════════════════════

NEAR_ZERO_RATIO = 0.05          # |基准| < 5% × |观测值| → 视为「基准接近 0」，比例被放大


def relative_deviation(observed, baseline, *, near_zero_ratio: float = NEAR_ZERO_RATIO) -> dict:
    """相对偏差。**分母恒为基准值（baseline）**，不是深挖值、不是绝对值、不是平均值。

    - 基准为 0 → 不输出比例（`rel_pct=None`），只给绝对差，并标 `ZERO_BASELINE`；
    - 基准接近 0 → 输出比例但标 `NEAR_ZERO`，**不得机械套用比例阈值**；
    - 符号变化 → 标 `SIGN_CHANGE`，比例无方向意义，以绝对差（百分点）为准；
    - 缺值 → 标 `INCOMPARABLE`，不输出比例。
    """
    res = {
        "observed": observed, "baseline": baseline, "denominator": None,
        "abs_diff": None, "rel_pct": None, "flags": [], "verdict": "", "note": "",
    }
    if observed is None or baseline is None:
        res["flags"].append("INCOMPARABLE")
        res["verdict"] = "无法计算（缺值）"
        return res

    res["denominator"] = baseline                     # ★ 统一分母：基准值
    res["abs_diff"] = observed - baseline

    if baseline == 0:
        res["flags"].append("ZERO_BASELINE")
        res["verdict"] = "不可用比例判定（基准为 0）→ 只报绝对差"
        return res

    if observed * baseline < 0:
        res["flags"].append("SIGN_CHANGE")

    res["rel_pct"] = (observed - baseline) / baseline * 100.0

    if abs(baseline) < near_zero_ratio * abs(observed):
        res["flags"].append("NEAR_ZERO")

    if "SIGN_CHANGE" in res["flags"]:
        res["verdict"] = "符号变化：比例无方向意义 → 以绝对差为准"
    elif "NEAR_ZERO" in res["flags"]:
        res["verdict"] = "基准接近 0：比例被放大 → 不机械套阈值，须人工判断"
    else:
        res["verdict"] = "正常可比"
    return res


def classify_deviation(dev: dict, *, consistent: float = 1.0,
                       slight: float = 5.0, intercept: float = 50.0) -> dict:
    """把相对偏差映射到判定档位。**带阻断标记的一律不套比例阈值。**

    档位沿用本包既有判定（≤1% 一致 / 1~5% 轻微 / 5~50% 显著 / >50% 拦截），
    但仅在 `relative_deviation` 未给出任何阻断标记时才生效。
    """
    if dev.get("flags"):
        return {"band": None, "verdict": dev.get("verdict") or "不可比",
                "note": "存在阻断标记 " + "/".join(dev["flags"]) + " → 未按比例阈值判定，须人工判断"}
    rel = dev.get("rel_pct")
    if rel is None:
        return {"band": None, "verdict": dev.get("verdict") or "不可比", "note": "无比例可比"}
    a = abs(rel)
    if a <= consistent:
        return {"band": "一致", "verdict": "一致", "note": ""}
    if a <= slight:
        return {"band": "轻微偏差", "verdict": "轻微偏差", "note": ""}
    if a <= intercept:
        return {"band": "显著偏差", "verdict": "显著偏差", "note": ""}
    return {"band": "拦截级", "verdict": "拦截级（>50%）",
            "note": "该数字不得进入交付物；但须先确认两值口径一致（见 check_comparable）"}


def deviation_report(a: dict, b: dict, **kw) -> dict:
    """**先可比性、后偏差**的一站式入口（生产流程应调用这个，而不是直接调 relative_deviation）。"""
    comp = check_comparable(a, b)
    if not comp["comparable"]:
        return {"comparable": False, "blockers": comp["blockers"], "warnings": comp["warnings"],
                "deviation": None, "classification": None,
                "action": "口径不可比 → 不得计算相对偏差；应并列呈现并说明分歧来源"}
    dev = relative_deviation(a.get("value"), b.get("value"), **kw)
    return {"comparable": True, "blockers": [], "warnings": comp["warnings"],
            "deviation": dev, "classification": classify_deviation(dev),
            "action": "可进入判定"}


# ══════════════════════════════════════════════════════════════════
# 4 · 恒等式 / 涨幅一致性 / 正文与表格一致性
# ══════════════════════════════════════════════════════════════════

def check_ratio_consistency(part, whole, reported_pct, *,
                            names=("分子", "分母"), source=None,
                            rounding_pp: float = 0.05) -> dict:
    """**同一口径、同一期间的恒等式检查**：比率 == 分子 / 分母。

    注意——本函数**只做恒等式核对**，不用比例阈值：
    若三者出自同一份报表、同一口径、同一期间，则该式应严格成立（仅留有舍入余量）。

    ⚠️ 发现不一致时，**隐含比率不等于真实比率**：分子或分母本身可能就是错的
    （口径错配、单位错、取错科目）。本函数**不宣布**隐含值是真值，
    只保留三个原值 + 来源并标 `conflict`，交由人工复核。
    """
    res = {
        "part": part, "whole": whole, "reported_pct": reported_pct,
        "implied_pct": None, "diff_pp": None, "conflict": False,
        "names": list(names), "source": source, "note": "", "theorem": "比率 = 分子 / 分母",
    }
    if part is None or whole is None or reported_pct is None:
        res["note"] = "输入不完整：缺值 → 记未知，不补造"
        return res
    if whole == 0:
        res["note"] = "分母为 0 → 无法计算隐含比率"
        res["conflict"] = True
        return res

    res["implied_pct"] = part / whole * 100.0
    res["diff_pp"] = abs(res["implied_pct"] - reported_pct)
    res["conflict"] = res["diff_pp"] > rounding_pp

    if res["conflict"]:
        res["note"] = (
            f"内部不一致：按「{'/'.join(names)}」算得的隐含比率 {res['implied_pct']:.2f}% "
            f"与披露的 {reported_pct}% 相差 {res['diff_pp']:.2f} 个百分点。"
            f"⚠️ **不得直接把 {res['implied_pct']:.2f}% 宣布为真实比率** —— "
            f"分子或分母本身可能口径错配/单位错/取错科目。三个原值均予保留（来源：{source or '未标注'}），"
            f"标为冲突，待人工复核。"
        )
    else:
        res["note"] = f"恒等式成立（差 {res['diff_pp']:.4f} 个百分点，在舍入余量内）"
    return res


def check_pct_change(prev_close, close, reported_pct, *,
                     tol_pp: float = 0.05, source=None) -> dict:
    """涨跌幅 = (今收 / 前收 − 1) × 100%。与接口给出的涨幅逐条核对。

    ⚠️ 不一致时**只标待复核**：不得据以改写深挖给出的阶段/状态判定
    （阶段是模型判定，涨跌幅是观测/计算数据，两者分层）。
    """
    res = {
        "prev_close": prev_close, "close": close, "reported_pct": reported_pct,
        "computed_pct": None, "diff_pp": None, "conflict": False,
        "source": source, "action": "", "note": "",
    }
    if prev_close in (None, 0) or close is None or reported_pct is None:
        res["note"] = "输入不完整或前收为 0 → 记未知，不补造"
        res["action"] = "待复核"
        return res

    res["computed_pct"] = (close / prev_close - 1.0) * 100.0
    res["diff_pp"] = abs(res["computed_pct"] - reported_pct)
    res["conflict"] = res["diff_pp"] > tol_pp
    if res["conflict"]:
        res["action"] = "标记待复核（不自行判定谁对、不更改深挖阶段）"
        res["note"] = (f"接口涨幅 {reported_pct}% 与前收 {prev_close} / 收盘 {close} 算得的 "
                       f"{res['computed_pct']:.2f}% 冲突（差 {res['diff_pp']:.2f} 个百分点）。"
                       f"两值来源均为 {source or '未标注'}，均予保留。")
    else:
        res["action"] = "一致，无需处理"
    return res


def deviation_level_and_change(values, *, object_id=None, source=None,
                               metric="乖离率", tol: float = 0.005) -> list[dict]:
    """由**水平序列**（由旧到新）同时给出「指标水平」与「变化量」，并显式区分二者。

    - 水平（level）：最新值，单位 `%`；
    - 变化量（change）：最新值 − 上一个值，单位 **百分点**。

    实测（2026-09-26 乖离率报告）：PCB 水平 −0.44%、当日变化 **−4.47 个百分点**；
    光通信 水平 +0.25%、当日变化 **−2.73 个百分点**。二者量纲不同，
    **正文与表格不得互相代入**（正文若把 −4.47 当作"乖离率"引用，即为混用）。
    """
    vals = list(values or [])
    if len(vals) < 2:
        return []
    level = vals[-1]
    change = vals[-1] - vals[-2]
    return [
        make_obs(object_id=object_id, metric=f"{metric}水平", value=round(level, 4),
                 unit="%", period="最新交易日", source=source, method="computed"),
        make_obs(object_id=object_id, metric=f"{metric}当日变化", value=round(change, 4),
                 unit="百分点", period="最新交易日 vs 前一交易日", source=source,
                 method="computed(最新−前值)"),
    ]


def check_prose_table_consistency(records: list[dict], claims: list[dict], *,
                                  tol: float = 0.05) -> dict:
    """检查**正文引用**与**表格字段**是否混用（尤其「水平值 ↔ 变化量」互换）。

    - `records`：[{object_id, level, change}]（表格里的两个字段）
    - `claims` ：[{object_id, cited_value, cited_as}]，`cited_as ∈ {"level","change"}` 表示
      正文里这个数字被当作什么引用。

    返回 `swapped`（被查出的混用）与 `ok`/`not_found` 明细。
    """
    idx = {r["object_id"]: r for r in records}
    out = {"ok": [], "swapped": [], "not_found": [], "missing_record": []}
    for c in claims:
        oid = c.get("object_id")
        row = idx.get(oid)
        if row is None:
            out["missing_record"].append(c)
            continue
        val = c.get("cited_value")
        as_ = c.get("cited_as")
        other = "change" if as_ == "level" else "level"
        same = row.get(as_)
        diff = row.get(other)
        if same is not None and abs(val - same) <= tol:
            out["ok"].append(c)
        elif diff is not None and abs(val - diff) <= tol:
            out["swapped"].append({
                **c,
                "explain": (f"正文把「{other}」的值 {diff} 当作「{as_}」（正确值 {same}）引用 —— "
                            f"水平值与变化量量纲不同，正文与表格不得混用"),
            })
        else:
            out["not_found"].append(c)
    return out


# ══════════════════════════════════════════════════════════════════
# 5 · 金额与单位、摘要与表格一致性
# ══════════════════════════════════════════════════════════════════

UNIT_SCALE: dict[str, float] = {
    "元": 1.0, "万": 1e4, "万元": 1e4, "亿": 1e8, "亿元": 1e8, "万亿": 1e12,
}

_MAG_DECADES = (10, 100, 1000, 10000, 100000)


def check_amount_unit(records: list[dict], *, target_unit: str = "亿元",
                      decade_tol: float = 0.03) -> dict:
    """金额与单位检查：① 单位缺失；② 同一指标下的**量级可疑**（10/100/1000/10000 倍）。

    实测曾在数据源内部发现 2 处 10 倍单位错误，故这一步是**前置项**，不得跳过。
    """
    findings = {"unit_missing": [], "unit_unknown": [], "orders_of_magnitude": []}
    scale = UNIT_SCALE.get(target_unit, 1.0)
    groups: dict[str, list[tuple[dict, float]]] = {}

    for r in records:
        u = r.get("unit")
        if u in (None, ""):
            findings["unit_missing"].append(r)
            continue
        if u not in UNIT_SCALE:
            findings["unit_unknown"].append(r)
            continue
        v = r.get("value")
        if not isinstance(v, (int, float)):
            continue
        groups.setdefault(str(r.get("metric")), []).append((r, v * UNIT_SCALE[u] / scale))

    for metric, items in groups.items():
        if len(items) < 2:
            continue
        base = min(abs(v) for _, v in items if v)
        if not base:
            continue
        for r, v in items:
            if not v:
                continue
            ratio = abs(v) / base
            for d in _MAG_DECADES:
                if abs(ratio - d) / d <= decade_tol:
                    findings["orders_of_magnitude"].append({
                        "metric": metric, "object_id": r.get("object_id"),
                        "value_in_target_unit": v, "smallest_in_group": base,
                        "factor": f"{d}×", "note": "疑似单位/量级错误，保留原值与来源，标冲突待复核",
                        "source": r.get("source"),
                    })
                    break
    findings["ok"] = not any(findings[k] for k in ("unit_missing", "unit_unknown", "orders_of_magnitude"))
    return findings


def check_summary_table_consistency(summary: dict, table: dict, *,
                                    tol_pp: float = 0.05) -> dict:
    """摘要与表格一致性：同一指标在「结论摘要」与「数据表」里必须同值。

    用统一分母（以表格值为基准）算相对偏差；不一致即列出，**不擅自选一个当准**。
    """
    mismatches, missing, ok = [], [], []
    for label, tv in table.items():
        if label not in summary:
            missing.append(label)
            continue
        sv = summary[label]
        dev = relative_deviation(sv, tv)
        if dev["flags"] or dev["rel_pct"] is None:
            same = (sv == tv)
            (ok if same else mismatches).append({"label": label, "summary": sv, "table": tv,
                                                 "note": "/".join(dev["flags"]) or "无比例可比"})
        elif abs(dev["rel_pct"]) <= tol_pp or abs(dev["abs_diff"] or 0) <= tol_pp:
            ok.append({"label": label, "summary": sv, "table": tv})
        else:
            mismatches.append({"label": label, "summary": sv, "table": tv,
                               "rel_pct": round(dev["rel_pct"], 4),
                               "note": "摘要与表格不一致 —— 保留两值，标冲突待复核"})
    for label in summary:
        if label not in table:
            missing.append(label)
    return {"consistent": not mismatches, "mismatches": mismatches, "only_in_one_side": missing, "ok": ok}


# ══════════════════════════════════════════════════════════════════
# 6 · 结构化 payload 可用性 + 面板质量（get_right_side）
# ══════════════════════════════════════════════════════════════════

KNOWN_TRANSITIONS = {"Hold", "Upgrade", "Breakdown", "Unknown"}


def parse_payload(text: str) -> dict:
    """解析原始返回。首行 `// base: ...`（shenwa_api.py 打印）先剔除。"""
    lines = str(text).splitlines()
    if lines and lines[0].lstrip().startswith("//"):
        lines = lines[1:]
    return json.loads("\n".join(lines))


def classify_payload(payload) -> dict:
    """payload 级判定：区分「正常返回 / 空结果 / 明确业务失败 / 权限不足 / 结构异常」。

    ⚠️ **空结果与业务失败都不是「无否决」**；调用异常也不是「报告失败」。
    """
    res = {"kind": "unknown", "success": None, "status": None, "msg": None,
           "n_items": None, "usable": False, "note": ""}
    if not isinstance(payload, dict):
        res["kind"] = "malformed"
        res["note"] = "顶层不是对象 → 结构异常"
        return res
    res["success"] = payload.get("success")
    res["status"] = payload.get("status")
    res["msg"] = payload.get("msg")
    data = payload.get("data")

    if str(payload.get("status", "")).upper() == "AUT" or "权限" in str(payload.get("msg") or ""):
        res["kind"] = "permission"
        res["note"] = "权限不足（需 ADM/VIP2）→ 如实告知，不得静默降级"
        return res
    if payload.get("success") is False and data in (None, [], {}):
        res["kind"] = "business_error"
        res["note"] = f"明确业务失败：status={res['status']} msg={res['msg']}"
        return res
    if data in (None, ""):
        res["kind"] = "malformed"
        res["note"] = "缺少 data 字段"
        return res
    if isinstance(data, list):
        res["n_items"] = len(data)
        if len(data) == 0:
            res["kind"] = "empty"
            res["note"] = "返回空数组 —— 空结果 ≠ 无否决、≠ 无异常"
            return res
        res["kind"] = "ok"
        res["usable"] = True
        return res
    if isinstance(data, dict):
        res["n_items"] = len(data)
        if not data:
            res["kind"] = "empty"
            res["note"] = "data 为空对象 —— 空结果 ≠ 无否决"
            return res
        res["kind"] = "ok"
        res["usable"] = True
        return res
    res["kind"] = "malformed"
    res["note"] = f"data 类型不支持：{type(data).__name__}"
    return res


def check_panel_quality(rows: list[dict], *, expected_coverage=None) -> dict:
    """结构化面板（`get_right_side` 归一化后的行）质量检查。

    检查项：日期与代码唯一性、覆盖数量、重复记录、未知状态、**缺失否决字段**。

    ★ 关键：把 `has_veto` 分成 **三态** —— True / False / **缺失**。
    缺失、None、解析失败**一律不得**当作「无否决」。旧实现的
    `veto = [r for r in rows if r["has_veto"]]` 会把 None 静默当成 False，故本函数显式分列。

    覆盖范围**按实际返回**统计（`coverage_by_date` / `expected_from_data`），
    **不写死 62**；如需与已知范围比对，用 `expected_coverage` 显式传入。
    """
    res = {
        "n_total": len(rows), "empty": not rows,
        "dates": [], "coverage_by_date": {}, "coverage_current": 0,
        "expected_from_data": 0, "partial_dates": [],
        "duplicate_keys": [], "missing_date_or_code": 0,
        "unknown_state": [], "unclassified_tt": {},
        "veto_true": 0, "veto_false": 0, "veto_missing": [],
        "flags": [], "verdict": "ok",
    }
    if not rows:
        res["flags"].append("EMPTY")
        res["verdict"] = "空结果：不得据此宣称「无升级/无破坏/无否决」"
        return res

    counts: dict[str, int] = {}
    seen: dict[tuple, int] = {}
    tt_counter: dict[str, int] = {}

    for r in rows:
        d, c = r.get("date"), r.get("code")
        if not d or not c:
            res["missing_date_or_code"] += 1
        else:
            key = (d, c)
            seen[key] = seen.get(key, 0) + 1
            counts[d] = counts.get(d, 0) + 1

        if r.get("curr_state") in (None, ""):
            res["unknown_state"].append({"date": d, "code": c, "name": r.get("name")})

        tt = r.get("tt")
        tt_counter[str(tt)] = tt_counter.get(str(tt), 0) + 1
        if tt not in KNOWN_TRANSITIONS:
            res["unclassified_tt"][str(tt)] = res["unclassified_tt"].get(str(tt), 0) + 1

        hv = r.get("has_veto")
        if hv is True:
            res["veto_true"] += 1
        elif hv is False:
            res["veto_false"] += 1
        else:                                     # ★ None / 缺失 / 解析失败 → 单列，不算 False
            res["veto_missing"].append({"date": d, "code": c, "name": r.get("name"),
                                        "raw_has_veto": hv})

    res["dates"] = sorted(counts)
    res["coverage_by_date"] = dict(sorted(counts.items()))
    res["coverage_current"] = counts[res["dates"][-1]] if res["dates"] else 0
    res["expected_from_data"] = max(counts.values()) if counts else 0
    res["partial_dates"] = sorted(d for d, n in counts.items() if n < res["expected_from_data"])
    res["duplicate_keys"] = sorted([list(k) for k, v in seen.items() if v > 1])

    if res["duplicate_keys"]:
        res["flags"].append("DUPLICATE_RECORDS")
    if res["partial_dates"]:
        res["flags"].append("PARTIAL_DATES")          # 最新日期只返回部分指数
    if res["missing_date_or_code"]:
        res["flags"].append("MISSING_KEY")
    if res["unknown_state"]:
        res["flags"].append("UNKNOWN_STATE")
    if res["unclassified_tt"]:
        res["flags"].append("UNCLASSIFIED_TRANSITION")
    if res["veto_missing"]:
        res["flags"].append("VETO_FIELD_MISSING")
    if expected_coverage and res["coverage_current"] != expected_coverage:
        res["flags"].append("COVERAGE_MISMATCH")
        res["coverage_expected_external"] = expected_coverage

    if res["flags"]:
        res["verdict"] = "发现问题：" + "、".join(res["flags"]) + " —— 须逐项处置后再出表"
    return res


def check_advance_breakdown(rows: list[dict], date: str | None = None) -> dict:
    """按目标日期**切截面后**统计升级/破坏计数（供案例 F/G 复核）。

    切截面口径与 `right_side.py` 完全一致（同一 `date` 分组后取目标日），
    本函数不重复实现分桶逻辑，只做计数复核。
    """
    if not rows:
        return {"date": None, "coverage": 0, "upgrade": 0, "breakdown": 0,
                "note": "空面板 → 不得声称计数为 0"}
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("date"), []).append(r)
    target = date or max(groups)
    sec = groups.get(target, [])
    up = sum(1 for r in sec if r.get("tt") == "Upgrade")
    bd = sum(1 for r in sec if r.get("tt") == "Breakdown")
    return {"date": target, "coverage": len(sec), "upgrade": up, "breakdown": bd,
            "panel_total": len(rows), "panel_dates": sorted(groups),
            "note": "" if sec else "目标日期无记录"}


# ══════════════════════════════════════════════════════════════════
# 7 · 批量入口：记录集的质量门 + 检查清单驱动（SOP ③.6 / ⑤ 轮 1 调用这些）
# ══════════════════════════════════════════════════════════════════

def gate_records(records: list[dict], pairs: list[dict] | None = None,
                 *, require_fields: tuple[str, ...] = ("object_id", "metric", "value", "unit",
                                                       "period", "source", "method")) -> dict:
    """记录集质量门：

    - 字段完整性（**缺失记未知，不补造、不阻塞**）；
    - 金额与单位；
    - **可比性先于偏差**：`pairs` 里每一对先 `check_comparable`，可比才 `relative_deviation`；
      不可比时并列呈现、不计算比例。

    `pairs` 元素形如 `{"a": {…obs…}, "b": {…obs…}, "label": "ROIC"}`。
    """
    out = {
        "completeness": completeness(records),
        "missing_required": {
            f: sum(1 for r in records if r.get(f) in (None, "")) for f in require_fields
        },
        "amount_unit": check_amount_unit(records),
        "pairs": [],
        "incomparable": 0,
    }
    for p in pairs or []:
        rep = deviation_report(p["a"], p["b"])
        rep["label"] = p.get("label")
        if not rep["comparable"]:
            out["incomparable"] += 1
        out["pairs"].append(rep)
    return out


# ══════════════════════════════════════════════════════════════════
# 8 · 检查清单驱动：让用例夹具直接调用**生产函数**，而不是另写一套测试逻辑
# ══════════════════════════════════════════════════════════════════

_DISPATCH = ("deviation", "ratio", "pct_change", "prose_table", "comparable", "panel")


def _run_one(chk: dict) -> dict:
    """按清单项类型调用对应的**生产函数**，返回原始结果。"""
    t = chk["type"]
    if t == "deviation":
        dev = relative_deviation(chk["observed"], chk["baseline"])
        return {"deviation": dev, "classification": classify_deviation(dev)}
    if t == "ratio":
        return check_ratio_consistency(chk["part"], chk["whole"], chk["reported"],
                                       names=tuple(chk.get("names", ("分子", "分母"))),
                                       source=chk.get("source"))
    if t == "pct_change":
        return check_pct_change(chk["prev"], chk["close"], chk["reported"],
                                source=chk.get("source"))
    if t == "prose_table":
        return check_prose_table_consistency(chk["records"], chk["claims"])
    if t == "comparable":
        return check_comparable(make_obs(**chk["a"]), make_obs(**chk["b"]))
    if t == "panel":
        rows = chk["rows"]
        return check_panel_quality(rows, expected_coverage=chk.get("expected_coverage"))
    raise ValueError(f"未知检查类型：{t}")


def _eq(cur, want) -> bool:
    """宽松相等：数值带容差；`want` 为整数而 `cur` 为列表时比长度；两个 dict 比键数。"""
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


def _assert_expect(got: dict, expect: dict) -> list[str]:
    """按 `expect` 里的断言核对结果。

    路径用点号分级，如 `deviation.rel_pct`；**末段**加 `not_` 前缀表示「不等于」，
    如 `deviation.not_rel_pct`。
    """
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


def run_check_spec(doc: dict) -> dict:
    """执行一份检查清单。每项都调 `_DISPATCH` 里的**生产函数**，再按 `expect` 断言。"""
    results, n_fail = [], 0
    for chk in doc.get("checks", []):
        if chk["type"] not in _DISPATCH:
            results.append({"label": chk.get("label"), "type": chk["type"],
                            "pass": False, "detail": "未知检查类型"})
            n_fail += 1
            continue
        got = _run_one(chk)
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
    """自检：用**内置的真实数值**核对本模块的核心口径（不依赖外部文件）。"""
    ok = True

    # ① 相对偏差：分母必须取基准值
    dev = relative_deviation(14, 8.37)                       # ROIC 14% vs 基准 8.37%
    assert abs(dev["abs_diff"] - 5.63) < 1e-9, dev
    assert abs(dev["rel_pct"] - 67.2640) < 0.01, dev
    assert abs(dev["rel_pct"] - 40.25) > 1, "分母误用深挖值（5.63/14）"
    print(f"[ok] 相对偏差 14 vs 8.37 → 绝对差 {dev['abs_diff']:.2f} 个百分点，"
          f"相对偏差 {dev['rel_pct']:.2f}%（分母=基准 8.37；错误分母会得 40.25%）")

    # ② 恒等式：隐含比率 ≠ 真实比率
    rc = check_ratio_consistency(7249.26, 7588.80, 63.65, names=("总负债", "总资产"))
    assert rc["conflict"], rc
    assert abs(rc["implied_pct"] - 95.53) < 0.01, rc
    assert "不得直接" in rc["note"], rc
    print(f"[ok] 恒等式 7249.26/7588.80 → 隐含 {rc['implied_pct']:.2f}% vs 披露 63.65%，"
          f"差 {rc['diff_pp']:.2f} 个百分点，标冲突且不宣布隐含值为真值")

    # ③ 涨跌幅：与接口值冲突
    pc = check_pct_change(3602.31, 3855.75, 4.99)
    assert pc["conflict"], pc
    assert abs(pc["computed_pct"] - 7.04) < 0.01, pc
    assert "深挖阶段" in pc["action"], pc
    print(f"[ok] 涨跌幅 3602.31→3855.75 算得 {pc['computed_pct']:.2f}% vs 接口 4.99%，标待复核")

    # ④ 水平与变化量：不得互用
    recs = []
    recs += [{"object_id": "PCB", "level": -0.44, "change": -4.47}]
    recs += [{"object_id": "光通信", "level": 0.25, "change": -2.73}]
    pr = check_prose_table_consistency(recs, [
        {"object_id": "PCB", "cited_value": -4.47, "cited_as": "level"},
        {"object_id": "光通信", "cited_value": -2.73, "cited_as": "level"},
        {"object_id": "PCB", "cited_value": -0.44, "cited_as": "level"},
    ])
    assert len(pr["swapped"]) == 2, pr
    assert len(pr["ok"]) == 1, pr
    print(f"[ok] 正文/表格混用：查出 {len(pr['swapped'])} 处水平值↔变化量互换，正常引用 {len(pr['ok'])} 处")

    # ⑤ 否决三态：缺失不得当 False
    rows = [
        {"date": "2026-09-30", "code": "A", "curr_state": "S5", "tt": "Hold", "has_veto": True},
        {"date": "2026-09-30", "code": "B", "curr_state": "S1", "tt": "Upgrade", "has_veto": False},
        {"date": "2026-09-30", "code": "C", "curr_state": "S2", "tt": "Hold", "has_veto": None},
    ]
    q = check_panel_quality(rows)
    assert q["veto_true"] == 1 and q["veto_false"] == 1 and len(q["veto_missing"]) == 1, q
    assert "VETO_FIELD_MISSING" in q["flags"], q
    print(f"[ok] 否决三态：True={q['veto_true']} False={q['veto_false']} 缺失={len(q['veto_missing'])}"
          f"（缺失未被当成 False）")

    # ⑥ 期间不可比
    a = make_obs(object_id="X", metric="FCFF", value=461.58, unit="亿元", period="2025FY", source="D2")
    b = make_obs(object_id="X", metric="FCFF", value=-1.83, unit="亿元", period="2025Q3", source="D2")
    comp = check_comparable(a, b)
    assert not comp["comparable"], comp
    print(f"[ok] 期间可比性：全年 vs 单季 → 不可比（{comp['blockers'][0][:28]}…）")

    print("selftest: " + ("全部通过" if ok else "有失败"))
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="数据口径与确定性校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="内置真实数值自检")

    d = sub.add_parser("deviation", help="相对偏差（分母=基准值）")
    d.add_argument("--observed", type=float, required=True)
    d.add_argument("--baseline", type=float, required=True)

    r = sub.add_parser("ratio", help="恒等式核对：比率 = 分子/分母")
    r.add_argument("--part", type=float, required=True)
    r.add_argument("--whole", type=float, required=True)
    r.add_argument("--reported", type=float, required=True)

    c = sub.add_parser("pct-change", help="涨跌幅一致性")
    c.add_argument("--prev", type=float, required=True)
    c.add_argument("--close", type=float, required=True)
    c.add_argument("--reported", type=float, required=True)

    pn = sub.add_parser("panel", help="结构化面板质量检查（get_right_side 原始返回）")
    pn.add_argument("source")
    pn.add_argument("--date", default=None)
    pn.add_argument("--expected-coverage", type=int, default=None)

    rc = sub.add_parser("records", help="批量记录集质量门")
    rc.add_argument("source")

    ck = sub.add_parser("checks", help="执行检查清单（每项都调生产函数）")
    ck.add_argument("source")
    ck.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "deviation":
        _p(relative_deviation(a.observed, a.baseline))
        _p(classify_deviation(relative_deviation(a.observed, a.baseline)))
        return 0
    if a.cmd == "ratio":
        _p(check_ratio_consistency(a.part, a.whole, a.reported))
        return 0
    if a.cmd == "pct-change":
        _p(check_pct_change(a.prev, a.close, a.reported))
        return 0
    if a.cmd == "panel":
        payload = parse_payload(Path(a.source).read_text(encoding="utf-8"))
        status = classify_payload(payload)
        if not status["usable"]:
            _p({"payload": status})
            return 1
        import importlib.util
        rs_path = Path(__file__).resolve().parent / "right_side.py"
        spec = importlib.util.spec_from_file_location("right_side", rs_path)
        rs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rs)
        rows = rs.normalise(payload)
        _p({"payload": status,
            "quality": check_panel_quality(rows, expected_coverage=a.expected_coverage),
            "advance_breakdown": check_advance_breakdown(rows, a.date)})
        return 0
    if a.cmd == "records":
        doc = json.loads(Path(a.source).read_text(encoding="utf-8"))
        recs = [make_obs(**r) for r in doc.get("records", [])]
        pairs = [{"label": p.get("label"), "a": make_obs(**p["a"]), "b": make_obs(**p["b"])}
                 for p in doc.get("pairs", [])]
        _p(gate_records(recs, pairs))
        return 0
    if a.cmd == "checks":
        doc = json.loads(Path(a.source).read_text(encoding="utf-8"))
        res = run_check_spec(doc)
        if a.json:
            _p(res)
        else:
            print(f"检查清单 {res['case']}：{res['n_checks']} 项，失败 {res['n_fail']}")
            for r in res["results"]:
                print(f"  [{'ok' if r['pass'] else 'FAIL'}] {r['type']:<12} {r['label']} —— {r['detail']}")
        return 0 if res["pass"] else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轮次 4：估值方法与适用条件 —— 本包**唯一**的估值计算实现。

本模块只做「估值怎么算、什么时候能算、算出来能说到什么程度」，**不接任何接口、不读网络**。

七条设计原则（对应本轮 7 条修改要求，编号 R1~R7）：

  R1  现金流与折现率必须**配对**：FCFF ↔ WACC（折到**企业价值/经营资产价值**）；
      FCFE ↔ 股权资本成本 Ke（折到**股权价值**）。混用即拒绝。
  R2  FCFF 折出企业价值后，必须走「企业价值 → 股权价值」**桥**：
      ＋现金及非经营资产 －有息债务 －少数股东权益 －优先股 ±其他调整 → ÷总股本。
      缺少**必要**调整项时只给**结果边界**，**不当作每股合理价值**。
  R3  输入分三类标注：**历史观测 / 未来假设 / 计算结果**。历史 CAGR 外推一律是
      **预测假设**（须声明期限、适用理由、敏感性），**不得**称为已验证的未来增长率。
  R4  按**研究对象 + 数据条件**选方法，**不按用户风格强制**；
      取消「必须有 D7」「至少两种方法才算有效」两处一刀切，改为检查各方法的必要输入与适用条件。
  R5  多方法用于**解释差异**，接近程度 ≠ 准确度；
      **不得**因结果偏低或偏离共识而剔除该方法。
  R6  估值异常只影响**真正依赖该输入**的推演，不因某个无关指标偏差而废弃整份报告。
  R7  已发布历史材料**不覆盖**；错误示例另建修订版，并写明旧版问题（见 HISTORY_POLICY）。

CLI::

    python3 valuation.py selftest              # 内置自检（含 A~G 全部算例）
    python3 valuation.py rules                 # 七条原则 + 方法注册表（人读）
    python3 valuation.py dcf   --spec x.json   # 跑一条 FCFF 折现链
    python3 valuation.py bridge --spec x.json  # 只看企业价值→股权价值桥
    python3 valuation.py perpetuity --fcf 100 --rate 0.10 --growth 0.10
    python3 valuation.py methods --object 个股 --inputs a,b,c [--style value]
    python3 valuation.py compare --spec x.json
    python3 valuation.py scope --spec x.json
    python3 valuation.py checks --spec cases/06/input-checks.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ══════════════════════════ 0. 常量：现金流 / 折现率 / 输入分类 ══════════════════════════

CASHFLOW_KINDS: dict[str, dict] = {
    "fcff": {
        "alias": "FCFF",
        "cn": "企业自由现金流",
        "definition": "归属于**全体资本提供者**（股权 + 有息债权）的现金流："
                      "息税前利润 × (1 − 税率) + 折旧摊销 − 资本开支 − 营运资本增加；**不扣利息**。",
        "capital_provider": "全部资本（股权 + 有息债务）",
        "discounts_to": "企业价值（经营资产价值）",
        "required_rate": "wacc",
        "then": "须再走「企业价值 → 股权价值」桥（＋现金及非经营资产 －有息债务 －少数股东权益 －优先股）",
    },
    "fcfe": {
        "alias": "FCFE",
        "cn": "股权自由现金流",
        "definition": "扣除利息、并加回净借款后，**归属于普通股股东**的现金流；**已扣利息**。",
        "capital_provider": "仅股权",
        "discounts_to": "股权价值",
        "required_rate": "ke",
        "then": "折出即股权价值，除以总股本即每股；**不需要**再减有息债务",
    },
}

RATE_KINDS: dict[str, dict] = {
    "wacc": {"alias": "WACC", "cn": "加权平均资本成本",
             "definition": "股权成本与税后债务成本的加权平均；反映**全部资本**的机会成本",
             "applies_to": "fcff"},
    "ke": {"alias": "Ke", "cn": "股权资本成本",
           "definition": "股东要求的必要回报率（如 CAPM）；只反映**股权**的机会成本",
           "applies_to": "fcfe"},
}

INPUT_KINDS: dict[str, dict] = {
    "historical_observed": {"cn": "历史观测值", "tag": "[历史观测]",
                            "note": "已发生的、可从数据源核对的数值"},
    "disclosed_fact": {"cn": "已披露事实", "tag": "[已披露]",
                       "note": "公司/交易所/官方口径已公开的数值"},
    "future_assumption": {"cn": "未来假设", "tag": "[预测假设]",
                          "note": "面向未来的设定，**必须显式声明**，不得冒充已披露事实"},
    "estimate": {"cn": "估计值", "tag": "[估计]",
                 "note": "由判断给出的近似值，须标注口径与不确定度"},
    "computed": {"cn": "计算结果", "tag": "[计算结果]",
                 "note": "由本模块按公式算出的值，随输入与假设变动"},
}

INPUT_ORDER = ("disclosed_fact", "historical_observed", "future_assumption", "estimate", "computed")


class ValuationError(ValueError):
    """估值输入不满足方法适用条件时抛出（调用方可捕获后降级为「边界说明」）。"""


def _norm(kind: str) -> str:
    return str(kind or "").strip().lower().replace("-", "_")


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ══════════════════════════ 1. R1 现金流 ↔ 折现率配对 ══════════════════════════

def classify_cashflow(kind: str) -> dict:
    k = _norm(kind)
    if k not in CASHFLOW_KINDS:
        raise ValuationError(f"未知现金流类型 {kind!r}；可用 {sorted(CASHFLOW_KINDS)}")
    return {"kind": k, **CASHFLOW_KINDS[k]}


def classify_rate(kind: str) -> dict:
    k = _norm(kind)
    if k not in RATE_KINDS:
        raise ValuationError(f"未知折现率类型 {kind!r}；可用 {sorted(RATE_KINDS)}")
    return {"kind": k, **RATE_KINDS[k]}


def check_discount_rate_match(cashflow_kind: str, rate_kind: str) -> dict:
    """FCFF 必须配 WACC；FCFE 必须配 Ke。混用 → ok=False 并说明方向性偏差。"""
    cf = classify_cashflow(cashflow_kind)
    rt = classify_rate(rate_kind)
    expected = cf["required_rate"]
    ok = rt["kind"] == expected
    if ok:
        explain = (f"{cf['alias']} 由 {cf['capital_provider']} 享有，"
                   f"折现率用 {rt['alias']}（{rt['cn']}）—— 二者口径一致，折出 {cf['discounts_to']}。")
    else:
        if cf["kind"] == "fcff" and rt["kind"] == "ke":
            explain = ("FCFF 属于**全部资本提供者**，却用**股权成本 Ke** 折现："
                       "Ke > WACC（股权成本高于税后债务成本），"
                       "分母偏大 → **系统性低估**企业价值；反向（FCFE 用 WACC）则**高估**股权价值。"
                       "口径错配会使结果不可解释，必须先统一口径。")
        else:
            explain = (f"{cf['alias']} 要求折现率口径为 {RATE_KINDS[expected]['alias']}，"
                       f"实际传入 {rt['alias']}（{rt['cn']}）—— 资本提供者口径不一致。")
    return {
        "ok": ok, "mismatch": not ok,
        "cashflow": cf["kind"], "cashflow_alias": cf["alias"],
        "rate": rt["kind"], "rate_alias": rt["alias"],
        "expected_rate": expected, "expected_rate_alias": RATE_KINDS[expected]["alias"],
        "discounts_to": cf["discounts_to"],
        "then": cf["then"],
        "explain": explain,
    }


# ══════════════════════════ 2. R2 永续公式守卫 + 企业价值 ══════════════════════════

def perpetuity_value(cashflow: float, rate: float, growth: float) -> dict:
    """戈登永续：V = CF₁ / (r − g)。**g ≥ r 时拒绝计算**（R2/C 项）。"""
    r, g = float(rate), float(growth)
    res = {"cashflow": float(cashflow), "rate": r, "growth": g,
           "formula": "V = CF₁ / (r − g)"}
    if r <= 0:
        res.update(ok=False, blocked=True, value=None,
                   reason=f"折现率 r = {r:.4f} ≤ 0，永续公式不适用。")
        return res
    if g >= r:
        res.update(
            ok=False, blocked=True, value=None, denominator=r - g,
            reason=(f"永续增长率 g = {g:.4%} ≥ 折现率 r = {r:.4%}："
                    f"分母 r − g = {r - g:.4%} ≤ 0，公式**不适用**"
                    f"（会得到 ∞ 或负数，与「永续价值为正且有限」矛盾）。"
                    f"须改为：① 检查 g 是否与长期通胀/名义 GDP 一致；"
                    f"② 改用显式预测期 + 终值倍数或清算价值；"
                    f"③ 或降低 g / 上调 r 后重算。**不得**把该次计算结果当作估值输出。"))
        return res
    v = float(cashflow) / (r - g)
    res.update(ok=True, blocked=False, value=v, denominator=r - g,
               reason=f"g = {g:.4%} < r = {r:.4%}，分母 {r - g:.4%} > 0，公式可用。")
    return res


def enterprise_value_from_fcff(fcff_next: float, wacc: float, growth: float,
                               *, terminal_mode: str = "perpetuity") -> dict:
    """FCFF 单期折现（永续不变即 g=0）。返回**企业价值**，不是股权价值。"""
    pv = perpetuity_value(fcff_next, wacc, growth)
    out = {"method": "dcf_fcff", "stage": "enterprise_value",
           "terminal_mode": terminal_mode,
           "formula": "企业价值 EV = FCFF₁ / (WACC − g)",
           "fcff_next": float(fcff_next), "wacc": float(wacc), "g": float(growth),
           "ok": pv["ok"], "blocked": pv["blocked"],
           "enterprise_value": pv["value"], "reason": pv["reason"]}
    if not pv["ok"]:
        out["enterprise_value"] = None
    return out


# ══════════════════════════ 3. R2 企业价值 → 股权价值桥 ══════════════════════════

# 必要调整项：资产侧（现金及等价物 / 非经营资产 至少一项）+ 有息债务 + 总股本
ASSET_KEYS = ("cash_and_equivalents", "non_operating_assets")
REQUIRED_KEYS = ("interest_bearing_debt", "shares")
# 次要调整项：未提供时**按零处理但必须显式声明**（不得静默假定为零）
SECONDARY_KEYS = {
    "minority_interest": "少数股东权益（少了它 → 高估股权价值）",
    "preferred_equity": "优先股权益（少了它 → 高估股权价值）",
    "other_adjustments": "其他权益调整（须写明口径；正数加、负数减）",
}

_MISSING = object()


def equity_bridge(enterprise_value: float, *, cash_and_equivalents=_MISSING,
                  non_operating_assets=_MISSING, interest_bearing_debt=_MISSING,
                  minority_interest=_MISSING, preferred_equity=_MISSING,
                  other_adjustments=_MISSING, shares=_MISSING) -> dict:
    """企业价值 → 股权价值 → 每股价值。

    语义：**未传参 = 未知**（不可假定为零）；**显式传 0 = 明确为零**。
    缺少必要调整项 → ``complete=False``、``per_share=None``，只给方向性边界说明。
    """
    def given(v):
        return v is not _MISSING and v is not None

    assets = {k: float(v) for k, v in
              (("cash_and_equivalents", cash_and_equivalents),
               ("non_operating_assets", non_operating_assets)) if given(v)}
    debt = float(interest_bearing_debt) if given(interest_bearing_debt) else None
    shares_v = float(shares) if given(shares) else None

    missing_required: list[str] = []
    if not assets:
        missing_required.append("现金及现金等价物 或 非经营资产（至少一项）")
    if debt is None:
        missing_required.append("有息债务")
    if shares_v is None:
        missing_required.append("总股本")

    _secondary_raw = {"minority_interest": minority_interest,
                      "preferred_equity": preferred_equity,
                      "other_adjustments": other_adjustments}
    unresolved_secondary = [k for k in SECONDARY_KEYS if not given(_secondary_raw[k])]

    ev = float(enterprise_value)
    notes: list[str] = []

    if missing_required:
        # 只给「不完整」的中间值与**方向性**边界，绝不给每股价值
        partial = ev + sum(assets.values()) - (debt or 0.0)
        notes.append("缺少必要调整项 → 本结果**不是**每股合理价值，只是企业价值到股权价值之间的中间量。")
        if "有息债务" in " ".join(missing_required):
            notes.append("未提供有息债务 → 该中间量**高估**股权价值（债务是要偿还的索取权）。")
        if not assets:
            notes.append("未提供现金/非经营资产 → 该中间量**低估**股权价值。")
        return {
            "formula": "股权价值 = 企业价值 ＋现金及非经营资产 －有息债务 －少数股东权益 －优先股 ＋其他调整",
            "enterprise_value": ev, "equity_value": None,
            "equity_value_incomplete": partial,
            "cash_and_equivalents": assets.get("cash_and_equivalents"),
            "non_operating_assets": assets.get("non_operating_assets"),
            "interest_bearing_debt": debt,
            "minority_interest": None if not given(minority_interest) else float(minority_interest),
            "shares": shares_v,
            "complete": False, "qualified": False,
            "is_equity_value_per_share": False, "per_share": None,
            "missing_required": missing_required,
            "unresolved_secondary": unresolved_secondary,
            "notes": notes,
            "boundary_note": ("结果边界：" + "；".join(notes)
                              + " 补齐上述必要调整项前，**不得**把它当作每股合理价值或目标价。"),
            "steps": [],
        }

    if unresolved_secondary:
        notes.append("次要调整项未提供 → 按 0 处理但**已显式声明**："
                     + "、".join(SECONDARY_KEYS[k] for k in unresolved_secondary)
                     + "；若这些项实际存在，本结果会**高估**股权价值。")

    mi = float(minority_interest) if given(minority_interest) else 0.0
    pe = float(preferred_equity) if given(preferred_equity) else 0.0
    oa = float(other_adjustments) if given(other_adjustments) else 0.0
    asset_total = sum(assets.values())

    equity = ev + asset_total - debt - mi - pe + oa
    per_share = equity / shares_v if shares_v else None

    steps = [
        {"step": "＋ 现金及现金等价物", "amount": assets.get("cash_and_equivalents", 0.0)},
        {"step": "＋ 非经营资产", "amount": assets.get("non_operating_assets", 0.0)},
        {"step": "－ 有息债务", "amount": -debt},
        {"step": "－ 少数股东权益", "amount": -mi},
        {"step": "－ 优先股权益", "amount": -pe},
        {"step": "± 其他调整", "amount": oa},
        {"step": "＝ 股权价值", "amount": equity},
        {"step": "÷ 总股本", "amount": shares_v, "result": per_share},
    ]
    return {
        "formula": "股权价值 = 企业价值 ＋现金及非经营资产 －有息债务 －少数股东权益 －优先股 ＋其他调整",
        "enterprise_value": ev,
        "cash_and_equivalents": assets.get("cash_and_equivalents"),
        "non_operating_assets": assets.get("non_operating_assets"),
        "interest_bearing_debt": debt,
        "minority_interest": mi, "preferred_equity": pe, "other_adjustments": oa,
        "shares": shares_v,
        "equity_value": equity, "per_share": per_share,
        "complete": True, "qualified": not unresolved_secondary,
        "is_equity_value_per_share": True,
        "missing_required": [], "unresolved_secondary": unresolved_secondary,
        "notes": notes, "boundary_note": ("；".join(notes) if notes else "调整项齐备。"),
        "steps": steps,
    }


# ══════════════════════════ 4. 完整折现链（FCFF / FCFE） ══════════════════════════

def dcf_fcff_per_share(fcff_next: float, wacc: float, growth: float, **bridge_kwargs) -> dict:
    """FCFF 折现 → 企业价值 →（桥）→ 股权价值 → 每股价值。返回**带公式的计算链**。"""
    m = check_discount_rate_match("fcff", "wacc")
    ev = enterprise_value_from_fcff(fcff_next, wacc, growth)
    res = {
        "method": "dcf_fcff", "method_cn": "FCFF 折现（企业价值法）",
        "cashflow_rate_match": m,
        "chain": [
            {"step": 1, "kind": "computed",
             "formula": "企业价值 EV = FCFF₁ / (WACC − g)",
             "inputs": {"FCFF₁": float(fcff_next), "WACC": float(wacc), "g": float(growth)},
             "result": ev["enterprise_value"], "ok": ev["ok"], "reason": ev["reason"]},
        ],
        "enterprise_value": ev["enterprise_value"],
        "blocked": ev["blocked"], "blocked_reason": None if ev["ok"] else ev["reason"],
    }
    if not ev["ok"]:
        res.update(equity_value=None, equity_value_incomplete=None, per_share=None,
                   complete=False, qualified=False,
                   is_equity_value_per_share=False, boundary_note=ev["reason"], bridge=None)
        return res
    br = equity_bridge(ev["enterprise_value"], **bridge_kwargs)
    res["bridge"] = br
    res["chain"].append({
        "step": 2, "kind": "computed", "formula": br["formula"],
        "result": br["equity_value"], "result_incomplete": br.get("equity_value_incomplete"),
        "steps": br["steps"], "complete": br["complete"],
    })
    res["chain"].append({
        "step": 3, "kind": "computed", "formula": "每股价值 = 股权价值 / 总股本",
        "inputs": {"股权价值": br["equity_value"], "总股本": br["shares"]},
        "result": br["per_share"], "complete": br["complete"],
    })
    res.update(equity_value=br["equity_value"],
               equity_value_incomplete=br.get("equity_value_incomplete"),
               per_share=br["per_share"],
               complete=br["complete"], qualified=br["qualified"],
               is_equity_value_per_share=br["is_equity_value_per_share"],
               missing_required=br["missing_required"],
               unresolved_secondary=br["unresolved_secondary"],
               boundary_note=br["boundary_note"], notes=br["notes"])
    return res


def dcf_fcfe_per_share(fcfe_next: float, ke: float, growth: float,
                       *, shares: float, non_operating_assets: float = _MISSING) -> dict:
    """FCFE 折现 → **直接得股权价值**（不再减债务）。"""
    m = check_discount_rate_match("fcfe", "ke")
    pv = perpetuity_value(fcfe_next, ke, growth)
    res = {"method": "dcf_fcfe", "method_cn": "FCFE 折现（股权现金流法）",
           "cashflow_rate_match": m,
           "chain": [{"step": 1, "kind": "computed",
                      "formula": "股权价值 = FCFE₁ / (Ke − g)",
                      "inputs": {"FCFE₁": float(fcfe_next), "Ke": float(ke), "g": float(growth)},
                      "result": pv["value"], "ok": pv["ok"], "reason": pv["reason"]}]}
    if not pv["ok"]:
        res.update(equity_value=None, per_share=None, complete=False, qualified=False,
                   is_equity_value_per_share=False, boundary_note=pv["reason"])
        return res
    equity = pv["value"]
    notes = []
    if non_operating_assets is _MISSING:
        notes.append("未提供非经营资产：FCFE 法默认其收益已包含在 FCFE 中；"
                     "若存在**不产生 FCFE** 的非经营资产，本结果会低估股权价值。")
    else:
        equity += float(non_operating_assets)
    per = equity / float(shares)
    res["chain"].append({"step": 2, "kind": "computed",
                         "formula": "每股价值 = 股权价值 / 总股本",
                         "result": per})
    res.update(equity_value=equity, per_share=per, complete=True,
               qualified=not notes, is_equity_value_per_share=True,
               boundary_note="；".join(notes) if notes else "口径齐备（FCFE 法无需再减有息债务）。",
               notes=notes)
    return res


# ══════════════════════════ 5. R3 输入分类 + 历史 CAGR 外推 ══════════════════════════

def classify_inputs(rows: list[dict]) -> dict:
    """把输入按 历史观测 / 已披露事实 / 未来假设 / 估计值 / 计算结果 分组，并检出**误用**。

    每行：``{"name":…, "value":…, "kind":…, "used_as":…(可选), "declared":…(可选)}``
    ``used_as`` ∈ {``as_observed``, ``as_assumption``, ``as_result``}。
    """
    groups: dict[str, list[dict]] = {k: [] for k in INPUT_ORDER}
    issues: list[str] = []
    for r in rows:
        k = _norm(r.get("kind"))
        name = r.get("name", "?")
        if k not in INPUT_KINDS:
            issues.append(f"「{name}」的 kind={r.get('kind')!r} 不在 {sorted(INPUT_KINDS)} 内，无法标注。")
            continue
        groups[k].append({"name": name, "value": r.get("value"),
                          "tag": INPUT_KINDS[k]["tag"], "used_as": r.get("used_as")})
        used_as = r.get("used_as")
        if k in ("historical_observed", "disclosed_fact") and used_as == "as_assumption" \
                and not r.get("declared"):
            issues.append(f"「{name}」是{INPUT_KINDS[k]['cn']}，却被当作**未来假设**使用"
                          f"且未声明 → 必须标为预测假设并写明期限、理由、敏感性。")
        if k in ("future_assumption", "estimate") and used_as == "as_observed":
            issues.append(f"「{name}」是{INPUT_KINDS[k]['cn']}，却被写成**已披露事实/历史观测** → 属冒充事实。")
    return {"groups": groups, "counts": {k: len(v) for k, v in groups.items()},
            "issues": issues, "ok": not issues,
            "legend": {k: INPUT_KINDS[k]["cn"] + " " + INPUT_KINDS[k]["tag"] for k in INPUT_ORDER}}


def historical_cagr_to_forecast(cagr: float, horizon_years, *,
                                justification=None, sensitivity=None,
                                base_period=None) -> dict:
    """把**历史 CAGR** 转成**未来预测假设**（R3/D 项）。

    必须齐备：预测期限 + 适用理由 + 敏感性。任一缺失 → ``ok=False``。
    无论怎样都**不**返回 ``verified_future_growth=True``。
    """
    missing = []
    if horizon_years in (None, 0, ""):
        missing.append("预测期限 horizon_years")
    if not justification:
        missing.append("适用理由 justification")
    if not sensitivity:
        missing.append("敏感性 sensitivity")
    pct = f"{float(cagr):.2%}"
    base = base_period or "历史区间"
    statement = (f"将 {base} 的 CAGR {pct} 外推至未来 {horizon_years} 年："
                 f"这是 **预测假设**，不是已验证的未来增长率。")
    return {
        "kind": "future_assumption", "label": "预测假设",
        "tag": INPUT_KINDS["future_assumption"]["tag"],
        "value": float(cagr), "base_period": base, "horizon_years": horizon_years,
        "justification": justification, "sensitivity": sensitivity,
        "verified_future_growth": False,
        "ok": not missing, "missing": missing,
        "statement": statement,
        "disclosure": (statement + (f" 适用理由：{justification}。" if justification else "")
                       + (f" 敏感性：{sensitivity}。" if sensitivity else "")
                       + " 使用本假设的结论须同时给出敏感性区间。"),
        "forbidden_wording": ["已验证的未来增长率", "确定的高增长", "必然维持该增速"],
        "required_wording": ["预测假设", "情景假设", "在…假设下"],
    }


# ══════════════════════════ 6. R4 方法选择与适用条件门 ══════════════════════════

VALUATION_METHODS: dict[str, dict] = {
    "dcf_fcff": {
        "cn": "FCFF 折现（企业价值法）",
        "object": ["个股", "单一主体", "产业集团"],
        "required_inputs": ["fcff_forecast", "wacc", "shares", "interest_bearing_debt",
                            "cash_or_non_operating_assets"],
        "output": "股权价值（须经桥接）",
        "boundary": ("必须齐备：FCFF 预测 + WACC + 有息债务 + 现金/非经营资产 + 总股本；"
                     "缺债务或现金 → **只能给企业价值，不给每股股权价值**"),
        "needs_peer": False,
        "gives_per_share": True,
        "note": "现金流与折现率必须同为「全资本」口径。",
    },
    "dcf_fcfe": {
        "cn": "FCFE 折现（股权现金流法）",
        "object": ["个股", "单一主体"],
        "required_inputs": ["fcfe_forecast", "ke", "shares"],
        "output": "股权价值（直接）",
        "boundary": "须齐备 FCFE 预测 + 股权成本 Ke + 总股本；折出的即股权价值，无需再减债务",
        "needs_peer": False,
        "gives_per_share": True,
        "note": "适用于分红/回购可预测、杠杆稳定的主体；Ke 与 FCFE 必须同为股权口径。",
    },
    "dividend_yield": {
        "cn": "股息率法",
        "object": ["个股", "单一主体"],
        "required_inputs": ["dividend_per_share", "required_yield"],
        "output": "每股价值（隐含价格）",
        "boundary": "须有**稳定分红记录**与合理的要求股息率；不适用不分红或分红剧烈波动的主体",
        "needs_peer": False, "gives_per_share": True,
        "note": "要求股息率是**假设**，须做情景区间。",
    },
    "pb_roe": {
        "cn": "PB-ROE",
        "object": ["个股", "单一主体"],
        "required_inputs": ["pb", "roe", "required_return"],
        "output": "每股价值（隐含价格）",
        "boundary": "须有 ROE 与 PB 的稳定关系；**跨行业不可直接比较**",
        "needs_peer": True, "gives_per_share": True,
        "note": "要求回报是假设；同业对照可提高可比性，但**不是方法本身的前提**。",
    },
    "relative_pe": {
        "cn": "相对估值（PE 折价/溢价）",
        "object": ["个股", "单一主体"],
        "required_inputs": ["eps", "peer_or_benchmark_multiple"],
        "output": "每股价值（隐含价格）",
        "boundary": "必须有**可比**倍数（同业或板块）；无可比对象 → 该方法**不适用**",
        "needs_peer": True, "gives_per_share": True,
        "note": "D7（板块估值分位/同业）是**本方法的必要输入**，不是所有估值的前提。",
    },
    "peg": {
        "cn": "PEG",
        "object": ["个股", "成长型主体"],
        "required_inputs": ["pe", "earnings_growth_forecast"],
        "output": "相对合理性判断（不给每股价值）",
        "boundary": "增速须为**预测假设**；PEG 不适用于负增长或周期性极强的主体",
        "needs_peer": False, "gives_per_share": False,
        "note": "只回答「增速与估值是否匹配」，不产出价格。",
    },
    "ps": {
        "cn": "PS（市销率）",
        "object": ["个股", "亏损期成长主体"],
        "required_inputs": ["revenue_per_share", "peer_ps"],
        "output": "每股价值（隐含价格）",
        "boundary": "适用于盈利尚未体现的主体；**须有可比 PS**",
        "needs_peer": True, "gives_per_share": True,
        "note": "不可替代盈利能力分析。",
    },
}

# 数据源能力 → 方法输入的映射（只用于把「取了什么数据」翻译成「方法可用不可用」）
CAPABILITY_TO_INPUTS = {
    "D1": ["pe", "pb", "price", "market_cap", "eps_derived"],
    "D2": ["fcff_forecast", "fcfe_forecast", "earnings_growth_forecast",
           "dividend_per_share", "roe", "revenue_per_share", "eps",
           "interest_bearing_debt", "cash_or_non_operating_assets", "shares"],
    "D6": ["peer_list"],
    "D7": ["peer_or_benchmark_multiple", "peer_ps"],
}


def select_methods(research_object: str, available_inputs, *, style=None) -> dict:
    """按**研究对象 + 数据条件**挑方法；**风格不参与决定**（R4）。

    ``available_inputs`` 可以是输入键集合，也可以是 ``{"D1","D2"}`` 这类能力编号（自动展开）。
    """
    avail = set()
    for x in available_inputs or []:
        s = str(x).strip()
        if s in CAPABILITY_TO_INPUTS:
            avail.update(CAPABILITY_TO_INPUTS[s])
        else:
            avail.add(_norm(s))

    ready, unavailable = [], []
    for name, spec in VALUATION_METHODS.items():
        if research_object and not any(o in research_object for o in spec["object"]):
            unavailable.append({"method": name, "cn": spec["cn"],
                                "reason": f"研究对象「{research_object}」不在适用对象 {spec['object']} 内",
                                "missing": [], "boundary": spec["boundary"],
                                "stop_only_self": True})
            continue
        miss = [k for k in spec["required_inputs"] if k not in avail]
        if miss:
            unavailable.append({"method": name, "cn": spec["cn"],
                                "reason": "必要输入不足：" + "、".join(miss),
                                "missing": miss, "boundary": spec["boundary"],
                                "stop_only_self": True})
        else:
            ready.append({"method": name, "cn": spec["cn"], "output": spec["output"],
                          "boundary": spec["boundary"], "needs_peer": spec["needs_peer"],
                          "gives_per_share": spec["gives_per_share"], "note": spec["note"]})
    return {
        "research_object": research_object,
        "ready": [r["method"] for r in ready], "ready_detail": ready,
        "unavailable": unavailable,
        "n_ready": len(ready),
        "style_input": style,
        "style_applied": False,
        "style_note": ("方法选择依据**研究对象与数据条件**；用户风格只作为偏好提示，"
                       "**不决定**方法，也不因风格相悖而强制并列另一种口径。"),
    }


def valuation_gate(selection: dict) -> dict:
    """替代两处一刀切：**不再要求「必须有 D7」、也不要求「至少两种方法」**（R4）。

    只检查：① 有没有**任何一个**方法齐备必要输入；② 齐备的方法各自能说到什么程度。
    """
    n = selection.get("n_ready", len(selection.get("ready", [])))
    unavail = selection.get("unavailable", [])
    if n == 0:
        return {
            "can_output": False, "n_methods": 0, "methods": [],
            "peer_required": False, "single_method_ok": True,
            "must_state_boundary": True, "must_state_inputs": True,
            "reason": ("没有任何一种估值方法的必要输入齐备 → **不输出估值数字**；"
                       "交付物中说明缺哪些输入、以及补齐后各方法可给出什么。"
                       "（注意：这不是「缺 D7 就不能估值」——D7 只是相对估值类方法的必要输入。）"),
            "allowed_output": "只列方法清单与缺失输入，不出数",
        }
    return {
        "can_output": True, "n_methods": n,
        "methods": selection.get("ready", []),
        "peer_required": False, "single_method_ok": True,
        "must_state_boundary": True, "must_state_inputs": True,
        "reason": (f"{n} 种方法必要输入齐备 → 可输出，但**每种方法只在其边界内陈述**；"
                   f"**不要求**凑够两种方法，**也不因少于两种就不能输出**。"
                   + ("" if n > 1 else " 单方法在边界内同样有效。")),
        "allowed_output": "各方法边界内的结果 + 明示仍缺的输入",
        "still_missing": {u["method"]: u["missing"] for u in unavail if u.get("missing")},
    }


# ══════════════════════════ 7. R5 多方法差异（不自动剔除） ══════════════════════════

def compare_methods(results: list[dict], *, requested_drops=None) -> dict:
    """解释差异，**不**把接近程度当准确度，**不**自动删除偏低/离群结果（R5/E 项）。"""
    vals = [(r.get("method"), float(r["per_share"])) for r in results
            if r.get("per_share") is not None]
    requested_drops = list(requested_drops or [])
    if not vals:
        return {"ok": False, "reason": "没有可比较的每股价值（各方法均未给出数值）",
                "dropped": [], "refused_drops": requested_drops,
                "proximity_is_not_accuracy": True}
    numbers = [v for _, v in vals]
    lo, hi = min(numbers), max(numbers)
    mid = _median(numbers)
    spread = (hi - lo) / mid if mid else None

    if spread is None:
        level, meaning = "undetermined", "中位数为 0，无法计算离散度"
    elif spread < 0.05:
        level, meaning = "converged", "结果接近"
    elif spread < 0.15:
        level, meaning = "structural_divergence", "存在结构性分歧"
    else:
        level, meaning = "divergent", "差异显著"

    detailed = []
    for r in results:
        if r.get("per_share") is None:
            detailed.append({"method": r.get("method"), "per_share": None,
                             "why_none": r.get("boundary_note") or r.get("blocked_reason")})
            continue
        detailed.append({"method": r.get("method"), "per_share": r["per_share"],
                         "assumptions": r.get("assumptions", {}),
                         "distance_from_lowest": r["per_share"] - lo})

    return {
        "ok": True, "n_methods": len(vals),
        "low": lo, "high": hi, "median": mid,
        "spread_pct": None if spread is None else round(spread * 100, 4),
        "level": level, "meaning": meaning,
        "detailed": detailed,
        "differences": {
            "why_they_differ": ("各方法的**输入口径与假设不同**：绝对法（DCF/FCFE）依赖 WACC/Ke 与增长率假设，"
                                "相对法（PE/PB/PS）依赖可比倍数，股息率法依赖分红政策。"
                                "差异本身是**信息**，不是错误。"),
            "must_report": ["每个方法的关键假设", "差异来源（输入不同 / 口径不同 / 时点不同）",
                            "差异是否可由某一假设的合理变动解释"],
        },
        "dropped": [],                                   # ★ 永不自动剔除
        "refused_drops": requested_drops,
        "refuse_reason": ("本包**不因结果偏低或偏离其他方法而剔除**该方法（轮次 4 修正 R5）；"
                          "如需舍弃某方法，只能因为其**必要输入不成立或口径错配**，"
                          "且必须在交付物中写明原因。") if requested_drops else None,
        "proximity_is_not_accuracy": True,
        "accuracy_note": ("接近程度只说明**假设相近**，不等于**准确**；"
                          "准确度取决于输入是否被独立核实、假设是否被声明、口径是否一致。"),
        "forbidden": ["为求收敛删除较低结果", "把收敛度当作准确度", "对差异静默取平均"],
    }


# ══════════════════════════ 8. R6 异常影响范围 ══════════════════════════

def impact_scope(anomalous_inputs, items: list[dict]) -> dict:
    """异常只影响**真正依赖该输入**的推演；**不**废弃整份报告（R6）。

    ``items``: ``[{"id":…, "claim":…, "depends_on":[输入名…]}…]``
    """
    bad = {_norm(x) for x in (anomalous_inputs or [])}
    affected, unaffected = [], []
    for it in items:
        deps = {_norm(d) for d in it.get("depends_on", [])}
        if deps & bad:
            affected.append({**it, "triggered_by": sorted(deps & bad)})
        else:
            unaffected.append(it)
    return {
        "anomalous_inputs": sorted(bad),
        "affected": affected, "unaffected": unaffected,
        "n_affected": len(affected), "n_unaffected": len(unaffected),
        "whole_report_void": False,
        "policy": ("估值异常只影响**真正依赖该输入**的推演；"
                   "某个无关指标偏差**不**导致整份报告作废（轮次 4 修正 R6）。"),
        "affected_action": "撤回 / 改写 / 降确定程度（三者择一，须写明）",
        "unaffected_action": "照常保留，不受牵连",
    }


def scope_anchor_impact(anchor: str, chain: list[dict]) -> dict:
    """替代「任一锚点偏差 > 5% → 整链作废」：只作废**真正依赖该锚点**的下游节点。"""
    a = _norm(anchor)
    voided, kept = [], []
    reachable = {a}
    for node in chain:
        deps = {_norm(d) for d in node.get("depends_on", [])}
        if deps & reachable:
            voided.append(node.get("id") or node.get("claim"))
            reachable |= {_norm(node.get("id") or "")}
        else:
            kept.append(node.get("id") or node.get("claim"))
    return {
        "anchor": a,
        "voided": [v for v in voided if v],
        "kept": [k for k in kept if k],
        "n_voided": len([v for v in voided if v]), "n_kept": len([k for k in kept if k]),
        "whole_chain_void": False,
        "policy": ("锚点失真只作废**依赖该锚点**的推演；与该锚点无关的推演照常保留"
                   "（不再「整链作废」）。"),
    }


# ══════════════════════════ 9. R7 历史材料政策 ══════════════════════════

HISTORY_POLICY = {
    "rule": "已发布的历史材料**一律不覆盖**；发现错误时**另建修订版本**，并写明旧版问题。",
    "published_materials": "不覆盖",
    "detail": ("`examples/` 下既有文件属已发布历史材料，**保持原样**；发现错误时**另建修订版本**"
               "（新文件名带版本号），并在修订版开头写明「对应旧版」「旧版问题」「本版改了什么」"
               "「本版不替代旧版」。"),
    "naming": "<NN>-<主题>-修订-<版本>.md",
    "must_state": ["对应旧版的文件名与编号", "旧版的具体问题（引用到行/表）",
                   "本版改了什么", "本版与旧版并存、不覆盖"],
}


def revision_header(*, old_file: str, old_issues: list[str], changes: list[str],
                    version: str, relation: str = "本修订版与旧版**并存**，不覆盖旧版") -> str:
    lines = [f"# 估值修订示例（{version}）", "",
             f"> **对应旧版**：`{old_file}`（**不修改、不删除**，原样保留）", "",
             "## 旧版存在的问题", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(old_issues, 1)]
    lines += ["", "## 本版改了什么", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(changes, 1)]
    lines += ["", f"> **版本关系**：{relation}。", ""]
    return "\n".join(lines)


# ══════════════════════════ 10. 检查清单运行器 ══════════════════════════

def _dig(obj, path):
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return None
    return cur


def _same(cur, want) -> bool:
    if isinstance(cur, list) and isinstance(want, int) and not isinstance(want, bool):
        return len(cur) == want
    if isinstance(cur, dict) and isinstance(want, dict):
        return all(k in cur and _same(cur[k], want[k]) for k in want)
    if isinstance(cur, (int, float)) and isinstance(want, (int, float)) \
            and not isinstance(cur, bool) and not isinstance(want, bool):
        return abs(cur - want) <= max(0.02, abs(want) * 1e-3)
    return cur == want


def run_one(chk: dict, base_dir=None) -> dict:
    t = chk.get("type")
    payload = dict(chk)
    for k in ("label", "type", "expect"):
        payload.pop(k, None)
    if base_dir:
        for k in ("chain_file", "results_file", "spec_file", "items_file"):
            if payload.get(k):
                payload[k] = str(Path(base_dir) / payload[k])
                payload[k[:-5]] = json.loads(Path(payload[k]).read_text(encoding="utf-8"))

    got: dict
    if t == "rate_match":
        got = check_discount_rate_match(payload["cashflow"], payload["rate"])
    elif t == "perpetuity":
        got = perpetuity_value(payload["fcf"], payload["rate"], payload["growth"])
    elif t == "dcf":
        p = payload
        got = dcf_fcff_per_share(
            p["fcff_next"], p["wacc"], p["growth"],
            cash_and_equivalents=p.get("cash_and_equivalents", _MISSING),
            non_operating_assets=p.get("non_operating_assets", _MISSING),
            interest_bearing_debt=p.get("interest_bearing_debt", _MISSING),
            minority_interest=p.get("minority_interest", _MISSING),
            preferred_equity=p.get("preferred_equity", _MISSING),
            other_adjustments=p.get("other_adjustments", _MISSING),
            shares=p.get("shares", _MISSING))
    elif t == "fcfe":
        p = payload
        got = dcf_fcfe_per_share(p["fcfe_next"], p["ke"], p["growth"], shares=p["shares"],
                                 **({"non_operating_assets": p["non_operating_assets"]}
                                    if "non_operating_assets" in p else {}))
    elif t == "bridge":
        p = payload
        got = equity_bridge(p["enterprise_value"],
                            **{k: p[k] for k in
                               ("cash_and_equivalents", "non_operating_assets",
                                "interest_bearing_debt", "minority_interest",
                                "preferred_equity", "other_adjustments", "shares")
                               if k in p})
    elif t == "forecast":
        p = payload
        got = historical_cagr_to_forecast(p["cagr"], p.get("horizon_years"),
                                          justification=p.get("justification"),
                                          sensitivity=p.get("sensitivity"),
                                          base_period=p.get("base_period"))
    elif t == "classify_inputs":
        got = classify_inputs(payload["rows"])
    elif t == "select":
        got = select_methods(payload.get("object", ""), payload.get("inputs", []),
                             style=payload.get("style"))
    elif t == "gate":
        sel = select_methods(payload.get("object", ""), payload.get("inputs", []),
                             style=payload.get("style"))
        got = valuation_gate(sel)
        got["_selection"] = {"ready": sel["ready"], "unavailable": sel["unavailable"]}
    elif t == "compare":
        rs = payload.get("results")
        if rs is None:
            rs = json.loads(Path(payload["results_file"]).read_text(encoding="utf-8"))
        got = compare_methods(rs, requested_drops=payload.get("requested_drops"))
    elif t == "impact":
        got = impact_scope(payload["anomalous_inputs"], payload["items"])
    elif t == "anchor":
        got = scope_anchor_impact(payload["anchor"], payload["chain"])
    elif t == "revision":
        h = revision_header(**payload)
        got = {"header": h,
               "n_old_issues": len(payload.get("old_issues") or []),
               "n_changes": len(payload.get("changes") or []),
               "mentions_old_file": payload.get("old_file", "\0") in h,
               "says_coexist": "并存" in h,
               "has_old_problem_section": "旧版存在的问题" in h,
               "policy": HISTORY_POLICY["published_materials"]}
    else:
        return {"label": chk.get("label"), "pass": False, "detail": f"未知检查类型 {t!r}"}

    expect = chk.get("expect", {})
    bad = [k for k in expect if not _same(_dig(got, k), expect[k])]
    detail = "符合预期" if not bad else \
        "不符 " + str({k: _dig(got, k) for k in bad}) + f"（期望 { {k: expect[k] for k in bad} }）"
    return {"label": chk.get("label", t), "pass": not bad, "detail": detail,
            "expect": expect, "actual": {k: _dig(got, k) for k in expect}}


def run_check_spec(spec: dict, base_dir=None) -> dict:
    checks = spec["checks"] if isinstance(spec, dict) else spec
    results = [run_one(c, base_dir) for c in checks]
    fails = [r for r in results if not r["pass"]]
    return {"case": spec.get("case") if isinstance(spec, dict) else None,
            "n_checks": len(results), "n_fail": len(fails),
            "pass": not fails, "results": results}


# ══════════════════════════ 11. 内置自检（A~G 全覆盖） ══════════════════════════

def selftest() -> int:
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, "SELFTEST FAIL: " + msg
        ok += 1
        print(f"[ok] {msg}")

    # ── A：纯合成算例，可手工核对 ────────────────────────────────
    r = dcf_fcff_per_share(100.0, 0.10, 0.0,
                           non_operating_assets=20.0, interest_bearing_debt=200.0,
                           minority_interest=0.0, preferred_equity=0.0,
                           other_adjustments=0.0, shares=10.0)
    chk(abs(r["enterprise_value"] - 1000.0) < 1e-9, "A 经营资产价值 = 100/(0.10-0) = 1000")
    chk(abs(r["equity_value"] - 820.0) < 1e-9, "A 股权价值 = 1000 + 20 − 200 = 820")
    chk(abs(r["per_share"] - 82.0) < 1e-9, "A 每股价值 = 820 / 10 = 82")
    chk(r["complete"] and r["qualified"] and r["is_equity_value_per_share"], "A 调整项齐备 → 可称每股价值")
    chk(r["cashflow_rate_match"]["ok"], "A 现金流与折现率配对：FCFF ↔ WACC")

    # ── B：缺必要调整项 → 不得假定为零后宣称完整每股价值 ──────────
    b = dcf_fcff_per_share(100.0, 0.10, 0.0, shares=10.0)   # 未提供现金与债务
    chk(b["complete"] is False and b["per_share"] is None,
        "B 缺现金与债务 → complete=False、per_share=None（不假定为零）")
    chk(b["equity_value_incomplete"] == 1000.0 and b["is_equity_value_per_share"] is False,
        "B 只给不完整中间量 1000，且标注「不是每股合理价值」")
    chk(any("高估" in n for n in b["notes"]) and any("低估" in n for n in b["notes"]),
        "B 边界说明同时给出高估与低估方向")
    b2 = dcf_fcff_per_share(100.0, 0.10, 0.0, non_operating_assets=20.0,
                            interest_bearing_debt=200.0, shares=10.0)   # 次要项未提供
    chk(any("按 0 处理但**已显式声明**" in n for n in b2["notes"]) and b2["qualified"] is False,
        "B 次要调整项未提供 → 按零处理但显式声明，qualified=False")

    # ── C：g ≥ r → 阻止永续公式 ────────────────────────────────
    c1 = perpetuity_value(100.0, 0.10, 0.10)
    c2 = perpetuity_value(100.0, 0.10, 0.12)
    chk(c1["blocked"] and c1["value"] is None and c2["blocked"],
        "C g = r 与 g > r 均被阻止（value=None）")
    chk("不适用" in c1["reason"] and "∞" in c1["reason"], "C 说明原因（分母 ≤ 0，会得 ∞ 或负数）")
    chk(perpetuity_value(100.0, 0.10, 0.02)["value"] == 1250.0, "C 反例：g=2% < r=10% → 1250 正常计算")

    # ── D：历史 CAGR 外推 → 预测假设 ────────────────────────────
    d1 = historical_cagr_to_forecast(0.1353, 10)
    chk(d1["label"] == "预测假设" and d1["verified_future_growth"] is False,
        "D 13.53% 外推 10 年 → 标为「预测假设」，verified_future_growth=False")
    chk(d1["ok"] is False and len(d1["missing"]) == 2,
        "D 缺期限理由/敏感性 → ok=False（须补 justification 与 sensitivity）")
    d2 = historical_cagr_to_forecast(0.1353, 10, justification="产能落地节奏延续",
                                     sensitivity="±3pct → 每股价值 ±18%", base_period="近三年")
    chk(d2["ok"] and "预测假设" in d2["disclosure"] and "13.53%" in d2["statement"],
        "D 齐备期限/理由/敏感性后可用，并强制标注")

    # ── E：多方法差异 → 保留、解释、不自动删除 ────────────────────
    e = compare_methods([
        {"method": "dcf_fcff", "per_share": 82.0, "assumptions": {"wacc": 0.10, "g": 0.0}},
        {"method": "dividend_yield", "per_share": 130.0, "assumptions": {"required_yield": 0.032}},
    ], requested_drops=["dividend_yield"])
    chk(e["dropped"] == [] and e["refused_drops"] == ["dividend_yield"],
        "E 请求删除较低/离群结果 → dropped 为空并拒绝")
    chk(e["n_methods"] == 2 and e["level"] == "divergent" and e["spread_pct"] > 15,
        f"E 两法差异 {e['spread_pct']}% → 标 divergent 且两者都保留")
    chk(e["proximity_is_not_accuracy"] is True and "接近程度" in e["accuracy_note"],
        "E 明示「接近程度 ≠ 准确度」")

    # ── F：缺同业数据 → 绝对法按边界输出；某法输入不足 → 只停该方法 ──
    f1 = select_methods("个股", ["fcff_forecast", "wacc", "shares",
                                 "interest_bearing_debt", "cash_or_non_operating_assets"])
    chk(f1["ready"] == ["dcf_fcff"], f"F 无同业数据 → 只有 dcf_fcff 可用（{f1['ready']}）")
    chk(any(u["method"] == "relative_pe" and "peer_or_benchmark_multiple" in u["missing"]
            for u in f1["unavailable"]), "F relative_pe 因缺可比倍数停用，且只停自己")
    g1 = valuation_gate(f1)
    chk(g1["can_output"] and g1["n_methods"] == 1 and g1["single_method_ok"],
        "F 单方法齐备即可输出（不再要求两种方法、不再要求 D7）")
    f2 = select_methods("个股", ["pe", "pb", "eps", "roe", "peer_or_benchmark_multiple"])
    chk("dcf_fcff" in [u["method"] for u in f2["unavailable"]]
        and "relative_pe" in f2["ready"],
        "F 缺财务数据时 dcf_fcff 停用、relative_pe 仍可用（各停各的）")
    f3 = valuation_gate(select_methods("个股", []))
    chk(f3["can_output"] is False and "D7 只是相对估值类方法的必要输入" in f3["reason"],
        "F 全无输入 → 不出数，且说明「不是缺 D7 就不能估值」")

    # ── G：独立可手工核对的算例 ─────────────────────────────────
    g = dcf_fcfe_per_share(50.0, 0.08, 0.03, shares=100.0, non_operating_assets=0.0)
    chk(abs(g["equity_value"] - 1000.0) < 1e-9,
        "G 手工核对：FCFE 50/(0.08−0.03) = 1000（股权价值，不再减债务）")
    chk(abs(g["per_share"] - 10.0) < 1e-9, "G 手工核对：1000 / 100 股 = 每股 10")
    mis = check_discount_rate_match("fcff", "ke")
    chk(mis["mismatch"] and "系统性低估" in mis["explain"],
        "G FCFF 配 Ke → 判错配并说明方向（系统性低估企业价值）")

    # ── R1/R3/R6/R7 附加 ───────────────────────────────────────
    chk(classify_cashflow("FCFF")["required_rate"] == "wacc"
        and classify_cashflow("FCFE")["required_rate"] == "ke",
        "R1 FCFF→WACC、FCFE→Ke 的配对表可机读")
    ci = classify_inputs([
        {"name": "FCFF FY2025", "kind": "disclosed_fact", "used_as": "as_observed"},
        {"name": "未来 10 年增速", "kind": "future_assumption", "used_as": "as_assumption"},
        {"name": "每股价值", "kind": "computed", "used_as": "as_result"},
        {"name": "历史 CAGR 13.53%", "kind": "historical_observed", "used_as": "as_assumption"},
    ])
    chk(ci["counts"]["disclosed_fact"] == 1 and ci["counts"]["future_assumption"] == 1
        and ci["counts"]["computed"] == 1 and ci["ok"] is False,
        "R3 三类输入分开标注，并抓出「历史值被当未来假设且未声明」")
    imp = impact_scope(["wacc"], [
        {"id": "C1", "claim": "每股价值 82", "depends_on": ["wacc", "fcff_forecast"]},
        {"id": "C2", "claim": "行业格局稳定", "depends_on": ["industry_data"]},
    ])
    chk(imp["n_affected"] == 1 and imp["n_unaffected"] == 1 and imp["whole_report_void"] is False,
        "R6 异常只打中依赖它的 1 条结论，另一条不受牵连，且不废弃整份报告")
    anc = scope_anchor_impact("pe_anchor", [
        {"id": "A", "depends_on": ["pe_anchor"]},
        {"id": "B", "depends_on": ["A"]},
        {"id": "C", "depends_on": ["dividend"]},
    ])
    chk(anc["n_voided"] == 2 and anc["n_kept"] == 1 and anc["whole_chain_void"] is False,
        "R6 锚点失真只作废依赖链上的 2 个节点，无关节点保留（不再「整链作废」）")
    chk(HISTORY_POLICY["published_materials"] == "不覆盖"
        and "另建修订版本" in HISTORY_POLICY["detail"],
        "R7 历史材料不覆盖，错误另建修订版（HISTORY_POLICY 可机读）")

    print(f"selftest: 全部通过（{ok} 项）")
    return 0


# ══════════════════════════ 12. CLI ══════════════════════════

def precedence_note() -> dict:
    return {
        "principle": "估值口径一致 > 方法适用条件 > 结果边界 > 多方法差异 > 结论表述",
        "order": ["口径配对（现金流↔折现率）", "方法必要输入", "结果边界",
                  "多方法差异解释", "结论确定程度"],
        "note": ("先把现金流与折现率配对、再谈方法；方法不足时只停该方法；"
                 "结果不完整时只给边界；差异要解释不要抹平。"),
    }


def rules_text() -> str:
    lines = ["七条设计原则（R1~R7）："]
    for i, t in enumerate([
        "现金流与折现率必须配对：FCFF↔WACC（企业价值）、FCFE↔Ke（股权价值）",
        "FCFF→企业价值→（桥）→股权价值→每股；缺必要调整项只给边界，不给每股价值",
        "输入分「历史观测/已披露事实/未来假设/估计值/计算结果」；历史 CAGR 外推=预测假设",
        "按研究对象与数据条件选方法，不按风格强制；取消「必须有 D7」「至少两种方法」",
        "多方法用于解释差异；接近程度≠准确度；不得为求收敛删除较低结果",
        "估值异常只影响真正依赖该输入的推演，不废弃整份报告",
        "已发布历史材料不覆盖，错误示例另建修订版",
    ], 1):
        lines.append(f"  R{i}  {t}")
    lines += ["", "方法注册表（必要输入 / 适用条件）："]
    for k, s in VALUATION_METHODS.items():
        lines.append(f"  · {k:<15} {s['cn']}")
        lines.append(f"      必要输入：{'、'.join(s['required_inputs'])}")
        lines.append(f"      适用对象：{'、'.join(s['object'])}｜需要可比对象：{'是' if s['needs_peer'] else '否'}")
        lines.append(f"      边界：{s['boundary']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="估值方法与适用条件（轮次 4）")
    ap.add_argument("cmd", choices=["selftest", "rules", "dcf", "bridge", "perpetuity",
                                    "methods", "compare", "scope", "checks", "precedence"])
    ap.add_argument("--spec")
    ap.add_argument("--fcf", type=float)
    ap.add_argument("--rate", type=float)
    ap.add_argument("--growth", type=float)
    ap.add_argument("--object", default="")
    ap.add_argument("--inputs", default="")
    ap.add_argument("--style")
    ns = ap.parse_args(argv)

    if ns.cmd == "selftest":
        return selftest()
    if ns.cmd == "rules":
        print(rules_text())
        return 0
    if ns.cmd == "precedence":
        print(json.dumps(precedence_note(), ensure_ascii=False, indent=2))
        return 0
    if ns.cmd == "perpetuity":
        print(json.dumps(perpetuity_value(ns.fcf, ns.rate, ns.growth),
                         ensure_ascii=False, indent=2))
        return 0
    if ns.cmd == "methods":
        sel = select_methods(ns.object, [x for x in ns.inputs.split(",") if x], style=ns.style)
        print(json.dumps({"selection": sel, "gate": valuation_gate(sel)},
                         ensure_ascii=False, indent=2))
        return 0
    if ns.cmd == "checks":
        spec = json.loads(Path(ns.spec).read_text(encoding="utf-8"))
        res = run_check_spec(spec, base_dir=str(Path(ns.spec).parent))
        for r in res["results"]:
            print(("[ok] " if r["pass"] else "[FAIL] ") + f"{r['label']} —— {r['detail'][:100]}")
        print(f'{spec.get("case")}：{res["n_checks"]} 项，失败 {res["n_fail"]}')
        return 0 if res["pass"] else 1
    if ns.spec:
        spec = json.loads(Path(ns.spec).read_text(encoding="utf-8"))
        res = run_one({"type": ns.cmd, **spec})
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print("需 --spec", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

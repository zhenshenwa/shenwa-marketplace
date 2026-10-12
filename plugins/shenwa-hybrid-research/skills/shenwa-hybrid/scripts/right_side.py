#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""get_right_side 原始返回 → 单日截面 → 四类分桶 + 面板质量检查。

这是本包对该接口**唯一的取数与分桶口径实现**。文档（`references/04`、`skills/shenwa/SKILL.md`）
描述的口径以此文件为准；两者不一致时以本文件为准并同步修订文档。

为什么必须固化在代码里
----------------------
`get_right_side` 在 `dayOffset=N` 时返回的是 **62×N 条「面板数据」**（近 N 个交易日的纵向序列），
不是单日快照。直接对全量数组分桶会把 N 天的迁移**重复累加**。
真实样例实测（`cases/01-先切截面再分桶/`，dayOffset=2）：全量分桶 `Breakdown=5`，
切成最新交易日截面后是 `Breakdown=3` —— 口径写错就报错数字，且看起来毫无异常。

数据分层（本文件严格遵守）
--------------------------
- **观测数据**：`date` / `code` / `name` / `q5.price_performance` 等接口原始字段；
- **计算指标**：分桶计数、覆盖数量、质量标记（本文件与 `data_qc.py` 产出）；
- **模型判定**：`state_mapping` / `state_trans` / `veto_check` —— **深挖给出的状态与算法，
  本文件只做读取与一致性检查，绝不自行改判、不另造阶段标准。**

用法
----
    python3 right_side.py <原始返回.json> [--date YYYY-MM-DD] [--json]

- 文件首行若是 `// base: ...` 注释（`shenwa_api.py` 会打印），自动剔除后再解析。
- `--date` 缺省取数据中最大的 `date`（即最新交易日）；也可显式指定做历史对照。
- `--json` 输出机器可读结果，供 `maintenance/expert-package-review/verify.py` 断言。

口径
----
- 升级 = `state_trans.transition_type == "Upgrade"`
- 破坏 = `state_trans.transition_type == "Breakdown"`
- 维持 = `"Hold"`；未知 = `"Unknown"`
- ⚠️ 真实数据里还出现过 `Upgrade_Jump` / `Downgrade` / `Recovery`（2026-09-28 截面实测）。
  官方未给这三者口径，**本模块不猜语义**，一律进 `unclassified` 并显式报出，
  以免被静默并入某一桶。遇到时应回报而非自行归类。
- 字段路径：`state.state_mapping.current_state`（**不是**内层顶层的 `state_mapping`）、
  `state.veto_check`、`state.qualification_summary`。
- ⚠️ **否决字段缺失 ≠ 无否决**：`has_veto` 为空/缺失/解析失败时单列 `veto_missing`，
  绝不并入 `has_veto == False` 那一类（旧实现用真值判断会静默把 None 当 False）。
- ⚠️ **覆盖范围按实际返回统计，不写死 62**：`coverage_by_date` / `expected_from_data`
  由数据本身得出；与已知范围（62）的比对须显式传 `expected_coverage`。
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_data_qc():
    """与 `data_qc.py` 共用同一实现 —— 不在此处另写一套质量检查。"""
    spec = importlib.util.spec_from_file_location("data_qc", _HERE / "data_qc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


data_qc = _load_data_qc()

UPGRADE = {"Upgrade"}
BREAKDOWN = {"Breakdown"}
HOLD = {"Hold"}
UNKNOWN = {"Unknown"}
KNOWN = UPGRADE | BREAKDOWN | HOLD | UNKNOWN


def strip_base_comment(text: str) -> str:
    """剔除 `shenwa_api.py` 打印的 `// base: ...` 首行注释。"""
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("//"):
        lines = lines[1:]
    return "\n".join(lines)


def load(source):
    """source 可以是路径，也可以是原始文本。返回解析后的 payload。"""
    text = Path(source).read_text(encoding="utf-8") if isinstance(source, (str, Path)) and Path(str(source)).exists() else str(source)
    return json.loads(strip_base_comment(text))


def _inner(row: dict) -> dict:
    """每行的 `data` 是再一层 JSON 字符串，须二次 loads。"""
    v = row.get("data")
    return json.loads(v) if isinstance(v, str) else (v or {})


def normalise(payload: dict) -> list[dict]:
    """把 payload 摊平成统一结构，字段路径按实测口径取。"""
    out = []
    for it in payload.get("data") or []:
        inn = _inner(it)
        st = inn.get("state") or {}
        tr = inn.get("state_trans") or {}
        sm = st.get("state_mapping") or {}
        qs = st.get("qualification_summary") or {}
        vc = st.get("veto_check") or {}
        flags = list(vc.get("triggered_flags") or [])
        if not flags and tr.get("veto_reasons"):
            flags = [x.strip() for x in str(tr["veto_reasons"]).split(",") if x.strip()]
        # ★ 三态保留：True / False / None（缺失）。不得把 None 折叠成 False。
        hv = tr.get("has_veto")
        if hv is None:
            hv = vc.get("has_veto")
        out.append({
            "date": it.get("date"),
            "code": it.get("code"),
            "name": it.get("name"),
            "tt": tr.get("transition_type"),
            "prev_state": tr.get("prev_state"),
            "curr_state": tr.get("curr_state") or sm.get("current_state"),
            "q_overall": tr.get("q_overall") or qs.get("q_overall"),
            "q_pass_days": tr.get("q_pass_days"),
            "has_veto": hv,
            "has_veto_missing": hv is None,
            "flags": flags,
        })
    return out


def cut_cross_section(rows: list[dict], date: str | None = None):
    """★ 先切截面：按 date 分组，取目标交易日（缺省=最大日期）的那一批。"""
    if not rows:
        return None, []
    g = collections.OrderedDict()
    for r in rows:
        g.setdefault(r["date"], []).append(r)
    target = date or max(g)
    return target, g.get(target, [])


def bucket(rows: list[dict]) -> dict:
    """按 transition_type 分桶；未知取值不静默丢弃，集中进 unclassified。"""
    up = [r for r in rows if r["tt"] in UPGRADE]
    bd = [r for r in rows if r["tt"] in BREAKDOWN]
    hold = [r for r in rows if r["tt"] in HOLD]
    unk = [r for r in rows if r["tt"] in UNKNOWN]
    unclassified = [r for r in rows if r["tt"] not in KNOWN]
    near = [r for r in rows if r["q_overall"] == "near" or (r["q_pass_days"] or 0) > 0]
    veto = [r for r in rows if r["has_veto"] is True]              # ★ 只认真值 True
    veto_false = [r for r in rows if r["has_veto"] is False]
    veto_missing = [r for r in rows if r["has_veto_missing"]]      # ★ 缺失单列
    stages = collections.Counter(r["curr_state"] for r in rows)
    return {
        "coverage": len(rows),
        "upgrade": up,
        "breakdown": bd,
        "hold": hold,
        "unknown": unk,
        "unclassified": unclassified,
        "near": near,
        "veto": veto,
        "veto_false": veto_false,
        "veto_missing": veto_missing,
        "stage_distribution": dict(sorted(stages.items(), key=lambda kv: str(kv[0]))),
        "counts": {"upgrade": len(up), "breakdown": len(bd), "hold": len(hold),
                   "unknown": len(unk), "unclassified": len(unclassified),
                   "near": len(near), "veto": len(veto),
                   "veto_false": len(veto_false), "veto_missing": len(veto_missing)},
    }


def analyse(payload: dict, date: str | None = None,
            expected_coverage: int | None = None) -> dict:
    rows = normalise(payload)
    target, section = cut_cross_section(rows, date)
    res = bucket(section)
    res["date"] = target
    res["panel_total"] = len(rows)                     # 62 × N
    res["panel_dates"] = sorted({r["date"] for r in rows})
    res["raw_bucket_without_cut"] = dict(collections.Counter(
        r["tt"] for r in rows))                        # 对照组：不切截面会得到什么
    # ★ 面板质量检查（生产流程必经）：日期/代码唯一性、覆盖、重复、未知状态、缺失否决
    # 两级：`quality` 看整块面板（覆盖按日、重复、缺键）；`section_quality` 看**本次要报出的截面**
    # （未知状态、缺失否决字段 —— 只对实际进入统计的那一批负责）。
    res["quality"] = data_qc.check_panel_quality(rows, expected_coverage=expected_coverage)
    res["section_quality"] = data_qc.check_panel_quality(section)
    res["payload_status"] = data_qc.classify_payload(payload)
    return res


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="get_right_side 原始返回 → 单日截面 → 分桶 + 质量检查")
    ap.add_argument("source", help="原始返回文件路径（或直接给 JSON 文本）")
    ap.add_argument("--date", default=None, help="目标交易日，缺省=最新")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--expected-coverage", type=int, default=None,
                    help="已知覆盖范围（如 62）；不传则只按实际返回统计，不写死")
    a = ap.parse_args(argv)

    payload = load(a.source)
    status = data_qc.classify_payload(payload)
    if not status["usable"]:
        print(f"⚠️ 返回不可用：{status['kind']} —— {status['note']}")
        if a.json:
            print(json.dumps({"payload_status": status}, ensure_ascii=False, indent=2))
        return 1

    res = analyse(payload, a.date, a.expected_coverage)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    c = res["counts"]
    q = res["quality"]
    print(f"面板总条数 {res['panel_total']}，覆盖交易日 {res['panel_dates']}")
    print(f"截面 {res['date']}：覆盖 {res['coverage']}")
    print(f"  升级 {c['upgrade']} / 破坏 {c['breakdown']} / 维持 {c['hold']} / 未知 {c['unknown']}")
    print(f"  临界 {c['near']} / 否决 {c['veto']} / "
          f"否决=False {c['veto_false']} / ⚠️否决字段缺失 {c['veto_missing']}")
    if c["veto_missing"]:
        print(f"    ⚠️ 有 {c['veto_missing']} 条记录的否决字段缺失 —— "
              f"**不得当作「无否决」**，须回报（示例 {res['veto_missing'][:3]}）")
    if c["unclassified"]:
        vals = collections.Counter(r["tt"] for r in res["unclassified"])
        print(f"  ⚠️ 未归类 {c['unclassified']}：{dict(vals)}"
              f" —— 官方未给这些取值口径，请回报，勿自行归类")
    print(f"  阶段分布 {res['stage_distribution']}")
    print(f"（对照）不切截面直接分桶会得到：{res['raw_bucket_without_cut']}")
    if res["breakdown"] == 0:
        print("  ⚠️ 破坏=0 ≠ 结构健康：须同时报出 S5 存量占比与否决率")
    print(f"  面板质量：覆盖 {q['coverage_by_date']}（按实际返回统计，未写死）；"
          f"标记 {'无' if not q['flags'] else '、'.join(q['flags'])}")
    sq = res["section_quality"]
    print(f"  截面质量：标记 {'无' if not sq['flags'] else '、'.join(sq['flags'])}"
          f"；未知状态 {len(sq['unknown_state'])}；未归类迁移 {sq['unclassified_tt'] or '无'}"
          f"；缺失否决字段 {len(sq['veto_missing'])}")
    if q["partial_dates"]:
        print(f"    ⚠️ 覆盖不全的交易日：{q['partial_dates']}"
              f"（最新日 {q['coverage_current']} / 数据内最大 {q['expected_from_data']}）")
    if q["duplicate_keys"]:
        print(f"    ⚠️ 重复记录 (date, code)：{q['duplicate_keys'][:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

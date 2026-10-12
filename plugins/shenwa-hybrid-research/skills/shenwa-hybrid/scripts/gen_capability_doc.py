# -*- coding: utf-8 -*-
"""gen_capability_doc.py · 由 `capability.py` 生成人读版《09-能力清单.md》。

单一事实源：`skills/shenwa-hybrid/scripts/capability.py`（同一目录）。
本生成器保证**文档与实现不漂移**——`maintenance/expert-package-review/verify.py`
会调用 `render()` 重新生成一遍，与磁盘上的 `09-能力清单.md` 逐字比对。

不联网、不修改任何其他文件。

CLI:
  python gen_capability_doc.py           # 生成/刷新 09-能力清单.md
  python gen_capability_doc.py --check   # 只比对，不写盘；不一致时退出码 2
"""
from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))                 # …/scripts
SKILL_DIR = os.path.dirname(HERE)                                 # …/skills/shenwa-hybrid
OUT = os.path.join(SKILL_DIR, "references", "09-能力清单.md")


def load_capability():
    """按同目录加载 capability.py（不依赖 sys.path，避免与其它副本串味）。"""
    p = os.path.join(HERE, "capability.py")
    spec = importlib.util.spec_from_file_location("capability_for_doc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FIELD_CN = {
    "purpose": "用途", "inputs": "输入", "outputs": "输出",
    "object_level": "对象层级", "coverage": "覆盖范围", "depends_on": "必要依赖",
    "permission": "权限", "status": "已验证程度", "on_missing": "缺失时处理方式",
}


def _esc(s):
    """表格单元格里不能有裸换行、不能有未转义的竖线。"""
    return str(s).replace("\n", " ").replace("|", "\\|")


def table(caps, cap):
    out = ["| 编号 | 能力 | 状态 | 对象层级 | 权限 |", "|---|---|---|---|---|"]
    for c in caps:
        out.append(f"| {c['id']} | {c['name']} | `{c['status']}` | "
                   f"{_esc(c['object_level'])} | {_esc(c['permission'])} |")
    out.append("")
    out.append("### 九项属性明细")
    out.append("")
    for c in caps:
        out.append(f"#### {c['id']} · {c['name']}")
        out.append("")
        out.append("| 属性 | 内容 |")
        out.append("|---|---|")
        for f in cap.REQUIRED_FIELDS:
            out.append(f"| **{FIELD_CN[f]}** | {_esc(c[f])} |")
        out.append("")
    return "\n".join(out)


def render(cap) -> str:
    """生成《09-能力清单.md》全文（不含写盘副作用，便于校验器复算）。"""
    L = []
    L.append("# 09 · 能力清单（用途 / 输入 / 输出 / 层级 / 覆盖 / 依赖 / 权限 / 验证程度 / 缺失处理）")
    L.append("")
    L.append("> **本文件由 `skills/shenwa-hybrid/scripts/capability.py` 生成，请勿手改**；")
    L.append("> 要改能力边界，改 `capability.py` 后重跑 `scripts/gen_capability_doc.py`。")
    L.append("> 生成器与实现的**一致性由 `maintenance/expert-package-review/verify.py` 断言**。")
    L.append("")
    L.append("## 〇、怎么用这张清单")
    L.append("")
    L.append("1. **先看状态，再看能力名。** 状态四态：")
    for k in cap.STATUS_ORDER:
        L.append(f"   - `{k}` —— {cap.STATUS[k]}")
    L.append("2. **未验证 ≠ 不存在**，但**不能直接承诺支持**。")
    L.append("3. **权限是硬门槛**：标 `VIP2` / `ADM` 的能力，无权限时返回 `status=AUT`。")
    L.append("4. **缺失时的处理方式写在最后一列**，照它执行，不要临场发明。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一、编号口径（v1.7.0 统一）")
    L.append("")
    L.append(f"**冲突**：{cap.NUMBERING['conflict']}")
    L.append("")
    L.append(f"**根因**：{cap.NUMBERING['root_cause']}")
    L.append("")
    L.append("**解法**：")
    for x in cap.NUMBERING["resolution"]:
        L.append(f"- {x}")
    L.append("")
    L.append(f"**不改写的部分**：{cap.NUMBERING['unchanged']}")
    L.append("")
    L.append("### 编号总表")
    L.append("")
    for group, mapping in cap.NUMBERING["canonical"].items():
        L.append(f"**{group}**")
        L.append("")
        for k, v in mapping.items():
            L.append(f"- `{k}` — {v}")
        L.append("")
    L.append("---")
    L.append("")
    L.append("## 二、深挖侧能力（S）")
    L.append("")
    L.append(table(cap.SHENWA_CAPS, cap))
    L.append("---")
    L.append("")
    L.append("## 三、接入 / 运维（U，非研究能力）")
    L.append("")
    L.append(table(cap.UTILITY_CAPS, cap))
    L.append("---")
    L.append("")
    L.append("## 四、数据源侧能力（D1~D9）")
    L.append("")
    L.append(table(cap.DATA_CAPS, cap))
    L.append("---")
    L.append("")
    L.append("## 五、本地保存 vs 服务端「公开 / 私有」")
    L.append("")
    sb = cap.storage_boundary()
    L.append(f"**问题**：{sb['question']}")
    L.append("")
    L.append(f"**结论**：{sb['answer']}")
    L.append("")
    L.append("| 维度 | 内容 | 可控性 | 依据 | 状态 |")
    L.append("|---|---|---|---|---|")
    for key in ("local_save", "server_state"):
        d = sb[key]
        L.append(f"| `{key}` | {_esc(d['what'])} | {_esc(d['control'])} | "
                 f"{_esc(d['evidence'])} | `{d['status']}` |")
    L.append("")
    L.append("**不得承诺**：")
    for x in sb["cannot_promise"]:
        L.append(f"- {x}")
    L.append("")
    L.append("**可执行替代**：")
    for k, v in sb["executable_alternative"].items():
        L.append(f"- **{k}**：{v}")
    L.append("")
    L.append(f"**与偏好项的关系**：{sb['preference_mapping']}")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 六、量化 / 回测 / 跨市场：按实际链路标注")
    L.append("")
    for name, d in cap.special_chains().items():
        L.append(f"### {name}")
        L.append("")
        L.append(f"- **主张**：{d['claim']}")
        L.append(f"- **状态**：`{d['status']}`")
        if d["chain"]:
            L.append(f"- **实际链路**：{' → '.join(d['chain'])}")
        L.append(f"- **已支持**：{d['supported'] or '—'}")
        L.append(f"- **未支持**：{' / '.join(d['not_supported'])}")
        L.append(f"- **诚实规则**：{d['honesty_rule']}")
        L.append("")
    L.append("---")
    L.append("")
    L.append("## 七、命令")
    L.append("")
    L.append("```bash")
    L.append("python3 skills/shenwa-hybrid/scripts/capability.py selftest")
    L.append("python3 skills/shenwa-hybrid/scripts/capability.py list [--status conditional]")
    L.append("python3 skills/shenwa-hybrid/scripts/capability.py show D4")
    L.append("python3 skills/shenwa-hybrid/scripts/capability.py boundary       # 本地 vs 服务端")
    L.append("python3 skills/shenwa-hybrid/scripts/capability.py verify-offline # 量化/回测/跨市场")
    L.append("python3 skills/shenwa-hybrid/scripts/capability.py numbering      # 编号冲突与解法")
    L.append("python3 skills/shenwa-hybrid/scripts/gen_capability_doc.py --check # 文档与实现一致性")
    L.append("```")
    L.append("")
    return "\n".join(L).rstrip() + "\n"


def main(argv):
    check = "--check" in argv
    cap = load_capability()
    body = render(cap)
    old = None
    if os.path.exists(OUT):
        with open(OUT, "rb") as f:
            old = f.read().decode("utf-8")
    if check:
        same = (old == body)
        print(("一致" if same else "**不一致**") + f"：{OUT}（{len(body.encode('utf-8'))} B）")
        return 0 if same else 2
    with open(OUT, "wb") as f:
        f.write(body.encode("utf-8"))
    print(f"写入 {OUT}（{len(body.encode('utf-8'))} B）"
          + ("  与旧文件相同" if old == body else "  内容已更新"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

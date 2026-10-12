#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shenwa-marketplace 维护脚本

用途：把本机已注册的专家同步进本市场仓库，自动升版本、同步市场清单版本号，
      并在最后做全仓凭据扫描（有命中直接失败，防止误发）。

用法：
    python scripts/sync.py                 # 同步全部专家 + 递增 patch 版本
    python scripts/sync.py --minor         # 递增 minor（1.0.5 -> 1.1.0）
    python scripts/sync.py --major         # 递增 major（1.0.5 -> 2.0.0）
    python scripts/sync.py --no-bump       # 只同步，不动版本号
    python scripts/sync.py --check         # 只扫描凭据，不写任何文件
    python scripts/sync.py shenwa-agent    # 只处理指定专家
    python scripts/sync.py --force-clean   # 允许多余文件被批量清理（默认超阈值时只告警）

退出码：0 成功；1 有凭据泄漏或出错。
"""

import argparse
import base64
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
MARKETPLACE_JSON = REPO / ".codebuddy-plugin" / "marketplace.json"
SRC_ROOT = Path.home() / ".workbuddy" / "plugins" / "marketplaces" / "my-experts" / "plugins"

# 分发时必须排除的本地文件/目录
EXCLUDE_NAMES = {".created-by-session", ".shenwa_token", "__pycache__"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}

# 已知凭据黑名单（**编码存储**，避免本脚本自身成为泄漏源）
# 解码后为历史账号的手机号与邀请码
KNOWN_SECRETS = [
    base64.b64decode("MTM5ODAwNjcxNzM=").decode(),
    base64.b64decode("NTExOTE4").decode(),
]

# 通用凭据特征（防止新的账号被写死）
GENERIC_PATTERNS = [
    (r'[\'"]?(?:password|passwd|pwd)[\'"]?\s*[:=]\s*[\'"][^\'"\s]{4,}[\'"]', "疑似明文密码"),
    (r'[\'"]?(?:userID|user_id|username|phone|mobile)[\'"]?\s*[:=]\s*[\'"]1[3-9]\d{9}[\'"]', "疑似硬编码手机号"),
    (r'[\'"]?inviteCode[\'"]?\s*[:=]\s*[\'"]\d{4,}[\'"]', "疑似硬编码邀请码"),
]

# 同一个 run 内允许多余文件被自动清理的上限（超过则仅告警，避免批量删除）
STALE_LIMIT = 40


def iter_files(root: Path):
    if not root.exists():
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in EXCLUDE_NAMES or any(part in EXCLUDE_NAMES for part in p.parts):
            continue
        if p.suffix in EXCLUDE_SUFFIX:
            continue
        yield p


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_secrets(root: Path):
    """返回 [(相对路径, 命中片段, 说明)]"""
    hits = []
    for f in iter_files(root):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in KNOWN_SECRETS:
            if pat in text:
                hits.append((str(f.relative_to(REPO)), pat, "已知硬编码凭据"))
        for rx, label in GENERIC_PATTERNS:
            m = re.search(rx, text)
            if m:
                hits.append((str(f.relative_to(REPO)), m.group(0)[:60], label))
    return hits


def sync_tree(src: Path, dst: Path, force_clean: bool = False):
    """增量同步：逐个覆盖，只清多余文件，不做整目录删除"""
    src_files = {p.relative_to(src): p for p in iter_files(src)}
    dst.mkdir(parents=True, exist_ok=True)

    copied = same = 0
    for rel, sp in src_files.items():
        tp = dst / rel
        tp.parent.mkdir(parents=True, exist_ok=True)
        if tp.exists() and tp.stat().st_size == sp.stat().st_size and sha256(sp) == sha256(tp):
            same += 1
            continue
        shutil.copy2(sp, tp)
        copied += 1

    stale = [p.relative_to(dst) for p in iter_files(dst) if p.relative_to(dst) not in src_files]
    removed = 0
    if stale:
        if len(stale) > STALE_LIMIT and not force_clean:
            print(f"  ⚠ 目标目录有 {len(stale)} 个多余文件，超过安全上限 {STALE_LIMIT}，本次不删除")
            print("     确认无误后重跑并加 --force-clean")
            for s in stale[:10]:
                print("       -", s)
        else:
            for rel in stale:
                (dst / rel).unlink()
                removed += 1

    # 清理遗留空目录（best-effort，不删 dst 本身）
    for d in sorted((p for p in dst.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            if d != dst and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass

    return copied, same, removed, len(stale)


def bump_version(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def read_plugin_version(plugin_dir: Path) -> str:
    pj = json.loads((plugin_dir / ".codebuddy-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return pj["version"]


def write_plugin_version(plugin_dir: Path, new_ver: str):
    """只改版本号那一行，保持文件其余部分不变（diff 最小）"""
    p = plugin_dir / ".codebuddy-plugin" / "plugin.json"
    text = p.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'("version"\s*:\s*")\d+\.\d+\.\d+(")',
        lambda m: m.group(1) + new_ver + m.group(2),
        text,
        count=1,
    )
    if n == 0:
        raise RuntimeError(f"未能在 {p} 中找到 version 字段")
    p.write_text(new_text, encoding="utf-8")


def sync_marketplace_versions():
    """让 marketplace.json 各条目的 version 与插件实际版本一致"""
    data = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    changed = []
    for entry in data.get("plugins", []):
        name = entry.get("name")
        plugin_dir = PLUGINS_DIR / name
        if not plugin_dir.exists():
            continue
        real = read_plugin_version(plugin_dir)
        if entry.get("version") != real:
            changed.append((name, entry.get("version"), real))
            entry["version"] = real
    if changed:
        MARKETPLACE_JSON.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return changed


def main():
    ap = argparse.ArgumentParser(description="同步专家到 marketplace 仓库并升版本")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--major", action="store_true", help="递增 major")
    g.add_argument("--minor", action="store_true", help="递增 minor")
    g.add_argument("--no-bump", action="store_true", help="不递增版本")
    g.add_argument("--check", action="store_true", help="只扫描凭据，不写任何文件")
    ap.add_argument("--force-clean", action="store_true", help="允许多余文件批量清理")
    ap.add_argument("names", nargs="*", help="只处理指定专家名（默认全部）")
    args = ap.parse_args()

    if not PLUGINS_DIR.exists():
        print(f"ERROR: 未找到 {PLUGINS_DIR}")
        return 1

    if args.check:
        print(f"扫描目录：{REPO}")
        hits = scan_secrets(REPO)
        if hits:
            print(f"\n✗ 发现 {len(hits)} 处疑似凭据：")
            for f, pat, label in hits:
                print(f"  - {f}  [{label}]  {pat}")
            return 1
        print("\n✓ 凭据扫描通过（0 命中）")
        return 0

    targets = args.names or sorted(p.name for p in PLUGINS_DIR.iterdir() if p.is_dir())
    part = "major" if args.major else "minor" if args.minor else "patch"

    print("=" * 62)
    print("同步专家到 marketplace 仓库")
    print("=" * 62)

    for name in targets:
        src = SRC_ROOT / name
        dst = PLUGINS_DIR / name
        if not src.exists():
            print(f"\n[{name}] 跳过：源目录不存在 {src}")
            continue

        print(f"\n[{name}]")
        old_ver = read_plugin_version(src)
        copied, same, removed, stale_total = sync_tree(src, dst, args.force_clean)
        print(f"  文件：新增/更新 {copied}，未变 {same}，清理多余 {removed}"
              + (f"（另有 {stale_total - removed} 个待确认）" if stale_total > removed else ""))

        if args.no_bump:
            print(f"  版本保持 {old_ver}")
        else:
            new_ver = bump_version(old_ver, part)
            write_plugin_version(dst, new_ver)
            print(f"  版本 {old_ver} -> {new_ver}")

    # 版本号对齐是无条件动作：--no-bump 只表示「不递增版本」，不应连对齐一起跳过，
    # 否则手动改过 plugin.json 版本后，marketplace.json 会一直停在旧版本。
    changed = sync_marketplace_versions()
    if changed:
        print("\nmarketplace.json 版本号已对齐：")
        for name, old, new in changed:
            print(f"  {name}: {old} -> {new}")

    print("\n" + "=" * 62)
    print("凭据扫描")
    print("=" * 62)
    hits = scan_secrets(REPO)
    if hits:
        print(f"✗ 发现 {len(hits)} 处疑似凭据，请勿提交：")
        for f, pat, label in hits:
            print(f"  - {f}  [{label}]  {pat}")
        return 1

    print("✓ 凭据扫描通过（0 命中），可以提交推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())

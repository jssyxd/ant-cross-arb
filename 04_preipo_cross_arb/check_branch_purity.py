#!/usr/bin/env python3
"""check_branch_purity.py — 分支纯净度守卫

背景：`main` 是**单账户 market maker**，`tree-twin` 是**两腿 delta 对冲 / 赚基差波动**。
两条线完全不同的策略，组件不得互相渗透。

本脚本在 `tree-twin` 上运行，断言「做市专用组件」没有出现在本分支。
之所以需要它：把文件从 `tree-twin` 删掉**不能阻止**未来 `main → tree-twin` 的 merge
把它们重新带回来（merge 会把 main 上仍存在的文件复活）。所以需要一个可执行的守卫，
让「污染」变成 CI/本地可见的失败，而不是静默发生。

用法：
    python check_branch_purity.py          # 退出码 0 = 纯净
    python check_branch_purity.py -v       # 打印全部检查项

在 main 上运行时本脚本自动跳过（返回 0），因为 main 本来就该有这些文件。
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(REPO)

# 仅属于 main（做市线）的路径前缀
MM_ONLY_PREFIXES = (
    "01_crypto_altcoin_perp_mm/",
    "02_rwa_tokenized_equity_mm/",
    "03_dex_concentrated_lp_mm/",
    "shared/glft_engine/",
    "shared/toxic_gate/",
    "shared/sentinel/",
    "shared/aggregator/",
    "shared/data_lake_client/",
    "experiments/",
    "audit_reports/",
    "kaggle_pipeline/",
    "tests/test_regime_sentinel.py",
    "tests/test_kaggle_bridge.py",
    "04_preipo_cross_arb/features/",     # VPIN/OFI 防毒单特征
    "04_preipo_cross_arb/models/",       # LightGBM 门控（合成标签）
    "04_preipo_cross_arb/workflow/",     # 重训流水线
    "scripts/ingest_90d_deep_lake.py",
    "scripts/ingest_cryptohftdata_lake.py",
    "scripts/verify_lake_governance.py",
)

# 仅属于 tree-twin（套利线）、绝不可出现在 main 的文件
ARB_ONLY_PREFIXES = (
    "04_preipo_cross_arb/",
)

# 两条线都必须存在的公共件
REQUIRED_EVERYWHERE = (
    "README.md",
    ".gitignore",
)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", REPO_ROOT, *args],
                          capture_output=True, text=True).stdout


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").strip()


def tracked_files() -> list[str]:
    """读 **索引**（`git ls-files`）而不是 HEAD。

    这是刻意的：守卫的用途是「提交前拦住污染」，而这时 HEAD 还是干净的旧树，
    索引才代表"即将提交的内容"。提交后索引与 HEAD 相同，含义不变。
    """
    return [f for f in git("ls-files").splitlines() if f]


def main() -> int:
    verbose = "-v" in sys.argv
    branch = current_branch()
    files = tracked_files()
    problems: list[str] = []

    if branch != "tree-twin":
        if verbose:
            print(f"branch={branch}: 纯净度守卫只在 tree-twin 生效，跳过。")
        return 0

    for f in files:
        if f.startswith(MM_ONLY_PREFIXES):
            problems.append(f"MM-only 文件混入 tree-twin: {f}")

    for need in REQUIRED_EVERYWHERE:
        if need not in files:
            problems.append(f"缺少必需文件: {need}")

    for f in files:
        if f.endswith(".pyc") or "__pycache__" in f:
            problems.append(f"已编译产物被提交: {f}")

    # 套利线的活引擎必须存在且未被意外替换
    live = "04_preipo_cross_arb/core/ant_cross_arb_v4_adaptive.py"
    if live not in files:
        problems.append(f"缺少活引擎: {live}")

    if verbose:
        print(f"branch={branch}, tracked={len(files)}")
        print(f"MM-only 前缀数={len(MM_ONLY_PREFIXES)}, 命中={sum(1 for f in files if f.startswith(MM_ONLY_PREFIXES))}")

    if problems:
        print("BRANCH PURITY FAILED")
        for p in problems:
            print("  -", p)
        print("\n修复：`git rm -r --cached <path>`（本分支不该有），"
              "并在 main 上确认这些文件仍存在。")
        return 1

    print("BRANCH PURITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
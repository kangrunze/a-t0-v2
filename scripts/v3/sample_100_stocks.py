#!/usr/bin/env python3
"""
V3 G1 前置：从 500 股池随机抽取 100 股，固定 seed 可复现。

输出: config/v3_sample_100.txt（每行一个6位代码）
用法:
    python scripts/v3/sample_100_stocks.py --seed 42 --n 100
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 读 thresholds.yaml 获取数据根目录（绕过 at0 包 import，避免 measurement .pyc 版本冲突）
def _load_data_root() -> Path:
    yaml_path = PROJECT_ROOT / "config" / "thresholds.yaml"
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        root = data.get("data", {}).get("root")
        if root:
            return Path(root) / "zz500_5min"
    except Exception:
        pass
    return PROJECT_ROOT / "data" / "zz500_5min"


def list_available_codes(data_dir: Path) -> list[str]:
    return sorted(f.stem for f in data_dir.glob("*.json") if f.stem.isdigit())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--n", type=int, default=100, help="抽样数量")
    parser.add_argument("--out", default=None, help="输出文件路径")
    args = parser.parse_args()

    zz500_dir = _load_data_root()
    all_codes = list_available_codes(zz500_dir)
    print(f"[sample] 500 股池可用: {len(all_codes)} 只")

    if args.n > len(all_codes):
        print(f"[sample] 警告: 请求 {args.n} 超过池子大小，取全部")
        args.n = len(all_codes)

    random.seed(args.seed)
    sampled = sorted(random.sample(all_codes, args.n))

    out_path = Path(args.out) if args.out else PROJECT_ROOT / "config" / "v3_sample_100.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for code in sampled:
            f.write(code + "\n")

    print(f"[sample] 已抽取 {len(sampled)} 只, seed={args.seed}")
    print(f"[sample] 输出: {out_path}")
    print(f"[sample] 前10只: {sampled[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

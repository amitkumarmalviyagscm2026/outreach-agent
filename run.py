#!/usr/bin/env python3
"""CLI entrypoint. Used both for local runs and by the GitHub Actions workflow.

    python run.py --sector "Pharma" --count 10 --mode test
    python run.py --sector "Pharma" --count 100 --mode full
"""
from __future__ import annotations

import argparse
import sys

from src.config import load_secrets
from src.pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Campus-outreach pipeline: sector -> Excel workbook")
    parser.add_argument("--sector", required=True, help="Target sector, e.g. 'Pharma'")
    parser.add_argument("--count", type=int, default=10, help="Number of companies (clamped by --mode)")
    parser.add_argument("--mode", choices=["test", "full"], default="test")
    args = parser.parse_args()

    secrets = load_secrets()

    try:
        output_path = run_pipeline(args.sector, args.count, args.mode, secrets)
    except Exception as exc:  # noqa: BLE001 -- top-level: any unhandled error must exit non-zero
        print(f"FATAL: pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Output: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

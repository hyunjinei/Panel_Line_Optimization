#!/usr/bin/env python
# [AGENT-ADD] Actual-data comparison wrapper for CA-CJH-Insertion.
"""Run actual-data comparison including CA-CJH-Insertion."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run actual PBS comparison with CA-CJH")
    parser.add_argument("--config", default="config_self_label_diff.yaml")
    parser.add_argument("--methods", default="SPT,LPT,SEAM_MIN,GA,CA_CJH")
    parser.add_argument("--beam-width", default="")
    args = parser.parse_args()

    command = [
        sys.executable,
        "main.py",
        "eval",
        "--config",
        args.config,
        "--yes",
        "--",
        "--methods",
        args.methods,
        "--mode",
        "2",
    ]
    if str(args.beam_width).strip():
        command.extend(["--ca_cjh_beam_width", str(args.beam_width)])
        command.extend(["--ca_cjh_enable_beam_search", "1"])
        command.extend(["--ca_cjh_use_all_insertion_positions", "0"])

    print("[CA-CJH actual] 실행 명령:", " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    main()

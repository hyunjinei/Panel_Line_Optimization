#!/usr/bin/env python
# [AGENT-ADD] Small smoke runner for CA-CJH-Insertion.
"""Run a tiny CA-CJH-Insertion smoke test without requiring an RL model."""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from enhanced_environment.common.utils_core import DataConverter
from runtime_config import load_runtime_config, set_runtime_config
from scheduling.common.cjh_panel_insertion import run_ca_cjh_insertion


def main() -> None:
    parser = argparse.ArgumentParser(description="CA-CJH-Insertion smoke test")
    parser.add_argument("--config", default="config_self_label_diff.yaml")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--output-dir", default="results/ca_cjh_smoke")
    parser.add_argument("--beam-width", default="")
    args = parser.parse_args()

    config = load_runtime_config(args.config)
    set_runtime_config(config)
    data_cfg = (config.get("data") or {}) if isinstance(config, dict) else {}
    excel_path = data_cfg.get("excel_path") or "environment/판넬 블록 데이터셋_250618_SNU.xlsx"
    sheet_name = data_cfg.get("sheet") or None

    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path, sheet_name=sheet_name)
    blocks = blocks[: max(1, int(args.limit))]
    start_date = min(block.max_start_date for block in blocks).strftime("%Y-%m-%d")
    os.makedirs(args.output_dir, exist_ok=True)

    settings = {
        "assembly_max_days": ((config.get("evaluation") or {}).get("assembly_max_days", 20) if isinstance(config, dict) else 20),
        "assembly_date_offset": ((config.get("evaluation") or {}).get("assembly_date_offset", 0) if isinstance(config, dict) else 0),
        "save_detailed_csv": False,
        "ca_cjh": (config.get("ca_cjh") or {}) if isinstance(config, dict) else {},
    }
    if str(args.beam_width).strip():
        settings["ca_cjh_beam_width"] = int(args.beam_width)
        settings["ca_cjh_enable_beam_search"] = True
        settings["ca_cjh_use_all_insertion_positions"] = False
    results, stats, _, _ = run_ca_cjh_insertion(
        blocks=blocks,
        metadata=metadata,
        start_date=start_date,
        result_folder=args.output_dir,
        settings=settings,
    )
    print(
        "CA-CJH smoke 완료: "
        f"rows={len(results)}, makespan={float(stats.get('makespan_hours', 0) or 0):.2f}h, "
        f"violations={stats.get('total_violations_primary', stats.get('total_violations', 0))}, "
        f"evals={stats.get('ca_cjh_evaluations')}"
    )


if __name__ == "__main__":
    main()

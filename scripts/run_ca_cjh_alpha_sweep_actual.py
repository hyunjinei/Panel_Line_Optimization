#!/usr/bin/env python
# [AGENT-ADD] Alpha sweep for CA-CJH actual-data experiments.
"""Run CA-CJH cost-mode alpha sweep on actual panel-line data."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from enhanced_environment.common.utils_core import DataConverter
from runtime_config import build_calendar_overrides, load_runtime_config, set_runtime_config
from scheduling.common.cjh_panel_insertion import run_ca_cjh_insertion


def _merge_eval_config(config: Dict) -> Dict:
    merged: Dict = {}
    if isinstance(config.get("eval"), dict):
        merged.update(config.get("eval") or {})
    if isinstance(config.get("evaluation"), dict):
        merged.update(config.get("evaluation") or {})
    return merged


def _primary(stats: Dict) -> int:
    for key in ("total_violations_primary_train", "total_violations_primary", "total_violations_train", "total_violations"):
        if key in stats:
            return int(stats.get(key) or 0)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="CA-CJH alpha sweep on actual data")
    parser.add_argument("--config", default="config_self_label_diff.yaml")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--feature-mode", default="process_plus_core")
    parser.add_argument("--priority-mode", default="cjh_difficulty")
    parser.add_argument("--beta-load", type=float, default=0.7)
    parser.add_argument("--load-amplifier", type=float, default=0.0)
    parser.add_argument("--alphas", default="0,0.1,0.5,1,2,5,10")
    args = parser.parse_args()

    os.environ["PBS_FORCE_DEBUG"] = "0"
    os.environ["PBS_DEBUG_VERBOSE"] = "0"

    config = load_runtime_config(args.config)
    set_runtime_config(config)
    eval_cfg = _merge_eval_config(config if isinstance(config, dict) else {})
    data_cfg = (config.get("data") or {}) if isinstance(config, dict) else {}
    excel_path = data_cfg.get("excel_path") or "environment/판넬 블록 데이터셋_250618_SNU.xlsx"
    sheet_name = data_cfg.get("sheet") or None
    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(excel_path, sheet_name=sheet_name)
    calendar_overrides = build_calendar_overrides()
    if calendar_overrides:
        metadata["calendar_overrides"] = calendar_overrides

    start_date = min(block.max_start_date for block in blocks).strftime("%Y-%m-%d")
    output_dir = args.output_dir or os.path.join("PPO", "eval", "ca_cjh_sweeps", datetime.now().strftime("%Y%m%d_%H%M%S_alpha_actual"))
    os.makedirs(output_dir, exist_ok=True)
    alpha_values = [float(part.strip()) for part in str(args.alphas).split(",") if part.strip()]

    rows: List[Dict[str, object]] = []
    for alpha in alpha_values:
        run_dir = os.path.join(output_dir, f"alpha_{str(alpha).replace('.', 'p')}")
        os.makedirs(run_dir, exist_ok=True)
        ca_cfg = dict((config.get("ca_cjh") or {}) if isinstance(config, dict) else {})
        ca_cfg.update({
            "feature_mode": args.feature_mode,
            "priority_mode": args.priority_mode,
            "beta_load": float(args.beta_load),
            "load_amplifier": float(args.load_amplifier),
            "objective_mode": "cost",
            "alpha": float(alpha),
            "feasible_priority": True,
            "respect_workshop_order": True,
            "allow_forced_prefix_override": False,
            "trace_enabled": False,
            "use_all_insertion_positions": True,
            "parallel": True,
            "workers": 0,
            "chunksize": "auto",
            "enable_beam_search": False,
            "beam_width": None,
        })
        _, stats, _, _ = run_ca_cjh_insertion(
            blocks=blocks,
            metadata=metadata,
            start_date=start_date,
            result_folder=run_dir,
            settings={
                "assembly_max_days": int(eval_cfg.get("assembly_max_days", 20) or 20),
                "assembly_date_offset": int(eval_cfg.get("assembly_date_offset", 0) or 0),
                "save_detailed_csv": False,
                "ca_cjh": ca_cfg,
            },
        )
        row = {
            "method": "CA_CJH",
            "sweep": "alpha",
            "feature_mode": args.feature_mode,
            "priority_mode": args.priority_mode,
            "beta_load": float(args.beta_load),
            "load_amplifier": float(args.load_amplifier),
            "allow_forced_prefix_override": bool(stats.get("ca_cjh_allow_forced_prefix_override", False)),
            "objective_mode": "cost",
            "alpha": float(alpha),
            "makespan": float(stats.get("makespan_hours", 0) or 0),
            "violations": _primary(stats),
            "total_violation_count": int(stats.get("ca_cjh_total_violation_count", stats.get("total_violations_raw", 0)) or 0),
            "runtime": float(stats.get("computation_seconds", 0) or 0),
            "evaluations": int(stats.get("ca_cjh_evaluations", 0) or 0),
            "workers": int(stats.get("ca_cjh_workers", 0) or 0),
            "parallel": bool(stats.get("ca_cjh_parallel", False)),
            "result_folder": run_dir,
        }
        rows.append(row)
        pd.DataFrame(rows).to_csv(os.path.join(output_dir, "alpha_sweep_actual.csv"), index=False, encoding="utf-8-sig")
        print(row)

    print(f"alpha sweep saved: {os.path.join(output_dir, 'alpha_sweep_actual.csv')}")


if __name__ == "__main__":
    main()

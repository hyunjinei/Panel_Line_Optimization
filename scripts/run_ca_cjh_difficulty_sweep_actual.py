#!/usr/bin/env python
# [AGENT-ADD] CJH-native priority sweep for CA-CJH actual-data experiments.
"""Run CA-CJH cjh_difficulty priority sweep on actual panel-line data."""

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


def _safe_label(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


def main() -> None:
    parser = argparse.ArgumentParser(description="CA-CJH cjh_difficulty sweep on actual data")
    parser.add_argument("--config", default="config_self_label_diff.yaml")
    parser.add_argument("--output-dir", default="")
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
    output_dir = args.output_dir or os.path.join(
        "PPO",
        "eval",
        "ca_cjh_sweeps",
        datetime.now().strftime("%Y%m%d_%H%M%S_difficulty_actual"),
    )
    os.makedirs(output_dir, exist_ok=True)

    sweep_configs: List[Dict[str, object]] = [
        {
            "config_name": "CJH abnormality dominant",
            "w_load": 0.0,
            "w_abnormal": 0.7,
            "w_shape_dev": 0.3,
            "w_urgency": 0.0,
            "w_risk": 0.0,
            "load_amplifier": 0.0,
        },
        {
            "config_name": "CJH balanced",
            "w_load": 0.0,
            "w_abnormal": 0.5,
            "w_shape_dev": 0.5,
            "w_urgency": 0.0,
            "w_risk": 0.0,
            "load_amplifier": 0.0,
        },
        {
            "config_name": "CJH shape deviation dominant",
            "w_load": 0.0,
            "w_abnormal": 0.3,
            "w_shape_dev": 0.7,
            "w_urgency": 0.0,
            "w_risk": 0.0,
            "load_amplifier": 0.0,
        },
        {
            "config_name": "CJH abnormality only",
            "w_load": 0.0,
            "w_abnormal": 1.0,
            "w_shape_dev": 0.0,
            "w_urgency": 0.0,
            "w_risk": 0.0,
            "load_amplifier": 0.0,
        },
        {
            "config_name": "CJH shape deviation only",
            "w_load": 0.0,
            "w_abnormal": 0.0,
            "w_shape_dev": 1.0,
            "w_urgency": 0.0,
            "w_risk": 0.0,
            "load_amplifier": 0.0,
        },
        {
            "config_name": "CJH with load amplifier ablation",
            "w_load": 0.0,
            "w_abnormal": 0.7,
            "w_shape_dev": 0.3,
            "w_urgency": 0.0,
            "w_risk": 0.0,
            "load_amplifier": 0.35,
        },
    ]

    rows: List[Dict[str, object]] = []
    for idx, sweep_cfg in enumerate(sweep_configs, 1):
        label = f"{idx:02d}_{_safe_label(str(sweep_cfg['config_name']))}"
        run_dir = os.path.join(output_dir, label)
        os.makedirs(run_dir, exist_ok=True)
        ca_cfg = dict((config.get("ca_cjh") or {}) if isinstance(config, dict) else {})
        ca_cfg.update({
            "feature_mode": "process_plus_core",
            "priority_mode": "cjh_difficulty",
            "objective_mode": "strict_lexicographic",
            "alpha": 0.0,
            "feasible_priority": True,
            "respect_workshop_order": True,
            "allow_forced_prefix_override": False,
            "trace_enabled": True,
            "trace_output_dir": "ca_cjh_traces",
            "use_all_insertion_positions": True,
            "parallel": True,
            "workers": 0,
            "chunksize": "auto",
            "enable_beam_search": False,
            "beam_width": None,
            "w_load": float(sweep_cfg["w_load"]),
            "w_abnormal": float(sweep_cfg["w_abnormal"]),
            "w_shape_dev": float(sweep_cfg["w_shape_dev"]),
            "w_urgency": float(sweep_cfg["w_urgency"]),
            "w_risk": float(sweep_cfg["w_risk"]),
            "load_amplifier": float(sweep_cfg["load_amplifier"]),
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
        trace_dir = os.path.join(run_dir, "ca_cjh_traces")
        row = {
            "method": "CA_CJH",
            "sweep": "cjh_difficulty",
            "config_name": str(sweep_cfg["config_name"]),
            "feature_mode": "process_plus_core",
            "priority_mode": "cjh_difficulty",
            "objective_mode": "strict_lexicographic",
            "feasible_priority": bool(stats.get("ca_cjh_feasible_priority", True)),
            "respect_workshop_order": bool(stats.get("ca_cjh_respect_workshop_order", True)),
            "allow_forced_prefix_override": bool(stats.get("ca_cjh_allow_forced_prefix_override", False)),
            "w_load": float(sweep_cfg["w_load"]),
            "w_abnormal": float(sweep_cfg["w_abnormal"]),
            "w_shape_dev": float(sweep_cfg["w_shape_dev"]),
            "w_urgency": float(sweep_cfg["w_urgency"]),
            "w_risk": float(sweep_cfg["w_risk"]),
            "load_amplifier": float(sweep_cfg["load_amplifier"]),
            "makespan_hours": float(stats.get("makespan_hours", 0) or 0),
            "primary_violation_count": _primary(stats),
            "total_violation_count": int(stats.get("ca_cjh_total_violation_count", stats.get("total_violations_raw", 0)) or 0),
            "longi_abs_diff": float(stats.get("ca_cjh_longi_abs_diff", 0.0) or 0.0),
            "runtime_sec": float(stats.get("computation_seconds", 0) or 0),
            "total_evaluations": int(stats.get("ca_cjh_evaluations", 0) or 0),
            "workers": int(stats.get("ca_cjh_workers", 0) or 0),
            "parallel": bool(stats.get("ca_cjh_parallel", False)),
            "result_folder": run_dir,
            "global_priority_csv": os.path.join(trace_dir, "global_priority.csv"),
            "trace_csv": os.path.join(trace_dir, "insertion_trace.csv"),
        }
        rows.append(row)
        pd.DataFrame(rows).to_csv(os.path.join(output_dir, "difficulty_sweep_actual.csv"), index=False, encoding="utf-8-sig")
        print(row)

    print(f"cjh_difficulty sweep saved: {os.path.join(output_dir, 'difficulty_sweep_actual.csv')}")


if __name__ == "__main__":
    main()

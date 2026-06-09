#!/usr/bin/env python
# [AGENT-ADD] Consistency check between normal LPT evaluation and CA-CJH evaluator.
"""Check that CA-CJH's forced-prefix evaluator reproduces a normal LPT schedule."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from enhanced_environment.common.utils_core import DataConverter
from runtime_config import build_calendar_overrides, load_runtime_config, set_runtime_config
from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import (
    run_assembly_decoding_sequence_with_blocks,
)
from scheduling.common.cjh_panel_insertion import evaluate_panel_sequence_for_cjh


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


def _extract_step_sequence(step_info: List[Dict]) -> List[int]:
    sequence: List[int] = []
    seen = set()
    for row in step_info or []:
        block_id = row.get("selected_block_id")
        if block_id is None:
            continue
        block_id = int(block_id)
        if block_id not in seen:
            sequence.append(block_id)
            seen.add(block_id)
    return sequence


def main() -> None:
    parser = argparse.ArgumentParser(description="CA-CJH evaluator consistency check")
    parser.add_argument("--config", default="config_self_label_diff.yaml")
    parser.add_argument("--output-dir", default="/tmp/ca_cjh_consistency")
    parser.add_argument("--tolerance", type=float, default=1e-6)
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
    max_days = int(eval_cfg.get("assembly_max_days", 20) or 20)
    date_offset = int(eval_cfg.get("assembly_date_offset", 0) or 0)
    os.makedirs(args.output_dir, exist_ok=True)

    lpt_results, lpt_stats, lpt_steps = run_assembly_decoding_sequence_with_blocks(
        blocks=blocks,
        metadata=metadata,
        decoding_type="assembly",
        selection_method="lpt",
        max_days=max_days,
        start_date=start_date,
        date_offset=date_offset,
        output_csv=os.path.join(args.output_dir, "normal_lpt_evaluation_results.csv"),
        save_csv=True,
        save_detailed=False,
        return_step_info=True,
    )
    lpt_sequence = _extract_step_sequence(lpt_steps)
    if len(lpt_sequence) != len(blocks):
        print(f"[FAIL] LPT sequence length mismatch: {len(lpt_sequence)} != {len(blocks)}")
        sys.exit(2)

    evaluator_result = evaluate_panel_sequence_for_cjh(
        sequence=lpt_sequence,
        blocks=blocks,
        metadata=metadata,
        config={
            "assembly_max_days": max_days,
            "assembly_date_offset": date_offset,
            "start_date": start_date,
            "completion_policy": "priority",
            "output_csv": os.path.join(args.output_dir, "cjh_evaluator_lpt_sequence_results.csv"),
            "allow_forced_prefix_override": True,
            "quiet": True,
        },
        partial=False,
        save_csv=True,
        save_detailed=False,
    )

    normal_makespan = float(lpt_stats.get("makespan_hours", 0) or 0)
    normal_violations = _primary(lpt_stats)
    eval_makespan = float(evaluator_result.get("makespan_hours", 0) or 0)
    eval_violations = int(evaluator_result.get("hard_violation_count", 0) or 0)

    print("normal_lpt:")
    print(f"  makespan_hours={normal_makespan:.10f}")
    print(f"  primary_violations={normal_violations}")
    print(f"  rows={len(lpt_results)}")
    print("ca_cjh_evaluator_on_lpt_sequence:")
    print(f"  makespan_hours={eval_makespan:.10f}")
    print(f"  primary_violations={eval_violations}")
    print(f"  rows={len(evaluator_result.get('schedule_results') or [])}")

    same_makespan = abs(normal_makespan - eval_makespan) <= float(args.tolerance)
    same_violations = normal_violations == eval_violations
    if not same_makespan or not same_violations:
        print("[FAIL] CA-CJH evaluator does not reproduce normal LPT evaluation.")
        sys.exit(1)
    print("[OK] CA-CJH evaluator reproduces normal LPT evaluation.")


if __name__ == "__main__":
    main()

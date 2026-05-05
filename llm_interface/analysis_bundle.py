"""Structured analysis bundle builder for LLM-sidecar explanations.

# [AGENT-ADD] The bundle is grounded only on scheduler/final-audit outputs and
# decision traces. It is designed for test/eval analysis, not training.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .schemas import ComparisonSummary, ResultSummary


def _load_trace_csv(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    csv_path = Path(path)
    if not csv_path.exists():
        return pd.DataFrame()
    return pd.read_csv(csv_path, encoding="utf-8-sig")


def _top_moved_blocks(before: ResultSummary, after: ResultSummary, limit: int = 10) -> List[Dict[str, Any]]:
    before_pos = {block_id: idx + 1 for idx, block_id in enumerate(before.sequence)}
    after_pos = {block_id: idx + 1 for idx, block_id in enumerate(after.sequence)}
    moved: List[Dict[str, Any]] = []
    for block_id, new_pos in after_pos.items():
        old_pos = before_pos.get(block_id)
        if old_pos is None or old_pos == new_pos:
            continue
        moved.append(
            {
                "block_id": int(block_id),
                "before_position": int(old_pos),
                "after_position": int(new_pos),
                "position_delta": int(new_pos - old_pos),
                "absolute_shift": int(abs(new_pos - old_pos)),
            }
        )
    moved.sort(key=lambda item: (-item["absolute_shift"], item["block_id"]))
    return moved[:limit]


def _constraint_delta(before: ResultSummary, after: ResultSummary) -> List[Dict[str, Any]]:
    keys = sorted(set(before.top_constraints) | set(after.top_constraints))
    rows: List[Dict[str, Any]] = []
    for key in keys:
        before_count = int(before.top_constraints.get(key, 0))
        after_count = int(after.top_constraints.get(key, 0))
        if before_count == after_count:
            continue
        rows.append(
            {
                "constraint_id": key,
                "before": before_count,
                "after": after_count,
                "delta": after_count - before_count,
            }
        )
    rows.sort(key=lambda item: (-abs(item["delta"]), item["constraint_id"]))
    return rows


def _date_count_delta(before: ResultSummary, after: ResultSummary) -> List[Dict[str, Any]]:
    keys = sorted(set(before.date_counts) | set(after.date_counts))
    rows: List[Dict[str, Any]] = []
    for key in keys:
        before_count = int(before.date_counts.get(key, 0))
        after_count = int(after.date_counts.get(key, 0))
        if before_count == after_count:
            continue
        rows.append(
            {
                "date": str(key),
                "before": before_count,
                "after": after_count,
                "delta": after_count - before_count,
            }
        )
    rows.sort(key=lambda item: (-abs(item["delta"]), item["date"]))
    return rows


def _bay_delta(before: ResultSummary, after: ResultSummary, limit: int = 10) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for block_id, after_bay in after.bay_assignments.items():
        before_bay = before.bay_assignments.get(block_id)
        if before_bay is None or before_bay == after_bay:
            continue
        rows.append(
            {
                "block_id": int(block_id),
                "before_bay": before_bay,
                "after_bay": after_bay,
            }
        )
    rows.sort(key=lambda item: item["block_id"])
    return rows[:limit]


def _trace_preview(trace_df: pd.DataFrame, limit: int = 8) -> List[Dict[str, Any]]:
    if trace_df.empty:
        return []
    cols = [
        "step",
        "selected_block_id",
        "available_count",
        "current_date",
        "current_datetime",
        "selection_method",
        "selection_reason",
        "masking_stage_used",
        "forced_override",
        "precedence_blocked_ids",
        "manual_bay_override",
        "selected_three_bay_check",
        "assigned_bay",
        "confidence",
    ]
    available_cols = [col for col in cols if col in trace_df.columns]
    return trace_df[available_cols].head(limit).to_dict(orient="records")


def _trace_diff_preview(before_df: pd.DataFrame, after_df: pd.DataFrame, limit: int = 10) -> List[Dict[str, Any]]:
    if before_df.empty or after_df.empty:
        return []
    before_map = {int(row["step"]): row for _, row in before_df.iterrows() if pd.notna(row.get("step"))}
    after_map = {int(row["step"]): row for _, row in after_df.iterrows() if pd.notna(row.get("step"))}
    changed: List[Dict[str, Any]] = []
    for step in sorted(set(before_map) & set(after_map)):
        before_row = before_map[step]
        after_row = after_map[step]
        before_block = before_row.get("selected_block_id")
        after_block = after_row.get("selected_block_id")
        if before_block == after_block:
            continue
        changed.append(
            {
                "step": step,
                "before_block_id": int(before_block) if pd.notna(before_block) else None,
                "after_block_id": int(after_block) if pd.notna(after_block) else None,
                "before_reason": before_row.get("selection_reason", ""),
                "after_reason": after_row.get("selection_reason", ""),
                "before_available_count": int(before_row.get("available_count")) if pd.notna(before_row.get("available_count")) else None,
                "after_available_count": int(after_row.get("available_count")) if pd.notna(after_row.get("available_count")) else None,
            }
        )
    return changed[:limit]


def _forced_steps(trace_df: pd.DataFrame) -> List[Dict[str, Any]]:
    if trace_df.empty or "forced_override" not in trace_df.columns:
        return []
    mask = trace_df["forced_override"].fillna(False).astype(bool)
    if not mask.any():
        return []
    cols = [col for col in ["step", "selected_block_id", "selection_reason", "masking_stage_used", "manual_bay_override"] if col in trace_df.columns]
    return trace_df.loc[mask, cols].to_dict(orient="records")


def _summary_payload(summary: ResultSummary) -> Dict[str, Any]:
    return {
        "label": summary.label,
        "makespan_hours": float(summary.makespan_hours),
        "primary_violations": int(summary.primary_violations),
        "raw_violations": int(summary.raw_violations) if summary.raw_violations is not None else None,
        "sequence_head": [int(x) for x in summary.sequence[:15]],
        "sequence_length": len(summary.sequence),
        "top_constraints": {str(k): int(v) for k, v in summary.top_constraints.items()},
        "date_counts": {str(k): int(v) for k, v in summary.date_counts.items()},
        "notes": list(summary.notes),
    }


def build_analysis_bundle(
    comparison: ComparisonSummary,
    user_request: str,
    before_stats: Optional[Dict[str, Any]],
    after_stats: Optional[Dict[str, Any]],
    before_trace_csv: str | Path | None,
    after_trace_csv: str | Path | None,
    explanation: str,
) -> Dict[str, Any]:
    before_trace_df = _load_trace_csv(before_trace_csv)
    after_trace_df = _load_trace_csv(after_trace_csv)

    before = comparison.before
    after = comparison.after

    bundle = {
        "request": user_request,
        "before": _summary_payload(before),
        "after": _summary_payload(after),
        "delta": {
            "makespan_hours": float(after.makespan_hours - before.makespan_hours),
            "primary_violations": int(after.primary_violations - before.primary_violations),
            "raw_violations": (
                int(after.raw_violations - before.raw_violations)
                if before.raw_violations is not None and after.raw_violations is not None
                else None
            ),
            "constraint_changes": _constraint_delta(before, after),
            "date_count_changes": _date_count_delta(before, after),
            "bay_changes": _bay_delta(before, after),
            "top_moved_blocks": _top_moved_blocks(before, after),
        },
        "trace": {
            "before_csv": str(before_trace_csv) if before_trace_csv else None,
            "after_csv": str(after_trace_csv) if after_trace_csv else None,
            "before_steps": int(len(before_trace_df)),
            "after_steps": int(len(after_trace_df)),
            "before_preview": _trace_preview(before_trace_df),
            "after_preview": _trace_preview(after_trace_df),
            "changed_steps_preview": _trace_diff_preview(before_trace_df, after_trace_df),
            "forced_steps_after": _forced_steps(after_trace_df),
        },
        "official_stats": {
            "before": before_stats or {},
            "after": after_stats or {},
        },
        "explanation": explanation,
    }
    return bundle


def save_analysis_bundle(bundle: Dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

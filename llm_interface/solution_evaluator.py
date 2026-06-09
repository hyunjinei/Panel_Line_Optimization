"""Grounded solution evaluation for LLM-assisted schedule edits."""

# [AGENT-ADD] This evaluator checks whether a generated after-schedule actually
# satisfies the structured request and whether the claimed current state is
# consistent with the before-schedule used by the experiment.

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd

from .schemas import ScheduleEditRequest
from .validation import request_to_dict


def _metric_anchor_df(df: pd.DataFrame) -> pd.DataFrame:
    if "is_metric_anchor_row" not in df.columns:
        return df
    anchor = pd.to_numeric(df["is_metric_anchor_row"], errors="coerce").fillna(0).astype(int) == 1
    if int(anchor.sum()) == 0:
        return df
    return df.loc[anchor].copy()


def _rows_to_df(rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows or []))


def _sequence_from_df(df: pd.DataFrame) -> List[int]:
    metric_df = _metric_anchor_df(df)
    if metric_df.empty or "block_id" not in metric_df.columns:
        return []
    if "am_sequence" in metric_df.columns:
        metric_df = metric_df.sort_values("am_sequence")
    return [int(value) for value in metric_df["block_id"].dropna().tolist()]


def _bay_assignments_from_df(df: pd.DataFrame) -> Dict[int, str]:
    metric_df = _metric_anchor_df(df)
    if metric_df.empty or "block_id" not in metric_df.columns or "assigned_bay" not in metric_df.columns:
        return {}
    assignments: Dict[int, str] = {}
    for _, row in metric_df.iterrows():
        try:
            assignments[int(row["block_id"])] = str(row.get("assigned_bay") or "").upper()
        except Exception:
            continue
    return assignments


def _parse_list_cell(value: Any) -> List[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text or text == "[]":
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return [token.strip() for token in text.split(",") if token.strip()]


def _constraint_family_counts(df: pd.DataFrame) -> Dict[str, int]:
    metric_df = _metric_anchor_df(df)
    if metric_df.empty:
        return {}
    source_col = "constraint_families" if "constraint_families" in metric_df.columns else "raw_constraint_families"
    if source_col not in metric_df.columns:
        return {}
    counts: Dict[str, int] = {}
    for raw in metric_df[source_col].tolist():
        for family in _parse_list_cell(raw):
            counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _total(df: pd.DataFrame, column: str) -> int:
    metric_df = _metric_anchor_df(df)
    if column not in metric_df.columns:
        return 0
    return int(pd.to_numeric(metric_df[column], errors="coerce").fillna(0).sum())


def _max_float(df: pd.DataFrame, column: str) -> float:
    metric_df = _metric_anchor_df(df)
    if column not in metric_df.columns:
        return 0.0
    return float(pd.to_numeric(metric_df[column], errors="coerce").fillna(0).max())


def _audit_synced(df: pd.DataFrame) -> bool:
    if "audit_synced" not in df.columns:
        return False
    values = df["audit_synced"].dropna().astype(str).str.lower().tolist()
    return bool(values) and all(value in {"true", "1", "yes"} for value in values)


def _position_map(sequence: Sequence[int]) -> Dict[int, int]:
    return {int(block_id): idx for idx, block_id in enumerate(sequence)}


def evaluate_solution(
    *,
    request: ScheduleEditRequest,
    before_rows: Sequence[Dict[str, Any]],
    after_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate request satisfaction, current-state consistency, and audit metrics."""

    before_df = _rows_to_df(before_rows)
    after_df = _rows_to_df(after_rows)
    before_sequence = _sequence_from_df(before_df)
    after_sequence = _sequence_from_df(after_df)
    after_pos = _position_map(after_sequence)
    after_bays = _bay_assignments_from_df(after_df)

    checks: List[Dict[str, Any]] = []
    current_state_checks: List[Dict[str, Any]] = []

    for constraint in request.constraints:
        ctype = str(constraint.type or "")
        if ctype == "freeze_prefix":
            block_ids = [int(block_id) for block_id in constraint.block_ids or []]
            before_prefix = before_sequence[: len(block_ids)]
            after_prefix = after_sequence[: len(block_ids)]
            checks.append({
                "type": ctype,
                "expected_prefix": block_ids,
                "actual_after_prefix": after_prefix,
                "ok": after_prefix == block_ids,
            })
            current_state_checks.append({
                "type": ctype,
                "expected_current_prefix": block_ids,
                "actual_before_prefix": before_prefix,
                "ok": before_prefix == block_ids,
                "note": "freeze_prefix는 실제 실행 중 prefix와 before schedule prefix가 같을 때 운영 해석이 안전합니다.",
            })

        elif ctype == "priority_block":
            if constraint.block_id is None:
                continue
            block_id = int(constraint.block_id)
            frozen_count = 0
            for other in request.constraints:
                if other.type == "freeze_prefix":
                    frozen_count += len(other.block_ids or [])
            actual_position = after_pos.get(block_id)
            checks.append({
                "type": ctype,
                "block_id": block_id,
                "expected_position_after_frozen_prefix": frozen_count,
                "actual_position": actual_position,
                "ok": actual_position == frozen_count,
            })

        elif ctype == "delayed_block":
            if constraint.block_id is None:
                continue
            delayed_id = int(constraint.block_id)
            priority_ids = [
                int(other.block_id)
                for other in request.constraints
                if other.type == "priority_block" and other.block_id is not None
            ]
            ok = all(after_pos.get(priority_id, 10**9) < after_pos.get(delayed_id, -1) for priority_id in priority_ids)
            checks.append({
                "type": ctype,
                "block_id": delayed_id,
                "priority_before_ids": priority_ids,
                "actual_position": after_pos.get(delayed_id),
                "ok": ok,
            })

        elif ctype == "fixed_position":
            if constraint.block_id is None or constraint.position is None:
                continue
            block_id = int(constraint.block_id)
            position = int(constraint.position)
            checks.append({
                "type": ctype,
                "block_id": block_id,
                "expected_position": position,
                "actual_position": after_pos.get(block_id),
                "ok": after_pos.get(block_id) == position,
            })

        elif ctype == "precedence":
            if constraint.before_block_id is None or constraint.after_block_id is None:
                continue
            before_id = int(constraint.before_block_id)
            after_id = int(constraint.after_block_id)
            checks.append({
                "type": ctype,
                "before_block_id": before_id,
                "after_block_id": after_id,
                "before_position": after_pos.get(before_id),
                "after_position": after_pos.get(after_id),
                "ok": after_pos.get(before_id, 10**9) < after_pos.get(after_id, -1),
            })

        elif ctype == "manual_bay_assignment":
            if constraint.block_id is None or not constraint.bay:
                continue
            block_id = int(constraint.block_id)
            expected_bay = str(constraint.bay).upper()
            actual_bay = after_bays.get(block_id)
            checks.append({
                "type": ctype,
                "block_id": block_id,
                "expected_bay": expected_bay,
                "actual_bay": actual_bay,
                "ok": actual_bay == expected_bay,
            })

    request_ok = all(bool(check.get("ok")) for check in checks) if checks else False
    current_state_ok = all(bool(check.get("ok")) for check in current_state_checks) if current_state_checks else True

    before_metrics = {
        "makespan_hours": _max_float(before_df, "makespan_hours"),
        "primary_violations": _total(before_df, "violations_primary_count"),
        "raw_violations": _total(before_df, "violations_raw_count"),
        "constraint_family_counts": _constraint_family_counts(before_df),
        "audit_synced": _audit_synced(before_df),
    }
    after_metrics = {
        "makespan_hours": _max_float(after_df, "makespan_hours"),
        "primary_violations": _total(after_df, "violations_primary_count"),
        "raw_violations": _total(after_df, "violations_raw_count"),
        "constraint_family_counts": _constraint_family_counts(after_df),
        "audit_synced": _audit_synced(after_df),
    }

    return {
        "request": request_to_dict(request),
        "request_satisfaction_ok": bool(request_ok),
        "request_satisfaction_checks": checks,
        "current_state_consistency_ok": bool(current_state_ok),
        "current_state_consistency_checks": current_state_checks,
        "final_audit_used": bool(before_metrics["audit_synced"] and after_metrics["audit_synced"]),
        "before": before_metrics,
        "after": after_metrics,
        "delta": {
            "makespan_hours": after_metrics["makespan_hours"] - before_metrics["makespan_hours"],
            "primary_violations": after_metrics["primary_violations"] - before_metrics["primary_violations"],
            "raw_violations": after_metrics["raw_violations"] - before_metrics["raw_violations"],
        },
        "interpretation_flags": {
            "operationally_valid_current_state": bool(current_state_ok),
            "use_metric_delta_as_operational_evidence": bool(current_state_ok and request_ok),
        },
    }


def save_solution_evaluation(evaluation: Dict[str, Any], output_dir: str | Path) -> Dict[str, str]:
    """Save JSON/CSV/Markdown views of solution evaluation."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "solution_evaluation.json"
    json_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    checks_path = out_dir / "solution_request_checks.csv"
    pd.DataFrame(evaluation.get("request_satisfaction_checks") or []).to_csv(checks_path, index=False, encoding="utf-8-sig")

    current_state_path = out_dir / "solution_current_state_checks.csv"
    pd.DataFrame(evaluation.get("current_state_consistency_checks") or []).to_csv(
        current_state_path,
        index=False,
        encoding="utf-8-sig",
    )

    md_path = out_dir / "solution_evaluation.md"
    lines = [
        "# Solution Evaluation",
        "",
        f"- request_satisfaction_ok: {evaluation.get('request_satisfaction_ok')}",
        f"- current_state_consistency_ok: {evaluation.get('current_state_consistency_ok')}",
        f"- final_audit_used: {evaluation.get('final_audit_used')}",
        f"- use_metric_delta_as_operational_evidence: {evaluation.get('interpretation_flags', {}).get('use_metric_delta_as_operational_evidence')}",
        "",
        "## Metrics",
        "",
        f"- before makespan: {evaluation.get('before', {}).get('makespan_hours')} h",
        f"- after makespan: {evaluation.get('after', {}).get('makespan_hours')} h",
        f"- primary violation delta: {evaluation.get('delta', {}).get('primary_violations')}",
        f"- raw violation delta: {evaluation.get('delta', {}).get('raw_violations')}",
        "",
        "## Note",
        "",
        "Metric improvement should be interpreted as operational evidence only when the current-state consistency check passes.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "solution_evaluation_json": str(json_path),
        "solution_request_checks_csv": str(checks_path),
        "solution_current_state_checks_csv": str(current_state_path),
        "solution_evaluation_md": str(md_path),
    }


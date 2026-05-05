"""Adapters from existing PBS result CSVs to compact summaries."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Dict, List

import pandas as pd

from .schemas import ResultSummary


# [AGENT-ADD] Explanation summaries must use the same representative rows as the official
# scheduler metrics; otherwise subassembly-expanded rows inflate daily counts and constraint counts.
def _metric_anchor_df(df: pd.DataFrame) -> pd.DataFrame:
    if "is_metric_anchor_row" not in df.columns:
        return df
    anchor = pd.to_numeric(df["is_metric_anchor_row"], errors="coerce").fillna(0).astype(int) == 1
    if int(anchor.sum()) == 0:
        return df
    return df.loc[anchor].copy()



def _parse_list_cell(value) -> List[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    raw = str(value).strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except (ValueError, SyntaxError):
        pass
    return [token.strip() for token in raw.split(",") if token.strip()]



def _extract_sequence(df: pd.DataFrame) -> List[int]:
    if "block_id" not in df.columns or "am_sequence" not in df.columns:
        return []
    ordered = df.sort_values("am_sequence")
    return [int(v) for v in ordered["block_id"].tolist()]



def _constraint_counts(df: pd.DataFrame) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    if "constraint_ids" in df.columns:
        for raw in df["constraint_ids"].tolist():
            counter.update(_parse_list_cell(raw))
    if not counter and "raw_constraint_ids" in df.columns:
        for raw in df["raw_constraint_ids"].tolist():
            counter.update(_parse_list_cell(raw))
    return dict(counter)



def _top_constraints(df: pd.DataFrame) -> Dict[str, int]:
    counter = _constraint_counts(df)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:5])



def _date_counts(df: pd.DataFrame) -> Dict[str, int]:
    if "date" not in df.columns:
        return {}
    counts = (
        df["date"].dropna().astype(str).value_counts().sort_index()
    )
    return {str(index): int(value) for index, value in counts.items()}



def _bay_assignments(df: pd.DataFrame) -> Dict[int, str]:
    if "block_id" not in df.columns or "assigned_bay" not in df.columns:
        return {}
    assignments: Dict[int, str] = {}
    for _, row in df.iterrows():
        try:
            block_id = int(row["block_id"])
        except Exception:
            continue
        assignments[block_id] = str(row.get("assigned_bay", "") or "")
    return assignments



def summarize_result_rows(rows: list[dict], label: str | None = None) -> ResultSummary:
    """Create a compact summary directly from in-memory scheduler rows.

    # [AGENT-ADD] Interactive rescheduling should not depend on scheduler-side CSV saving.
    """

    if not rows:
        return ResultSummary(
            label=label or "empty",
            sequence=[],
            makespan_hours=0.0,
            primary_violations=0,
            raw_violations=0,
            top_constraints={},
            date_counts={},
            bay_assignments={},
            notes=["empty_rows"],
        )

    df = pd.DataFrame(rows)
    metric_df = _metric_anchor_df(df)
    makespan_hours = float(pd.to_numeric(metric_df.get("makespan_hours"), errors="coerce").fillna(0).max())
    primary = int(pd.to_numeric(metric_df.get("violations_primary_count"), errors="coerce").fillna(0).sum())
    raw = None
    if "violations_raw_count" in metric_df.columns:
        raw = int(pd.to_numeric(metric_df.get("violations_raw_count"), errors="coerce").fillna(0).sum())

    notes: List[str] = []
    if "audit_synced" in df.columns:
        synced_values = list(df["audit_synced"].dropna().unique())
        if synced_values:
            notes.append(f"audit_synced={synced_values}")
    if "forced_override" in df.columns:
        forced_count = int(pd.to_numeric(df.get("forced_override"), errors="coerce").fillna(0).sum())
        if forced_count:
            notes.append(f"forced_override_rows={forced_count}")

    return ResultSummary(
        label=label or "rows",
        sequence=_extract_sequence(metric_df),
        makespan_hours=makespan_hours,
        primary_violations=primary,
        raw_violations=raw,
        top_constraints=_top_constraints(metric_df),
        date_counts=_date_counts(metric_df),
        bay_assignments=_bay_assignments(metric_df),
        notes=notes,
    )



def summarize_results_csv(csv_path: str, label: str | None = None) -> ResultSummary:
    """Create a compact summary from an existing PBS results CSV.

    # [AGENT-ADD] This reads the current canonical result format produced by the scheduler.
    """

    path = Path(csv_path)
    df = pd.read_csv(path, encoding="utf-8-sig")
    metric_df = _metric_anchor_df(df)
    makespan_hours = float(pd.to_numeric(metric_df.get("makespan_hours"), errors="coerce").fillna(0).max())
    primary = int(pd.to_numeric(metric_df.get("violations_primary_count"), errors="coerce").fillna(0).sum())
    raw = None
    if "violations_raw_count" in metric_df.columns:
        raw = int(pd.to_numeric(metric_df.get("violations_raw_count"), errors="coerce").fillna(0).sum())

    notes: List[str] = []
    if "audit_synced" in df.columns:
        synced_values = list(df["audit_synced"].dropna().unique())
        if synced_values:
            notes.append(f"audit_synced={synced_values}")

    return ResultSummary(
        label=label or path.stem,
        sequence=_extract_sequence(metric_df),
        makespan_hours=makespan_hours,
        primary_violations=primary,
        raw_violations=raw,
        top_constraints=_top_constraints(metric_df),
        date_counts=_date_counts(metric_df),
        bay_assignments=_bay_assignments(metric_df),
        notes=notes,
    )

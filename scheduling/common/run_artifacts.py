"""Run artifact helpers for reproducible scheduling experiments."""

# [AGENT-ADD] Thesis-quality reproducibility helpers shared by standalone schedulers.

from __future__ import annotations

import ast
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd


def sanitize_run_tag(raw_value: Optional[str], *, default_prefix: str = "run") -> str:
    """[AGENT-ADD] Convert a free-form run tag into a filesystem-safe slug."""
    text = (raw_value or "").strip()
    if not text:
        text = f"{default_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("._-")
    return text or f"{default_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def maybe_load_runtime_config(config_path: Optional[str]) -> Optional[Path]:
    """[AGENT-ADD] Load runtime config for standalone scripts when available."""
    if not config_path:
        return None

    candidate = Path(config_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if not candidate.exists():
        return None

    from runtime_config import load_runtime_config, set_runtime_config

    loaded = load_runtime_config(str(candidate))
    set_runtime_config(loaded)
    return candidate


def resolve_output_path(
    *,
    default_filename: str,
    mode: str,
    output_csv: Optional[str] = None,
    output_dir: Optional[str] = None,
    run_tag: Optional[str] = None,
) -> Path:
    """[AGENT-ADD] Resolve the CSV path while keeping legacy defaults when unset."""
    if output_csv:
        target = Path(output_csv).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    if output_dir or run_tag:
        base_dir = Path(output_dir).expanduser() if output_dir else (Path.cwd() / "results" / "fresh_runs")
        if not base_dir.is_absolute():
            base_dir = Path.cwd() / base_dir
        tag = sanitize_run_tag(run_tag, default_prefix=mode)
        run_dir = base_dir / tag / mode
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir / default_filename

    return Path.cwd() / default_filename


def mirror_to_legacy_path(saved_path: Path, legacy_filename: str) -> Path:
    """[AGENT-ADD] Keep legacy root CSV names only for canonical default runs.

    [AGENT-EDIT] Experimental outputs under results/fresh_runs must not overwrite the
    root legacy CSV names. Otherwise ablation runs can masquerade as baseline outputs.
    """
    cwd = Path.cwd().resolve()
    saved_resolved = saved_path.resolve()
    try:
        relative_path = saved_resolved.relative_to(cwd)
    except ValueError:
        relative_path = None

    # [AGENT-EDIT] Do not let tagged experiment artifacts overwrite root summary CSVs.
    if relative_path is not None and len(relative_path.parts) >= 2 and relative_path.parts[:2] == ("results", "fresh_runs"):
        return saved_path

    legacy_path = cwd / legacy_filename
    if saved_resolved != legacy_path.resolve():
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(saved_path, legacy_path)
    return legacy_path


def _parse_constraint_list(raw_value) -> List[str]:
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    if raw_value is None:
        return []
    text = str(raw_value).strip()
    if not text or text.lower() == "nan":
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _canonical_makespan_hours(df: pd.DataFrame) -> float:
    work = df.copy()
    if "is_metric_anchor_row" in work.columns:
        work = work[work["is_metric_anchor_row"].fillna(1).astype(int) == 1]
    start_col = "panel_start_time" if "panel_start_time" in work.columns else "start_time"
    end_col = "final_end_time" if "final_end_time" in work.columns else "end_time"
    starts = pd.to_datetime(work[start_col], errors="coerce")
    ends = pd.to_datetime(work[end_col], errors="coerce")
    if starts.notna().any() and ends.notna().any():
        return round((ends.max() - starts.min()).total_seconds() / 3600.0, 2)

    if "canonical_makespan_hours" in work.columns:
        values = pd.to_numeric(work["canonical_makespan_hours"], errors="coerce")
        if values.notna().any():
            return round(float(values.max()), 2)

    if "makespan_hours" in work.columns:
        values = pd.to_numeric(work["makespan_hours"], errors="coerce")
        if values.notna().any():
            return round(float(values.max()), 2)

    return 0.0


def summarize_result_csv(csv_path: Path, *, mode: str) -> Dict[str, object]:
    """[AGENT-ADD] Build a canonical summary directly from a saved result CSV."""
    df = pd.read_csv(csv_path)
    anchors = int(df["is_metric_anchor_row"].fillna(1).astype(int).sum()) if "is_metric_anchor_row" in df.columns else len(df)
    primary = int(df["violations_primary_count"].sum()) if "violations_primary_count" in df.columns else int(df["violations"].sum())
    raw = int(df["violations_raw_count"].sum()) if "violations_raw_count" in df.columns else primary
    meta = int(df["violations_meta_count"].sum()) if "violations_meta_count" in df.columns else 0
    info = int(df["violations_info_count"].sum()) if "violations_info_count" in df.columns else 0

    counter: Counter = Counter()
    if "constraint_ids" in df.columns:
        for raw_ids in df["constraint_ids"].fillna("[]"):
            for item in _parse_constraint_list(raw_ids):
                counter[item] += 1

    return {
        "mode": mode,
        "csv": csv_path.name,
        "csv_path": str(csv_path),
        "rows": int(len(df)),
        "anchor_rows": anchors,
        "canonical_makespan_hours": _canonical_makespan_hours(df),
        "primary_count": primary,
        "raw_count": raw,
        "meta_count": meta,
        "info_count": info,
        "top_primary_constraints": ", ".join(f"{key}:{value}" for key, value in counter.most_common(5)),
    }


def save_comparison_report(
    *,
    summaries: Iterable[Dict[str, object]],
    output_dir: str,
    run_tag: Optional[str] = None,
    filename_prefix: str = "fresh_run_comparison",
) -> Dict[str, Path]:
    """[AGENT-ADD] Save comparison artifacts as CSV and Markdown."""
    base_dir = Path(output_dir).expanduser()
    if not base_dir.is_absolute():
        base_dir = Path.cwd() / base_dir
    tag = sanitize_run_tag(run_tag, default_prefix=filename_prefix)
    target_dir = base_dir / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(list(summaries))
    csv_path = target_dir / f"{filename_prefix}.csv"
    md_path = target_dir / f"{filename_prefix}.md"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    headers = list(df.columns)
    safe_df = df.astype(str).replace({"nan": "", "None": ""})
    rows = [[str(value) for value in row] for row in safe_df.values.tolist()]
    md_table_lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        md_table_lines.append("| " + " | ".join(row) + " |")

    md_lines = [
        "# Fresh Run Comparison",
        "",
        *md_table_lines,
        "",
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"csv": csv_path, "md": md_path}
